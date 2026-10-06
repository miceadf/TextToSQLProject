"""Phase 8-24: value dictionary entries imported from a code table."""

from __future__ import annotations

from typing import Any

import pytest

from backend.adaptive.catalog.postgres import PostgresConnectionConfig
from backend.adaptive.harness import postgres_values
from backend.adaptive.harness.postgres_values import OVER_LIMIT, PostgresDistinctValueReader
from tests.adaptive import test_postgres_registration as base
from tests.adaptive import test_legacy_view_partial_mapping as lv
from tests.adaptive.test_postgres_registration import DB_ID, _register, _review_all, client_repo  # noqa: F401
from tests.adaptive.test_value_dictionary_editor import DICTIONARIES, _set, stored_values  # noqa: F401


# --- the reader -------------------------------------------------------------------------------------


class _Cursor:
    def __init__(self, log: list[Any], rows: list[Any]) -> None:
        self.log, self.rows = log, rows

    def execute(self, query: str, params: Any) -> None:
        self.log.append((query, params))

    def fetchall(self) -> list[Any]:
        return self.rows


class _Connection:
    def __init__(self, log: list[Any], rows: list[Any]) -> None:
        self.log, self.rows = log, rows

    def cursor(self) -> _Cursor:
        return _Cursor(self.log, self.rows)

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def reader(rows: list[Any], log: list[Any]) -> PostgresDistinctValueReader:
    config = PostgresConnectionConfig("postgresql://u:p@127.0.0.1/db", schema="school")
    return PostgresDistinctValueReader({"db": config}, connector=lambda _config: _Connection(log, rows))


def test_pairs_read_one_group_of_a_common_code_table() -> None:
    log: list[Any] = []
    pairs = reader([("21", "교양필수"), ("11", "전공필수")], log).pairs("db", "code", "cd", "cd_nm", ("grp", "ISU"), 64)
    assert pairs == [("11", "전공필수"), ("21", "교양필수")]
    [(query, params)] = log
    assert query == (
        'SELECT DISTINCT CAST("cd" AS text), CAST("cd_nm" AS text) FROM "school"."code" '
        'WHERE "cd" IS NOT NULL AND "cd_nm" IS NOT NULL AND CAST("grp" AS text) = %s LIMIT %s'
    )
    assert params == ("ISU", 65)


def test_pairs_are_bounded() -> None:
    rows = [(str(n), f"이름{n}") for n in range(5)]
    assert reader(rows, []).pairs("db", "code", "cd", "nm", None, 4) is OVER_LIMIT
    assert reader(rows, []).pairs("db", "code", "cd", "nm", None, 5000 + 1) is None
    assert reader([("1", None), ("2", "둘")], []).pairs("db", "code", "cd", "nm", None, 64) == [("2", "둘")]


# --- the API ----------------------------------------------------------------------------------------


@pytest.fixture
def pairs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    box: dict[str, Any] = {"value": [("교양", "교양 일반"), ("전공(핵심)", "전공 핵심"), ("99", "없는 코드")], "calls": []}

    def fake(self: Any, database_id: str, table: str, code: str, name: str, group: Any, limit: int) -> Any:
        box["calls"].append((table, code, name, group, limit))
        return box["value"]

    monkeypatch.setattr(postgres_values.PostgresDistinctValueReader, "pairs", fake)
    return box


def body(**overrides: Any) -> dict[str, Any]:
    return {
        "action": "IMPORT", "database_id": DB_ID, "field": "Course.category", "table": lv.VIEW,
        "code_column": "category", "name_column": "subject_name", "group_column": None, "group_value": None,
        "reviewer": "admin", **overrides,
    }


def entries(response: Any, field: str = "Course.category") -> dict[str, dict[str, Any]]:
    fields = {item["field"]: item for item in response.json()["fields"]}
    return {entry["stored"]: entry for entry in fields[field]["entries"]}


def test_codes_the_field_holds_are_imported_and_aliases_stay(client_repo, pairs) -> None:  # noqa: F811
    client, _repo, _app, _executor = client_repo
    assert _register(client).status_code == 200
    _review_all(client)
    assert _set(client, "Course.category", "교양", ["교양과목"]).status_code == 200
    response = client.post(DICTIONARIES, json=body(group_column="category", group_value="ISU"))
    assert response.status_code == 200, response.text[:300]
    assert response.json()["imported"] == {"added": 1, "updated": 1, "skipped": 1}  # 99 is not in the data
    found = entries(response)
    assert found["교양"] == {"stored": "교양", "label": "교양 일반", "aliases": ["교양과목"]}
    assert found["전공(핵심)"]["label"] == "전공 핵심" and "99" not in found
    assert pairs["calls"][0][3] == ("category", "ISU")
    again = client.post(DICTIONARIES, json=body())
    assert again.json()["imported"] == {"added": 0, "updated": 0, "skipped": 1}


@pytest.mark.parametrize(
    ("value", "code", "status"),
    [
        ([("교양", "같은 이름"), ("전공(핵심)", "같은 이름")], "IMPORT_CONFLICT", 409),  # two codes, one spelling
        ([("교양", "가"), ("교양", "나")], "IMPORT_CONFLICT", 409),  # one code, two names
        (OVER_LIMIT, "TOO_MANY_CODES", 400),
        (None, "SOURCE_UNAVAILABLE", 502),
    ],
)
def test_unusable_code_tables_are_refused(client_repo, pairs, value: Any, code: str, status: int) -> None:  # noqa: F811
    client, _repo, _app, _executor = client_repo
    assert _register(client).status_code == 200
    _review_all(client)
    pairs["value"] = value
    response = client.post(DICTIONARIES, json=body())
    assert response.status_code == status and response.json()["error"]["code"] == f"POSTGRES_REGISTRATION_{code}"
    assert entries(client.get(DICTIONARIES, params={"database_id": DB_ID})) == {}


@pytest.mark.parametrize(
    "overrides",
    [
        {"group_column": "category"},  # a group column without its value
        {"group_value": "ISU"},
        {"code_column": "no_such_column"},
        {"table": "no_such_table"},
        {"field": "Course.code"},  # not a dictionary field
        {"reviewer": ""},
        {"extra": 1},
    ],
)
def test_invalid_imports_are_refused(client_repo, pairs, overrides: dict[str, Any]) -> None:  # noqa: F811
    client, _repo, _app, _executor = client_repo
    assert _register(client).status_code == 200
    _review_all(client)
    response = client.post(DICTIONARIES, json=body(**overrides))
    assert response.status_code == 400 and pairs["calls"] == []


def test_codes_reused_by_other_groups_do_not_block_the_import(client_repo, pairs) -> None:  # noqa: F811
    """A common-code table read without a group: a code the field never holds may have other names."""
    client, _repo, _app, _executor = client_repo
    assert _register(client).status_code == 200
    _review_all(client)
    pairs["value"] = [("교양", "교양 일반"), ("1", "월요일"), ("1", "1학기")]
    response = client.post(DICTIONARIES, json=body())
    assert response.status_code == 200, response.text[:200]
    assert response.json()["imported"] == {"added": 1, "updated": 0, "skipped": 1}


def test_an_import_with_no_matching_code_reports_skips_not_a_conflict(client_repo, pairs) -> None:  # noqa: F811
    """Luna 8-24 review: an empty result is "all skipped", not a name collision."""
    client, _repo, _app, _executor = client_repo
    assert _register(client).status_code == 200
    _review_all(client)
    pairs["value"] = [("99", "없는 코드"), ("98", "또 없는 코드")]
    response = client.post(DICTIONARIES, json=body())
    assert response.status_code == 200, response.text[:200]
    assert response.json()["imported"] == {"added": 0, "updated": 0, "skipped": 2}
