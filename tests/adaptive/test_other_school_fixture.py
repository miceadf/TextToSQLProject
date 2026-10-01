"""Phase 8-17: the invented second school and its fixed questions."""

from __future__ import annotations

import subprocess
import sys

from backend.adaptive.fixtures import other_school as school
from backend.adaptive.ontology import load_default_ontology


def test_rows_are_deterministic_and_consistent():
    rows = school.school_rows()
    assert rows == school.school_rows()
    assert {table: len(values) for table, values in rows.items()} == {
        "isu_gubun": 5, "hakgwa": 11, "gyosu": 33, "gyogwamok": 80, "gangjwa": 102, "siganpyo": 143,
    }
    departments = {row[0] for row in rows["hakgwa"]}
    professors = {row[0] for row in rows["gyosu"]}
    courses = {row[0] for row in rows["gyogwamok"]}
    offerings = {row[0] for row in rows["gangjwa"]}
    codes = {row[0] for row in rows["isu_gubun"]}
    assert all(row[2] in departments for row in rows["gyosu"])
    assert all(row[2] in departments and row[3] in codes for row in rows["gyogwamok"])
    assert all(row[1] in courses and row[2] in professors for row in rows["gangjwa"])
    assert all(row[1] in offerings for row in rows["siganpyo"])
    for column, table in ((0, "gyogwamok"), (0, "gangjwa"), (0, "siganpyo")):
        assert len({row[column] for row in rows[table]}) == len(rows[table])


def test_values_are_stored_the_other_school_way():
    rows = school.school_rows()
    assert {row[2] for row in rows["siganpyo"]} <= {"MON", "TUE", "WED", "THU", "FRI"}
    assert all(len(row[3]) == 4 and row[3].isdigit() and row[3] < row[4] for row in rows["siganpyo"])
    assert {row[4] for row in rows["gangjwa"]} == {"10", "20"}
    assert all(offering[7] <= offering[6] for offering in rows["gangjwa"])  # enrolled <= capacity
    # Several offerings meet twice a week: more than one timetable row.
    assert len(rows["siganpyo"]) > len(rows["gangjwa"])


def test_physical_names_are_outside_the_ontology_hints():
    hints = {
        hint.lower()
        for field in load_default_ontology().fields.values()
        for hint in (getattr(field, "physical_name_hints", None) or ())
    }
    names = set(school.TABLE_COLUMNS) | {column for columns in school.TABLE_COLUMNS.values() for column in columns}
    assert not names & hints


def test_every_question_has_a_small_nonempty_gold_or_a_decline():
    joined = school.joined_rows()
    assert [question.qid for question in school.QUESTIONS] == [f"q{n:02d}" for n in range(1, 15)]
    for question in school.QUESTIONS:
        expected = school.gold(question)
        if question.predicate is None:
            assert expected is None
            continue
        matching = [row for row in joined if question.predicate(row)]
        assert expected and 1 <= len(matching) < 200, question.qid


def test_grade_compares_course_sets_and_declines():
    first = school.QUESTIONS[0]
    decline = school.Question("qx", "강의실 정보 보여줘", None, "a decline")
    expected = sorted(school.gold(first))
    rows = [{"course_code": code} for code in expected + expected[:1]]  # duplicates are fine
    assert school.grade(first, {"status": "SUCCEEDED", "rows": rows})[0] is True
    assert school.grade(first, {"status": "SUCCEEDED", "rows": rows[1:len(expected)]})[0] is False
    assert school.grade(first, {"status": "SUCCEEDED", "rows": rows + [{"course_code": "X"}]})[0] is False
    assert school.grade(first, {"status": "NOT_UNDERSTOOD"})[0] is False
    assert school.grade(decline, {"status": "UNSUPPORTED"})[0] is True
    assert school.grade(decline, {"status": "NOT_UNDERSTOOD"})[0] is True
    assert school.grade(decline, {"status": "SUCCEEDED", "rows": []})[0] is False


def test_cli_refuses_other_databases_without_printing_the_dsn(capsys):
    assert school.main([]) == 2
    secret = "postgresql://user:hunter2@db.example.com:5432/postgres"
    assert school.main(["create"], environ={school.DSN_ENV: secret}) == 1
    local_other = "postgresql://user:hunter2@127.0.0.1:5432/postgres"
    assert school.main(["drop"], environ={school.DSN_ENV: local_other}) == 1
    assert school.main(["check"], environ={}) == 1
    out = capsys.readouterr().out
    assert "hunter2" not in out and "example.com" not in out


def test_statements_touch_only_the_fixed_schema():
    class FakeSql:
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

    statements = school._statements(FakeSql)
    drops = [statement.text for statement, _params in school._drop_statements(FakeSql)]
    # Children first, then the schema; CASCADE could reach objects in other schemas.
    assert drops == [f'DROP TABLE IF EXISTS "other_school"."{table}"' for table in reversed(school.TABLE_COLUMNS)] + [
        'DROP SCHEMA IF EXISTS "other_school"'
    ]
    assert all("CASCADE" not in statement.text for statement, _params in statements)
    for statement, _params in statements[len(drops) + 1:]:
        assert '"other_school".' in statement.text
    assert sum(1 for _s, params in statements if params) == sum(len(v) for v in school.school_rows().values())


def test_import_does_not_load_a_driver():
    code = "import sys, backend.adaptive.fixtures.other_school; print('psycopg2' in sys.modules)"
    output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert output.strip() == "False"
