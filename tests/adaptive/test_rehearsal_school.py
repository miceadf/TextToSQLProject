"""Phase 9-04: the rehearsal school's rows (no database)."""

from __future__ import annotations

import re

from backend.adaptive.fixtures import other_school
from backend.adaptive.fixtures import rehearsal_school as school


def test_rows_use_the_new_storage_shapes() -> None:
    rows = school.school_rows()
    for _id, lec_no, _title, _dept, type_cd, credit_txt, term, *_rest in rows["lecture"]:
        assert re.fullmatch(r"[A-Z]{2}\d{4}-\d{2}", lec_no)
        assert re.fullmatch(r"\d-\d-\d", credit_txt)
        assert re.fullmatch(r"2026-[12]", term)
        assert type_cd in school.TYPE_NAMES
    for _lt, _lecture, dow, time_range, _room in rows["lecture_time"]:
        assert dow in "월화수목금"
        assert re.fullmatch(r"\d\d:\d\d-\d\d:\d\d", time_range)


def test_the_content_is_other_schools_and_deterministic() -> None:
    source = other_school.school_rows()
    rows = school.school_rows()
    assert rows == school.school_rows()
    assert len(rows["lecture"]) == len(source["gangjwa"])
    assert len(rows["lecture_time"]) == len(source["siganpyo"])
    assert [row[0] for row in rows["dept"]] == [row[0] for row in source["hakgwa"]]
    joined = school.joined_rows()
    original = other_school.joined_rows()
    assert len(joined) == len(original)
    for mine, theirs in zip(joined, original):
        assert mine["lec_no"].split("-")[0] == theirs["course_code"]
        assert mine["credits"] == theirs["credits"]
        assert mine["start"].replace(":", "") == theirs["start"]
        assert mine["end"].replace(":", "") == theirs["end"]
        assert school.TYPE_CODES[theirs["category"]] == mine["type_cd"]
        # Luna 9-04 review: every converted column, not just a few
        assert mine["lec_no"] == f"{theirs['course_code']}-{theirs['section'][-2:]}"
        assert mine["term"] == f"{theirs['year']}-{school.SEMESTERS[theirs['semester']]}"
        assert mine["dow"] == school.WEEKDAYS[theirs["day"]]
        for key, other in (("title", "course_name"), ("department", "department"), ("professor", "instructor"),
                           ("capacity", "capacity"), ("enrolled", "enrolled"), ("grade", "grade"), ("room", "room")):
            assert mine[key] == theirs[other], key


def test_the_cli_refuses_bad_usage_and_non_synthetic_databases(capsys) -> None:
    assert school.main(["wipe"], {}) == 2
    dsn = "postgresql://u:secret@10.0.0.5:5432/schema_adaptive_synthetic"
    assert school.main(["create"], {school.DSN_ENV: dsn}) == 1
    other_database = "postgresql://u:secret@127.0.0.1:5432/postgres"  # loopback, but not the synthetic DB
    assert school.main(["create"], {school.DSN_ENV: other_database}) == 1
    assert "secret" not in capsys.readouterr().out


class FakeSql:
    """Stands in for ``psycopg2.sql`` so no driver is imported (import-isolation tests share sys.modules)."""

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
    assert drops == [f'DROP TABLE IF EXISTS "rehearsal_school"."{table}"' for table in reversed(school.TABLE_COLUMNS)] + [
        'DROP SCHEMA IF EXISTS "rehearsal_school"'
    ]
    assert all("CASCADE" not in statement.text for statement, _params in statements)
    ddl = [statement.text for statement, params in statements if statement.text.startswith("CREATE TABLE")]
    assert 'REFERENCES "rehearsal_school"."dept" (dept_cd)' in ddl[1]
    assert 'REFERENCES "rehearsal_school"."lecture" (lecture_id)' in ddl[2]
    for statement, _params in statements[len(drops) + 1:]:
        assert '"rehearsal_school".' in statement.text
    inserts = [(statement.text, params) for statement, params in statements if params]
    assert len(inserts) == sum(len(rows) for rows in school.school_rows().values())
    assert all(len(params) == text.count("%s") for text, params in inserts)


def test_import_does_not_load_a_driver() -> None:
    import subprocess
    import sys

    code = "import sys, backend.adaptive.fixtures.rehearsal_school; print('psycopg2' in sys.modules)"
    output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert output.strip() == "False"
