"""Phase 9-29: Hanbit University's rows and fixture CLI (no database)."""

from __future__ import annotations

from collections import Counter
import re
import subprocess
import sys
import types

from backend.adaptive.fixtures import hanbit_school as school


def test_rows_are_deterministic_and_in_range() -> None:
    rows = school.school_rows()
    assert rows == school.school_rows()
    assert [len(rows[name]) for name in list(school.TABLE_COLUMNS)[:3]] == [4, 8, 32]
    assert 55 <= len(rows["subject"]) <= 65
    assert 110 <= len(rows["open_class"]) <= 130


def test_foreign_keys_and_team_teaching() -> None:
    rows = school.school_rows()
    college_ids = {row[0] for row in rows["college"]}
    dept_ids = {row[0] for row in rows["department"]}
    subject_codes = {row[0] for row in rows["subject"]}
    class_ids = {row[0] for row in rows["open_class"]}
    prof_ids = {row[0] for row in rows["professor"]}
    assert all(row[3] in college_ids for row in rows["department"])
    assert all(row[2] in dept_ids for row in rows["professor"])
    assert all(row[2] in dept_ids for row in rows["subject"])
    assert all(row[1] in subject_codes for row in rows["open_class"])
    assert all(row[0] in class_ids and row[1] in prof_ids for row in rows["class_professor"])
    assert all(row[1] in class_ids for row in rows["class_time"])
    keys = [(row[3], row[4], row[1], row[2]) for row in rows["open_class"]]
    assert len(keys) == len(set(keys))
    assignments = Counter(row[0] for row in rows["class_professor"] if row[2] == "주담당")
    assert set(assignments) == class_ids and set(assignments.values()) == {1}
    assert sum(row[2] == "공동" for row in rows["class_professor"]) >= 10
    pairs = [(row[0], row[1]) for row in rows["class_professor"]]
    assert len(pairs) == len(set(pairs))


def test_weekdays_terms_and_online_schedule_shapes() -> None:
    rows = school.school_rows()
    classes = {row[0]: row for row in rows["open_class"]}
    by_class = Counter(row[1] for row in rows["class_time"])
    assert {row[4] for row in rows["open_class"]} == {"1", "2", "3", "4"}
    assert all(1 <= row[2] <= 7 and row[3] < row[4] for row in rows["class_time"])
    assert any(row[2] == 6 for row in rows["class_time"])
    for class_id, row in classes.items():
        if row[7] == "온라인":
            assert by_class[class_id] == 0
        else:
            assert by_class[class_id] >= 1
        if row[4] in ("3", "4"):
            assert by_class[class_id] == 5


def test_catalog_rows_agree_with_source_rows() -> None:
    rows = school.school_rows()
    catalog = school.catalog_rows()
    assert len(catalog) == len(rows["open_class"])
    subjects = {row[0]: row for row in rows["subject"]}
    departments = {row[0]: row for row in rows["department"]}
    professors = {row[0]: row[1] for row in rows["professor"]}
    assignments: dict[int, list[tuple[str, str]]] = {}
    for class_id, prof_id, role in rows["class_professor"]:
        assignments.setdefault(class_id, []).append((professors[prof_id], role))
    times: dict[int, list[tuple[int, str, str, str]]] = {}
    for _id, class_id, day, start, end, room in rows["class_time"]:
        times.setdefault(class_id, []).append((day, start.strftime("%H:%M"), end.strftime("%H:%M"), room))
    for record, raw in zip(catalog, rows["open_class"]):
        class_id, code, number, _year, term, cap, enrolled, mode, english = raw
        subj = subjects[code]
        dept = departments[subj[2]]
        assert record["subject_code"] == code and record["subject_name"] == subj[1]
        assert record["dept_code"] == dept[1] and record["dept_name"] == dept[2]
        assert record["credit"] == subj[3] and record["completion_type"] == subj[4]
        assert record["target_grade"] == subj[5] and record["term_code"] == term
        assert record["class_no"] == number and record["capacity"] == cap and record["enrolled"] == enrolled
        assert record["delivery_mode"] == mode and record["english_yn"] == english
        assert record["professors"] == sorted(assignments[class_id], key=lambda pair: pair[1] != "주담당")
        assert record["times"] == times.get(class_id, [])


def test_results_document_online_class_facts() -> None:
    catalog = school.catalog_rows()
    online = [record for record in catalog if record["delivery_mode"] == "온라인"]
    online_subjects = {record["subject_code"] for record in online}
    subjects = {record["subject_code"] for record in catalog}
    subjects_with_non_online_classes = {
        record["subject_code"] for record in catalog if record["delivery_mode"] != "온라인"
    }
    assert len(online) == 8
    assert online_subjects - subjects_with_non_online_classes == {"DSC2006", "GEN4008", "PSY2006"}


def test_cli_refuses_bad_usage_and_non_synthetic_databases(capsys) -> None:
    assert school.main(["wipe"], {}) == 2
    dsn = "postgresql://u:secret@10.0.0.5:5432/schema_adaptive_synthetic"
    assert school.main(["create"], {school.DSN_ENV: dsn}) == 1
    other_database = "postgresql://u:secret@127.0.0.1:5432/postgres"
    assert school.main(["create"], {school.DSN_ENV: other_database}) == 1
    assert "secret" not in capsys.readouterr().out


class FakeSql:
    """Stands in for ``psycopg2.sql`` so no driver is imported."""

    class SQL:
        def __init__(self, text):
            self.text = text

        def format(self, *args, **kwargs):
            text = self.text
            for arg in args:
                text = text.replace("{}", arg.text, 1)
            for key, value in kwargs.items():
                text = text.replace("{" + key + "}", value.text)
            return FakeSql.SQL(text)

        def join(self, parts):
            return FakeSql.SQL(self.text.join(part.text for part in parts))

    class Identifier:
        def __init__(self, name):
            self.text = f'"{name}"'

    class Placeholder:
        text = "%s"


def test_statements_touch_only_the_fixed_schema() -> None:
    statements = school._statements(FakeSql)
    drops = [statement.text for statement, _params in school._drop_statements(FakeSql)]
    assert drops == [f'DROP TABLE IF EXISTS "{school.SCHEMA}"."{table}"' for table in reversed(school.TABLE_COLUMNS)] + [
        f'DROP SCHEMA IF EXISTS "{school.SCHEMA}"'
    ]
    assert all("cascade" not in statement.text.lower() for statement, _params in statements)
    assert [statement.text for statement, _params in statements[:len(drops) + 1]] == [
        *drops, f'CREATE SCHEMA "{school.SCHEMA}"'
    ]
    assert all(f'"{school.SCHEMA}".' in statement.text for statement, _params in statements[len(drops) + 1:])
    inserts = [(statement.text, params) for statement, params in statements if params]
    assert len(inserts) == sum(len(table_rows) for table_rows in school.school_rows().values())
    assert all(len(params) == text.count("%s") for text, params in inserts)


def test_cli_drop_and_check_success_without_printing_dsn(monkeypatch, capsys) -> None:
    counts = iter(len(rows) for rows in school.school_rows().values())

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            pass

        def fetchone(self):
            return (next(counts),)

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("backend.adaptive.fixtures.other_school._connect", lambda _environ: FakeConnection())
    monkeypatch.setitem(sys.modules, "psycopg2", types.ModuleType("psycopg2"))
    sys.modules["psycopg2"].sql = FakeSql
    environ = {school.DSN_ENV: "postgresql://u:secret@127.0.0.1:5432/schema_adaptive_synthetic"}

    assert school.main(["drop"], environ) == 0
    assert school.main(["check"], environ) == 0
    assert "secret" not in capsys.readouterr().out


def test_import_does_not_load_a_driver() -> None:
    code = "import sys, backend.adaptive.fixtures.hanbit_school; print('psycopg2' in sys.modules)"
    output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert output.strip() == "False"
