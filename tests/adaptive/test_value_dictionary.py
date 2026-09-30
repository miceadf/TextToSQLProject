"""Phase 8-06 reviewed value dictionaries."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.adaptive.api import legacy_view_profile as lvp
from backend.adaptive.api.chat_query import answer_chat_query
from backend.adaptive.api.postgres_runtime import PostgresRuntimeError, PostgresRuntimeProfile
from backend.adaptive.api.postgres_startup_profile import (
    PostgresStartupProfileError,
    load_postgres_startup_profiles,
)
from backend.adaptive.catalog.postgres import PostgresConnectionConfig
from backend.adaptive.harness.contracts import CompiledQuery, HarnessErrorCode
from backend.adaptive.harness.errors import HarnessFailure
from backend.adaptive.mapping.transforms import FieldTransform
from backend.adaptive.mapping.values import (
    ValueDictionary,
    ValueEntry,
    bind_filter_value,
    lookup_key,
)
from tests.adaptive.test_anchor_left_join import (
    ANCHOR,
    DATABASE_ID,
    FINGERPRINT,
    SECRET_DSN,
    TABLES,
    FakeExecutor,
    _write_pair,
    make_catalog,
    make_context,
    make_published,
    query_ir,
    repository,  # noqa: F401 - pytest fixture
)

CODED = ValueDictionary(
    entries=(
        ValueEntry(stored="01", label="전공", aliases=("전공과목",)),
        ValueEntry(stored="02", label="교양", aliases=("교양과목",)),
    ),
    unknown="reject",
    reviewer="synthetic-test",
    reason="coded category (synthetic)",
)


def make_profile(dictionaries: dict[str, Any] | None = None, **overrides: Any) -> PostgresRuntimeProfile:
    kwargs: dict[str, Any] = {
        "database_id": DATABASE_ID,
        "label": lvp.BASE_LABEL,
        "connection_config": PostgresConnectionConfig(SECRET_DSN),
        "published_mapping": make_published(),
        "field_transforms": dict(lvp.FIELD_TRANSFORMS),
        "unsupported_fields": lvp.UNSUPPORTED_FIELDS,
        "anchor_table": ANCHOR,
        "value_dictionaries": dict(lvp.VALUE_DICTIONARIES) if dictionaries is None else dictionaries,
    }
    kwargs.update(overrides)
    return PostgresRuntimeProfile(**kwargs)


def category(operator: str, value: Any) -> Any:
    return query_ir(["Course.name"], [{"field": "Course.category", "operator": operator, "value": value}])


def compile_only(context: Any, ir: Any) -> CompiledQuery:
    return context.compiler().compile(ir, context.harness_mapping())


def validate(context: Any, ir: Any, compiled: CompiledQuery) -> None:
    context.policy().validate(compiled, query_ir=ir, mapping=context.harness_mapping())


# --- dictionary model ----------------------------------------------------------


def test_lookup_key_normalizes_width_case_and_spaces() -> None:
    assert lookup_key(" 전공 핵심 ") == "전공핵심"
    assert lookup_key("전공（핵심）") == lookup_key("전공(핵심)")
    assert lookup_key("Major\tCore") == "majorcore"


@pytest.mark.parametrize(
    "entries",
    [
        # one spelling leading to two stored values
        [{"stored": "A", "label": "가"}, {"stored": "B", "label": "나", "aliases": ["가"]}],
        # spelling collision after normalization
        [{"stored": "A", "label": "전공 핵심"}, {"stored": "B", "label": "전공핵심"}],
        [{"stored": "A", "label": "가"}, {"stored": "A", "label": "나"}],
        [{"stored": "A", "label": "가", "aliases": ["  "]}],
        [{"stored": "A\u0000", "label": "가"}],
        [{"stored": "A", "label": "가", "extra": 1}],
        [],
        [{"stored": str(index), "label": f"L{index}"} for index in range(65)],
    ],
)
def test_invalid_dictionaries_are_rejected(entries: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        ValueDictionary.model_validate(
            {"entries": entries, "unknown": "pass", "reviewer": "r", "reason": "x"}
        )


def test_resolve_and_label_in_both_modes() -> None:
    legacy = lvp.VALUE_DICTIONARIES["Course.category"]
    assert legacy.resolve("전공핵심") == "전공(핵심)"
    assert legacy.resolve("핵심 전공") == "전공(핵심)"
    assert legacy.resolve("전공(핵심)") == "전공(핵심)"
    # whole values only; unknown values pass through unchanged
    assert legacy.resolve("전공") == "전공"
    assert legacy.resolve("전공핵심 과목") == "전공핵심 과목"
    assert CODED.resolve("전공과목") == "01" and CODED.resolve("교양") == "02"
    assert CODED.resolve("01") == "01"
    with pytest.raises(ValueError):
        CODED.resolve("일반선택")
    assert CODED.label_for("01") == "전공" and CODED.label_for("99") == "99"
    assert CODED.label_for(None) is None


def test_bind_prefers_the_declared_kind() -> None:
    kwargs = {
        "field_transforms": {"Schedule.day": FieldTransform.KOREAN_WEEKDAY},
        "value_dictionaries": {"Course.category": CODED},
    }
    assert bind_filter_value("Schedule.day", "월요일", **kwargs) == "월"
    assert bind_filter_value("Course.category", "전공", **kwargs) == "01"
    assert bind_filter_value("Course.name", "전공", **kwargs) == "전공"


def test_legacy_dictionary_covers_the_stored_categories() -> None:
    legacy = lvp.VALUE_DICTIONARIES["Course.category"]
    assert {entry.stored for entry in legacy.entries} == {
        "전공(핵심)", "전공(기초)", "전공(심화)", "교양(소양)", "교양(균형)",
        "교양(소양필수)", "교양(기초)", "일반선택", "교직",
    }
    assert legacy.unknown == "pass"
    assert all(entry.label == entry.stored for entry in legacy.entries)


# --- compiler and AST policy ---------------------------------------------------


@pytest.mark.parametrize(
    ("operator", "raw", "bound"),
    [
        ("equals", "전공핵심", "전공(핵심)"),
        ("contains", "전공 핵심", "%전공(핵심)%"),
        ("not_equals", "교양기초", "교양(기초)"),
        ("contains", "전공", "%전공%"),
    ],
)
def test_compiler_and_policy_bind_the_stored_value(operator: str, raw: str, bound: str) -> None:
    context = make_context(make_profile())
    ir = category(operator, raw)
    compiled = compile_only(context, ir)
    assert compiled.parameters == [bound, 50]
    validate(context, ir, compiled)
    if raw.replace(" ", "") != bound.strip("%"):
        # the policy expects the dictionary value, not the model's spelling
        literal = f"%{raw}%" if operator == "contains" else raw
        with pytest.raises(HarnessFailure):
            validate(context, ir, compiled.model_copy(update={"parameters": [literal, 50]}))


def test_rejecting_dictionary_fails_compile_for_unknown_values() -> None:
    context = make_context(make_profile({"Course.category": CODED}))
    with pytest.raises(HarnessFailure) as caught:
        compile_only(context, category("equals", "일반선택"))
    assert caught.value.code is HarnessErrorCode.INVALID_VALUE_TYPE
    assert compile_only(context, category("equals", "전공")).parameters == ["01", 50]


def test_without_dictionaries_values_are_unchanged() -> None:
    context = make_context(make_profile({}))
    assert compile_only(context, category("equals", "전공핵심")).parameters == ["전공핵심", 50]


# --- runtime --------------------------------------------------------------------


def test_rows_show_labels_for_stored_codes() -> None:
    context = make_context(make_profile({"Course.category": CODED}))
    rows = [{"Course.category": "01", "Course.name": "01"}, {"Course.category": "99", "Course.name": "x"}]
    assert context.normalize_rows(["Course.category", "Course.name"], rows) == [
        {"Course.category": "전공", "Course.name": "01"},
        {"Course.category": "99", "Course.name": "x"},
    ]


def test_profile_and_context_copy_dictionaries() -> None:
    profile = make_profile({"Course.category": CODED})
    assert profile.value_dictionaries == {"Course.category": CODED}
    profile.value_dictionaries.clear()
    assert profile.value_dictionaries == {"Course.category": CODED}
    assert make_context(profile).value_dictionaries() == {"Course.category": CODED}


@pytest.mark.parametrize(
    "dictionaries",
    [
        {"CourseOffering.year": CODED},  # unsupported (unmapped) field
        {"Course.credits": CODED},  # field with a value transform
        {"CourseOffering.capacity": CODED},  # integer ontology field
        {"Course.nickname": CODED},  # unknown field
    ],
)
def test_runtime_rejects_misplaced_dictionaries(dictionaries: dict[str, Any]) -> None:
    with pytest.raises(PostgresRuntimeError):
        make_context(make_profile(dictionaries))


def test_runtime_rejects_a_dictionary_on_a_non_text_column() -> None:
    tables = {name: dict(item) for name, item in TABLES.items()}
    tables["subject"] = {**TABLES["subject"], "columns": {**TABLES["subject"]["columns"], "category": "integer"}}
    with pytest.raises(PostgresRuntimeError):
        make_context(make_profile({"Course.category": CODED}), catalog=make_catalog(tables))


def test_runtime_profile_rejects_invalid_dictionary_objects() -> None:
    with pytest.raises(PostgresRuntimeError):
        make_profile({"Course.category": {"entries": []}})
    with pytest.raises(PostgresRuntimeError):
        make_profile(["Course.category"])  # type: ignore[arg-type]


# --- chat endpoint ----------------------------------------------------------------


class _Model:
    def __init__(self, filters: list[dict[str, Any]]) -> None:
        self.filters = filters

    def generate_ir(self, *_args: Any) -> dict[str, Any]:
        return {"entity": "Course", "select": ["Course.name"], "filters": self.filters, "limit": 50}


class _RowsExecutor(FakeExecutor):
    def execute(self, database_id: str, compiled: CompiledQuery) -> tuple[list[str], list[dict[str, Any]]]:
        self.queries.append(compiled)
        row = {field: None for field in compiled.expected_columns}
        row.update({"Course.code": "0001", "CourseOffering.section": "A1", "Course.category": "01"})
        return list(compiled.expected_columns), [row]


def test_chat_binds_codes_and_shows_labels() -> None:
    executor = _RowsExecutor()
    context = make_context(make_profile({"Course.category": CODED}), executor=executor)
    answer = answer_chat_query(
        database_id=DATABASE_ID,
        question="전공 과목",
        pg_contexts={DATABASE_ID: context},
        model=_Model([{"field": "Course.category", "operator": "equals", "value": "전공과목"}]),
    )
    assert answer["status"] == "SUCCEEDED"
    [compiled] = executor.queries
    # a recognised value binds its stored code exactly: no contains relaxation
    assert compiled.parameters == ["01", 200] and " LIKE " not in compiled.sql
    [understood] = answer["understood"]["filters"]
    assert understood["operator"] == "equals" and understood["value"] == "전공"
    [row] = answer["rows"]
    assert "전공" in row.values() and "01" not in row.values()


def test_chat_still_relaxes_values_the_dictionary_does_not_know() -> None:
    executor = FakeExecutor()
    context = make_context(make_profile(), executor=executor)
    answer = answer_chat_query(
        database_id=DATABASE_ID,
        question="전공 과목",
        pg_contexts={DATABASE_ID: context},
        model=_Model([
            {"field": "Course.category", "operator": "equals", "value": "전공"},
            {"field": "Course.category", "operator": "equals", "value": "전공핵심"},
        ]),
    )
    assert answer["status"] == "SUCCEEDED"
    assert executor.queries[0].parameters == ["%전공%", "전공(핵심)", 200]
    assert [item["operator"] for item in answer["understood"]["filters"]] == ["contains", "equals"]


def test_chat_does_not_understand_values_outside_a_rejecting_dictionary() -> None:
    executor = _RowsExecutor()
    context = make_context(make_profile({"Course.category": CODED}), executor=executor)
    answer = answer_chat_query(
        database_id=DATABASE_ID,
        question="일반선택 과목",
        pg_contexts={DATABASE_ID: context},
        model=_Model([{"field": "Course.category", "operator": "equals", "value": "일반선택"}]),
    )
    assert answer == {
        "version": "adaptive-chat-query-v1",
        "database_id": DATABASE_ID,
        "status": "NOT_UNDERSTOOD",
        "failure_code": "INVALID_VALUE_TYPE",
    }
    assert executor.queries == []


def test_chat_on_the_legacy_dictionary_binds_the_parenthesized_value() -> None:
    executor = FakeExecutor()
    context = make_context(make_profile(), executor=executor)
    answer = answer_chat_query(
        database_id=DATABASE_ID,
        question="전공핵심 과목 중 화요일 수업",
        pg_contexts={DATABASE_ID: context},
        model=_Model([
            {"field": "Course.category", "operator": "contains", "value": "전공핵심"},
            {"field": "Schedule.day", "operator": "equals", "value": "화"},
        ]),
    )
    assert answer["status"] == "SUCCEEDED"
    assert executor.queries[0].parameters == ["%전공(핵심)%", "화", 200]
    assert [item["value"] for item in answer["understood"]["filters"]] == ["전공(핵심)", "화"]


# --- private profile v4 ------------------------------------------------------------


def test_v4_profiles_round_trip(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    profile_bytes, mapping_bytes = lvp.build_documents(
        FINGERPRINT, datetime(2026, 1, 1, tzinfo=timezone.utc), lvp.BASE_SPEC
    )
    document = json.loads(profile_bytes)
    assert document["version"] == "postgres-runtime-profile-v4"
    _write_pair(repository, document, mapping_bytes)
    monkeypatch.setenv(lvp.BASE_DSN_ENV, SECRET_DSN)
    [loaded] = load_postgres_startup_profiles(repository_root=repository, database_ids=[DATABASE_ID])
    assert loaded.anchor_table == ANCHOR
    assert loaded.value_dictionaries == lvp.VALUE_DICTIONARIES
    context = make_context(loaded)
    assert compile_only(context, category("equals", "전공핵심")).parameters == ["전공(핵심)", 50]


def _dictionary_document() -> dict[str, Any]:
    return lvp.VALUE_DICTIONARIES["Course.category"].model_dump(mode="json")


@pytest.mark.parametrize(
    "change",
    [
        {"value_dictionaries": {"Course.category": {**_dictionary_document(), "extra": 1}}},
        # a default left out is not the canonical spelling
        {"value_dictionaries": {"Course.category": {
            key: value for key, value in _dictionary_document().items() if key != "unknown"
        }}},
        {"value_dictionaries": {"course.category": _dictionary_document()}},
        {"value_dictionaries": {"Course.credits": _dictionary_document()}},  # transformed field
        {"value_dictionaries": {"Department.code": _dictionary_document()}},  # unsupported field
        {"value_dictionaries": []},
        {"anchor_table": "Bad-Name"},
    ],
)
def test_v4_profile_is_strict(
    repository: Path, monkeypatch: pytest.MonkeyPatch, change: dict[str, Any]  # noqa: F811
) -> None:
    profile_bytes, mapping_bytes = lvp.build_documents(
        FINGERPRINT, datetime(2026, 1, 1, tzinfo=timezone.utc), lvp.BASE_SPEC
    )
    _write_pair(repository, {**json.loads(profile_bytes), **change}, mapping_bytes)
    monkeypatch.setenv(lvp.BASE_DSN_ENV, SECRET_DSN)
    with pytest.raises(PostgresStartupProfileError) as caught:
        load_postgres_startup_profiles(repository_root=repository, database_ids=[DATABASE_ID])
    assert "s3cret" not in str(caught.value)


def test_v4_requires_both_new_keys(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    profile_bytes, mapping_bytes = lvp.build_documents(
        FINGERPRINT, datetime(2026, 1, 1, tzinfo=timezone.utc), lvp.BASE_SPEC
    )
    monkeypatch.setenv(lvp.BASE_DSN_ENV, SECRET_DSN)
    for key in ("anchor_table", "value_dictionaries"):
        document = json.loads(profile_bytes)
        del document[key]
        _write_pair(repository, document, mapping_bytes)
        with pytest.raises(PostgresStartupProfileError):
            load_postgres_startup_profiles(repository_root=repository, database_ids=[DATABASE_ID])


# --- registration ------------------------------------------------------------------


def test_registration_requires_every_stored_value(repository: Path) -> None:  # noqa: F811
    # Phase 8-13 added a department dictionary; every dictionary is checked.
    stored = {
        entry.stored for dictionary in lvp.VALUE_DICTIONARIES.values() for entry in dictionary.entries
    }
    asked: list[tuple[str, str]] = []

    def missing_one(table: str, column: str) -> set[str]:
        asked.append((table, column))
        return stored - {"교직"}

    with pytest.raises(lvp.LegacyViewProfileError) as caught:
        lvp.write_profile(
            repository, make_catalog(), replace=False, spec=lvp.BASE_SPEC, distinct_values=missing_one
        )
    assert caught.value.code == "VALUE_MISSING"
    assert asked == [("subject", "category")]
    assert not (repository / lvp.BASE_PROFILE_DIR).exists()

    def broken(_table: str, _column: str) -> set[str]:
        raise RuntimeError("connection refused " + SECRET_DSN)

    with pytest.raises(lvp.LegacyViewProfileError) as caught:
        lvp.write_profile(
            repository, make_catalog(), replace=False, spec=lvp.BASE_SPEC, distinct_values=broken
        )
    assert caught.value.code == "INSPECTION_FAILED"
    assert lvp.write_profile(
        repository, make_catalog(), replace=False, spec=lvp.BASE_SPEC,
        distinct_values=lambda _t, _c: stored | {"기타"},
    ) == "WRITTEN"


# --- review fixes ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("declared", "accepted"),
    [
        ("character varying(50)", True),
        ("character varying", True),
        ("text", True),
        ("character(2)", True),
        ("textual_category", False),
        ("character_state", False),
        ("varcharish", False),
        ("text[]", False),
    ],
)
def test_runtime_accepts_only_builtin_text_column_types(declared: str, accepted: bool) -> None:
    tables = {name: dict(item) for name, item in TABLES.items()}
    tables["subject"] = {**TABLES["subject"], "columns": {**TABLES["subject"]["columns"], "category": declared}}
    profile = make_profile({"Course.category": CODED})
    if accepted:
        make_context(profile, catalog=make_catalog(tables))
    else:
        with pytest.raises(PostgresRuntimeError):
            make_context(profile, catalog=make_catalog(tables))


@pytest.mark.parametrize("field", ["Course.code", "CourseOffering.section"])
def test_identity_fields_cannot_carry_a_dictionary(field: str) -> None:
    # Results would otherwise show a label in place of the course key.
    with pytest.raises(PostgresRuntimeError):
        make_context(make_profile({field: CODED}))


@pytest.mark.parametrize("text", ["‮가", "가​", "⁦가"])
def test_format_characters_are_rejected(text: str) -> None:
    for entry in ({"stored": "A", "label": text}, {"stored": "A", "label": "가", "aliases": [text]}):
        with pytest.raises(ValidationError):
            ValueDictionary.model_validate(
                {"entries": [entry], "unknown": "pass", "reviewer": "r", "reason": "x"}
            )
