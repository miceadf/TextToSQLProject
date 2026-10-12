"""Phase 8-08 synthetic schema-variant benchmark (offline parts)."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest
from pydantic import ValidationError

from backend.adaptive.api.postgres_runtime import build_validated_contexts
from backend.adaptive.catalog.postgres import PostgresConnectionConfig
from backend.adaptive.evaluation import variant_benchmark as vb
from backend.adaptive.evaluation import variant_specs as vs
from backend.adaptive.harness.contracts import CompiledQuery
from backend.adaptive.mapping.transforms import FieldTransform
from backend.adaptive.ontology.loader import load_default_ontology

DSN = "postgresql://svc:s3cret-pw-808@127.0.0.1:5432/schema_adaptive_synthetic"
ONTOLOGY = load_default_ontology()


class FakeAdapter:
    def __init__(self, catalog: Any) -> None:
        self.catalog = catalog

    def inspect(self, database_id: str, *, include_samples: bool = False) -> Any:
        return self.catalog.model_copy(deep=True)


class EmptyExecutor:
    def __init__(self) -> None:
        self.queries: list[CompiledQuery] = []

    def execute(self, database_id: str, compiled: CompiledQuery) -> tuple[list[str], list[dict[str, Any]]]:
        self.queries.append(compiled)
        return list(compiled.expected_columns), []


def contexts_for(spec: vs.Variant, answer: vb.VariantMappingAnswer) -> Any:
    database_id = f"bench-{spec.name}"
    catalog = vs.spec_catalog(spec, database_id)
    profile = vb.answer_profile(answer, database_id, catalog, PostgresConnectionConfig(DSN))
    return build_validated_contexts(
        [profile],
        adapters_by_id={database_id: FakeAdapter(catalog)},
        executors_by_id={database_id: EmptyExecutor()},
        expected_ontology_version=1,
    )[database_id]


# --- specification and gold --------------------------------------------------------


def test_twelve_variants_across_the_four_axes() -> None:
    assert len(vs.VARIANTS) == 12 and len(set(vs.VARIANT_NAMES)) == 12
    assert Counter(spec.axis for spec in vs.VARIANTS) == {
        "structure": 3, "format": 2, "code": 3, "meaning": 3, "combined": 1,
    }


@pytest.mark.parametrize("spec", vs.VARIANTS, ids=vs.VARIANT_NAMES)
def test_spec_is_consistent(spec: vs.Variant) -> None:
    gold = vs.gold_fields(spec)
    assert set(gold) == set(vs.CANONICAL_FIELDS)
    names = {table.name for table in spec.tables}
    assert spec.anchor in names
    for table in spec.tables:
        columns = [col.name for col in table.columns]
        assert len(columns) == len(set(columns))
        assert set(table.pk) <= set(columns)
        rows = vs.table_rows(spec, table)
        # primary keys are unique and every row has one value per column
        keys = [tuple(row[columns.index(name)] for name in table.pk) for row in rows]
        assert len(keys) == len(set(keys)) and all(len(row) == len(columns) for row in rows)
        for _name, local, target, target_columns in table.fks:
            assert target in names and set(local) <= set(columns)
            target_table = spec.table(target)
            index = [col.name for col in target_table.columns].index(target_columns[0])
            present = {row[index] for row in vs.table_rows(spec, target_table)}
            assert {row[columns.index(local[0])] for row in rows} <= present
    # the catalog the inspector will report is deterministic
    assert vs.spec_catalog(spec, "bench-x") == vs.spec_catalog(spec, "bench-x")


def test_gold_accepts_foreign_key_copies_and_marks_unsupported_fields() -> None:
    base = vs.gold_fields(vs.variant("base"))
    assert base["Course.code"].locations == {
        ("courses", "course_code"), ("course_offerings", "course_code"),
    }
    assert base["Schedule.day"].transforms == {None, FieldTransform.KOREAN_WEEKDAY}
    trap = vs.gold_fields(vs.variant("grade-trap"))
    assert trap["CourseOffering.year"].locations == frozenset()
    legacy = vs.gold_fields(vs.variant("legacy-like"))
    assert {name for name, gold in legacy.items() if not gold.locations} == {
        "CourseOffering.year", "CourseOffering.semester",
    }
    assert legacy["Schedule.day"].dictionary == {
        "MON": "월", "TUE": "화", "WED": "수", "THU": "목", "FRI": "금", "SAT": "토",
    }
    assert legacy["Course.credits"].transforms == {FieldTransform.SLASH_FIRST_NUMERIC}


def test_stored_values_follow_the_variant_format() -> None:
    samples = vs.sample_values(vs.variant("credits-slash"))
    assert samples[("courses", "credit_hours")] == ["1/1/0", "2/2/0", "3/3/0"]
    assert vs.sample_values(vs.variant("category-code"))[("courses", "category")] == ["E", "G", "M"]
    capacity = vs.sample_values(vs.variant("capacity-trap"))
    assert min(capacity[("course_offerings", "capacity")]) == 30
    optional = vs.variant("optional-schedule")
    assert len(vs.table_rows(optional, optional.table("schedules"))) == 10


# --- reference -----------------------------------------------------------------------


def _question(qid: str) -> dict[str, Any]:
    return next(q for q in vb.QUESTIONS if q["id"] == qid)["ir"]


def test_reference_follows_sql_semantics() -> None:
    base = vs.variant("base")
    assert sum(vb.reference_result(base, _question("v01")).values()) == 12
    assert vb.reference_result(base, _question("v02")) == Counter({
        ("CSE101", "01", 3.0): 1, ("CSE201", "01", 3.0): 2, ("CSE301", "01", 3.0): 1,
        ("MTH101", "01", 3.0): 1, ("MTH201", "01", 3.0): 1, ("BIZ101", "01", 3.0): 2,
        ("CSE101", "02", 3.0): 1,
    })
    # NULL timetable values appear as rows but never satisfy a filter
    optional = vs.variant("optional-schedule")
    rows = vb.reference_result(optional, _question("v16"))
    assert rows[("CSE390", "01", None, None, None)] == 1
    assert sum(vb.reference_result(optional, _question("v14")).values()) == sum(
        vb.reference_result(base, _question("v14")).values()
    ) - 2
    assert vb.reference_result(vs.variant("grade-trap"), _question("v09")) == vb.REFUSED


def test_every_canonical_field_is_exercised() -> None:
    used = set()
    for question in vb.QUESTIONS:
        used |= {*question["ir"]["select"], *(f["field"] for f in question["ir"]["filters"])}
    assert used == set(vs.CANONICAL_FIELDS)


# --- answers and field scores -----------------------------------------------------------


@pytest.mark.parametrize("spec", vs.VARIANTS, ids=vs.VARIANT_NAMES)
def test_oracle_scores_full_marks_and_the_runtime_accepts_it(spec: vs.Variant) -> None:
    score = vb.score_fields(spec, vb.gold_answer(spec))
    assert score["field"] == 15 and score["anchor"] and score["wrong"] == {}
    context = contexts_for(spec, vb.gold_answer(spec))
    assert context.anchor_table() == spec.anchor


def test_score_fields_names_the_wrong_part() -> None:
    spec = vs.variant("legacy-like")
    gold = vb.gold_answer(spec)
    fields = dict(gold.fields)
    fields["Course.credits"] = vb.FieldAnswer(table="subject", column="credit_hours")
    fields["Schedule.day"] = vb.FieldAnswer(table="course_schedule", column="day_of_week",
                                            dictionary={"MON": "월"})
    fields["CourseOffering.year"] = vb.FieldAnswer(table="cnu_courses", column="course_year")
    fields["Instructor.name"] = vb.FieldAnswer()
    score = vb.score_fields(spec, gold.model_copy(update={"fields": fields, "anchor_table": None}))
    assert score["wrong"] == {
        "Course.credits": ["transform"],
        "Schedule.day": ["dictionary"],
        "CourseOffering.year": ["column"],
        "Instructor.name": ["column"],
    }
    assert score["field"] == 11 and score["anchor"] is False
    # a foreign-key copy is as good as the entity column
    alt = dict(gold.fields)
    alt["Department.code"] = vb.FieldAnswer(table="cnu_courses", column="dept_code")
    assert vb.score_fields(spec, gold.model_copy(update={"fields": alt}))["field"] == 15


def test_single_table_variant_accepts_no_anchor() -> None:
    spec = vs.variant("flat")
    answer = vb.gold_answer(spec).model_copy(update={"anchor_table": None})
    assert vb.score_fields(spec, answer)["anchor"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"table": "t"},
        {"transform": "TIME_TO_HHMM"},
        {"table": "t", "column": "c", "transform": "TIME_TO_HHMM", "dictionary": {"a": "b"}},
        {"table": "t", "column": "c", "extra": 1},
    ],
)
def test_field_answer_shape_is_strict(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        vb.FieldAnswer.model_validate(payload)


def test_answer_must_cover_the_canonical_fields() -> None:
    with pytest.raises(ValidationError):
        vb.VariantMappingAnswer(variant="base", fields={"Course.code": vb.FieldAnswer()})


# --- rule baseline -------------------------------------------------------------------------


def test_rule_baseline_is_deterministic_and_locked() -> None:
    # The unversioned benchmark selects v2 itself; product registration uses v3.
    scores = vb.offline_scores()
    assert vb.offline_scores() == scores
    rules = {name: record["rules"]["field"] for name, record in scores.items()}
    assert rules == {
        "base": 15, "flat": 15, "optional-schedule": 15, "credits-slash": 14, "time-type": 13,
        "category-code": 14, "weekday-english": 14, "semester-code": 14, "abbreviated": 10,
        "grade-trap": 15, "capacity-trap": 14, "legacy-like": 10,
    }
    # rules never propose transforms, dictionaries or anchors
    for spec in vs.VARIANTS:
        answer = vb.rule_based_answer(vs.spec_catalog(spec, "bench-x"), ONTOLOGY, spec.name)
        assert answer.anchor_table is None
        assert all(item.transform is None and item.dictionary is None for item in answer.fields.values())
        located = [(item.table, item.column) for item in answer.fields.values() if item.table]
        assert len(located) == len(set(located))


def test_rule_answers_are_accepted_by_the_runtime() -> None:
    for spec in vs.VARIANTS:
        answer = vb.rule_based_answer(vs.spec_catalog(spec, f"bench-{spec.name}"), ONTOLOGY, spec.name)
        contexts_for(spec, answer)


# --- execution classification (no database) -------------------------------------------------


def test_execute_answer_classifies_outcomes() -> None:
    spec = vs.variant("grade-trap")
    database_id = f"bench-{spec.name}"
    catalog = vs.spec_catalog(spec, database_id)
    executor = EmptyExecutor()
    result = vb.execute_answer(
        spec, vb.gold_answer(spec), database_id=database_id, catalog=catalog,
        config=PostgresConnectionConfig(DSN),
        make_adapter=lambda: FakeAdapter(catalog), make_executor=lambda: executor,
    )
    outcomes = result["outcomes"]
    assert result["profile"] == "ACCEPTED"
    # Offline benchmark uses the profile's declared v1, preserving refusals.
    assert outcomes["v09"] == "CORRECT" and outcomes["v19"] == "CORRECT"
    assert outcomes["v01"] == "WRONG_ROWS"  # the fake executor returns no rows
    assert len(executor.queries) == len(vb.QUESTIONS) - 2
    # an answer the runtime rejects fails every question
    broken = dict(vb.gold_answer(spec).fields)
    broken["Course.name"] = vb.FieldAnswer(table="courses", column="course_code")
    rejected = vb.execute_answer(
        spec, vb.gold_answer(spec).model_copy(update={"fields": broken}), database_id=database_id,
        catalog=catalog, config=PostgresConnectionConfig(DSN),
        make_adapter=lambda: FakeAdapter(catalog), make_executor=EmptyExecutor,
    )
    assert rejected["profile"] == "REJECTED"
    assert set(rejected["outcomes"].values()) == {"PROFILE_REJECTED"}


def test_creation_order_puts_referenced_tables_first() -> None:
    for spec in vs.VARIANTS:
        created: set[str] = set()
        for table in vb._creation_order(spec):
            assert all(target in created for _n, _c, target, _tc in table.fks)
            created.add(table.name)


# --- CLI ------------------------------------------------------------------------------------


def test_check_mode_prints_one_line_per_variant(capsys: pytest.CaptureFixture[str]) -> None:
    assert vb.main(["--check"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["variant"] for line in lines] == list(vs.VARIANT_NAMES)
    assert all(line["oracle"]["field"] == 15 for line in lines)


@pytest.mark.parametrize(
    ("env", "code"),
    [
        ({}, "ENV_MISSING"),
        ({"SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN": "postgresql://u:p@db.example:5432/schema_adaptive_synthetic"},
         "INVALID_HOST"),
        ({"SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN": "postgresql://u:p@127.0.0.1:5432/postgres"},
         "INVALID_DATABASE"),
    ],
)
def test_live_mode_validates_the_dsn_before_connecting(
    env: dict[str, str], code: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert vb.main(["--live-synthetic"], environ=env) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == code and "p@" not in output


def test_live_mode_rejects_bad_labels(capsys: pytest.CaptureFixture[str]) -> None:
    assert vb.main(["--live-synthetic", "--label", "Bad Label"], environ={
        "SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN": DSN,
    }) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "INVALID_LABEL"


# --- review fixes ----------------------------------------------------------------------


def _record(outcomes: dict[str, str], *, field: int = 15, profile: str = "ACCEPTED") -> dict[str, Any]:
    return {
        "fields": {"fields": 15, "column": field, "transform": 15, "dictionary": 15, "field": field,
                   "anchor": True, "wrong": {}},
        "execution": {"profile": profile, "outcomes": outcomes},
    }


def test_run_problems_flag_cleanup_errors_and_an_imperfect_oracle() -> None:
    perfect = {q["id"]: "CORRECT" for q in vb.QUESTIONS}
    run = {"variants": {
        "base": {"axis": "structure", "oracle": _record(perfect), "rules": _record(perfect)},
        "flat": {"axis": "structure", "oracle": _record(perfect), "rules": _record(perfect),
                 "cleanup": "FAILED"},
        "time-type": {"axis": "format", "oracle": _record({**perfect, "v14": "WRONG_ROWS"}),
                      "rules": _record(perfect)},
        "abbreviated": {"axis": "meaning", "oracle": _record(perfect, field=14), "rules": _record(perfect)},
        "grade-trap": {"axis": "meaning", "oracle": _record(perfect, profile="REJECTED"),
                       "rules": _record(perfect)},
        "legacy-like": {"axis": "combined", "error": "CATALOG_MISMATCH"},
    }}
    assert vb.run_problems(run) == {
        "flat": "CLEANUP_FAILED",
        "time-type": "ORACLE_NOT_PERFECT",
        "abbreviated": "ORACLE_NOT_PERFECT",
        "grade-trap": "ORACLE_NOT_PERFECT",
        "legacy-like": "CATALOG_MISMATCH",
    }


def test_inspection_failure_is_not_charged_to_the_answer() -> None:
    spec = vs.variant("base")
    catalog = vs.spec_catalog(spec, "bench-base")

    class Broken:
        def inspect(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("connection refused " + DSN)

    with pytest.raises(RuntimeError) as caught:
        vb.execute_answer(
            spec, vb.gold_answer(spec), database_id="bench-base", catalog=catalog,
            config=PostgresConnectionConfig(DSN), make_adapter=Broken, make_executor=EmptyExecutor,
        )
    assert str(caught.value) == "INSPECTION_FAILED"
    drifted = catalog.model_copy(update={"schema_fingerprint": "0" * 64})
    with pytest.raises(RuntimeError) as caught:
        vb.execute_answer(
            spec, vb.gold_answer(spec), database_id="bench-base", catalog=catalog,
            config=PostgresConnectionConfig(DSN), make_adapter=lambda: FakeAdapter(drifted),
            make_executor=EmptyExecutor,
        )
    assert str(caught.value) == "CATALOG_MISMATCH"


def test_reference_refuses_questions_a_limit_would_truncate() -> None:
    ir = {**_question("v01"), "limit": 5}
    with pytest.raises(ValueError):
        vb.reference_result(vs.variant("base"), ir)
    for spec in vs.VARIANTS:
        for question in vb.QUESTIONS:
            vb.reference_result(spec, question["ir"])
