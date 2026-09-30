"""Phase 8-09 AI mapping proposals (offline: fake model, no network)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from backend.adaptive.evaluation import ai_mapping as ai
from backend.adaptive.evaluation import variant_benchmark as vb
from backend.adaptive.evaluation import variant_specs as vs
from backend.adaptive.mapping.transforms import FieldTransform
from backend.adaptive.ontology.loader import load_default_ontology

ONTOLOGY = ai._ontology_fields()


class OracleModel:
    """Answers every call from the gold, as a perfect model would."""

    def __init__(self, spec: vs.Variant) -> None:
        self.gold = vs.gold_fields(spec)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, prompt: str, schema: dict[str, Any]) -> Any:
        self.calls.append((prompt, schema))
        if set(schema["properties"]) == set(vs.CANONICAL_FIELDS):
            return {
                name: f"{gold.primary[0]}.{gold.primary[1]}" if gold.primary else "none"
                for name, gold in self.gold.items()
            }
        name = re.search(r"^Field: (\S+) ", prompt, re.MULTILINE).group(1)
        gold = self.gold[name]
        transform = next((t for t in gold.transforms if t is not None), None) if None not in gold.transforms else None
        output: dict[str, Any] = {"transform": transform.value if transform else "none"}
        if "dictionary" in schema["properties"]:
            output["dictionary"] = [
                {"stored": stored, "canonical": canonical}
                for stored, canonical in (gold.dictionary or {}).items()
            ]
        return output


@pytest.mark.parametrize("spec", vs.VARIANTS, ids=vs.VARIANT_NAMES)
def test_a_perfect_model_reproduces_the_gold(spec: vs.Variant) -> None:
    model = OracleModel(spec)
    answer, trace = ai.propose(spec, model)
    score = vb.score_fields(spec, answer)
    assert score["field"] == 15 and score["anchor"], score["wrong"]
    mapped = sum(1 for item in answer.fields.values() if item.table)
    assert trace["calls"] == 1 + mapped == len(model.calls)


def test_column_prompt_only_offers_real_columns() -> None:
    spec = vs.variant("abbreviated")
    prompt, schema = ai.column_prompt(spec, ONTOLOGY)
    refs = {f"{t.name}.{c.name}" for t in spec.tables for c in t.columns}
    for name in vs.CANONICAL_FIELDS:
        assert set(schema["properties"][name]["enum"]) == refs | {"none"}
    assert schema["required"] == list(vs.CANONICAL_FIELDS) and schema["additionalProperties"] is False
    # stored values are listed in text order, as the v1 run showed them
    assert "tb_lect.limit_cnt : integer : 120, 20, 30, 35, 40" in prompt
    assert "s3cret" not in prompt


def test_time_samples_are_shown_as_postgres_renders_them() -> None:
    columns = {c["ref"]: c for c in ai.column_inputs(vs.variant("time-type"))}
    assert columns["schedules.start_time"]["values"][0] == "09:00:00"
    assert columns["schedules.day_of_week"]["values"][0] != "09:00:00"


def test_value_prompt_offers_a_dictionary_only_where_the_runtime_allows_one() -> None:
    columns = {c["ref"]: c for c in ai.column_inputs(vs.variant("category-code"))}
    _prompt, schema = ai.value_prompt("Course.category", ONTOLOGY["Course.category"], columns["courses.category"])
    items = schema["properties"]["dictionary"]["items"]["properties"]
    assert items["stored"]["enum"] == ["E", "G", "M"]
    assert items["canonical"]["enum"] == ["전공", "교양", "일반선택"]
    _prompt, schema = ai.value_prompt("Course.code", ONTOLOGY["Course.code"], columns["courses.course_code"])
    assert "dictionary" not in schema["properties"]  # no vocabulary, identity field
    _prompt, schema = ai.value_prompt("Course.name", ONTOLOGY["Course.name"], columns["courses.course_name"])
    assert set(schema["properties"]) == {"transform"}


def _column(ref: str, pg_type: str, values: list[str]) -> dict[str, Any]:
    table, column = ref.split(".")
    return {"ref": ref, "table": table, "column": column, "type": pg_type, "values": values}


@pytest.mark.parametrize(
    ("name", "column", "output", "expected", "dropped"),
    [
        # a time transform on a text column is dropped
        ("Schedule.start_time", _column("s.t", "text", ["09:00"]), {"transform": "TIME_TO_HHMM"},
         (None, None), ["TRANSFORM_TYPE_MISMATCH"]),
        ("Schedule.start_time", _column("s.t", "time without time zone", ["09:00:00"]),
         {"transform": "TIME_TO_HHMM"}, (FieldTransform.TIME_TO_HHMM, None), []),
        ("Course.credits", _column("c.c", "text", ["3/3/0"]), {"transform": "SLASH_FIRST_NUMERIC"},
         (FieldTransform.SLASH_FIRST_NUMERIC, None), []),
        ("Course.credits", _column("c.c", "numeric", ["3"]), {"transform": "SLASH_FIRST_NUMERIC"},
         (None, None), ["TRANSFORM_TYPE_MISMATCH"]),
        ("Course.category", _column("c.k", "text", ["E", "G", "M"]),
         {"transform": "none", "dictionary": [{"stored": "M", "canonical": "전공"},
                                              {"stored": "G", "canonical": "교양"}]},
         (None, {"M": "전공", "G": "교양"}), []),
        # identical stored and canonical values are a no-op
        ("Course.category", _column("c.k", "text", ["교양", "전공"]),
         {"transform": "none", "dictionary": [{"stored": "전공", "canonical": "전공"}]},
         (None, None), ["DICTIONARY_NO_OP"]),
        # two codes for one canonical value cannot be a dictionary
        ("Course.category", _column("c.k", "text", ["A", "B"]),
         {"transform": "none", "dictionary": [{"stored": "A", "canonical": "전공"},
                                              {"stored": "B", "canonical": "전공"}]},
         (None, None), ["DICTIONARY_NOT_ONE_TO_ONE"]),
        ("Course.category", _column("c.k", "text", ["A"]),
         {"transform": "none", "dictionary": [{"stored": "Z", "canonical": "전공"}]},
         (None, None), ["DICTIONARY_INVALID"]),
        ("Schedule.day", _column("s.d", "text", ["MON"]),
         {"transform": "KOREAN_WEEKDAY", "dictionary": [{"stored": "MON", "canonical": "월"}]},
         (FieldTransform.KOREAN_WEEKDAY, None), ["DICTIONARY_WITH_TRANSFORM"]),
        ("Course.name", _column("c.n", "text", ["x"]), "not an object", (None, None), ["INVALID_OUTPUT"]),
        ("Course.name", _column("c.n", "text", ["x"]), {"transform": "UPPER"}, (None, None),
         ["UNKNOWN_TRANSFORM"]),
    ],
)
def test_validator_keeps_only_what_the_runtime_accepts(
    name: str, column: dict[str, Any], output: Any, expected: tuple[Any, Any], dropped: list[str]
) -> None:
    transform, dictionary, actual_dropped = ai.validate_value_output(name, ONTOLOGY[name], column, output)
    assert (transform, dictionary) == expected and actual_dropped == dropped


def test_duplicate_columns_keep_the_first_field_and_errors_become_unsupported() -> None:
    spec = vs.variant("base")

    def model(prompt: str, schema: dict[str, Any]) -> Any:
        if set(schema["properties"]) == set(vs.CANONICAL_FIELDS):
            choice = {name: "none" for name in vs.CANONICAL_FIELDS}
            choice.update({
                "CourseOffering.section": "course_offerings.section",
                "CourseOffering.capacity": "course_offerings.capacity",
                "CourseOffering.enrolled": "course_offerings.capacity",
            })
            return choice
        raise ai.ProposalError("MODEL_UNAVAILABLE")

    answer, trace = ai.propose(spec, model)
    assert answer.fields["CourseOffering.capacity"].column == "capacity"
    assert answer.fields["CourseOffering.enrolled"].table is None
    assert trace["dropped"]["CourseOffering.enrolled"] == ["COLUMN_ALREADY_USED"]
    # a failed value call leaves the column with no transform or dictionary
    assert answer.fields["CourseOffering.section"].transform is None
    assert trace["failures"]["CourseOffering.section"] == "MODEL_UNAVAILABLE"
    assert answer.anchor_table == "course_offerings"


def test_failed_column_call_leaves_every_field_unsupported() -> None:
    def model(prompt: str, schema: dict[str, Any]) -> Any:
        raise ai.ProposalError("MODEL_INVALID_JSON")

    answer, trace = ai.propose(vs.variant("base"), model)
    assert all(item.table is None for item in answer.fields.values())
    assert trace["calls"] == 1 and answer.anchor_table is None


# --- model boundary ------------------------------------------------------------------


def test_ollama_model_sends_a_schema_and_parses_strictly() -> None:
    sent: list[tuple[str, dict[str, Any], float]] = []

    def transport(url: str, payload: dict[str, Any], timeout: float) -> Any:
        sent.append((url, payload, timeout))
        return {"response": '{"transform": "none"}'}

    model = ai.OllamaJsonModel(transport=transport)
    assert model("p", {"type": "object"}) == {"transform": "none"}
    url, payload, timeout = sent[0]
    assert url == "http://127.0.0.1:11434/api/generate"
    assert payload == {"model": ai.DEFAULT_MODEL, "prompt": "p", "stream": False,
                       "format": {"type": "object"}, "options": {"temperature": 0}}
    assert timeout == 120.0
    for response in ({"response": "not json"}, {"nothing": 1}, "text"):
        broken = ai.OllamaJsonModel(transport=lambda *_args, r=response: r)
        with pytest.raises(ai.ProposalError):
            broken("p", {})

    def failing(*_args: Any) -> Any:
        raise TimeoutError()

    with pytest.raises(ai.ProposalError) as caught:
        ai.OllamaJsonModel(transport=failing)("p", {})
    assert caught.value.code == "MODEL_UNAVAILABLE"


@pytest.mark.parametrize("base_url", ["http://gpu-box:11434", "https://127.0.0.1:11434", "http://localhost:11434"])
def test_ollama_model_accepts_only_loopback(base_url: str) -> None:
    with pytest.raises(Exception):
        ai.OllamaJsonModel(base_url=base_url, transport=lambda *_args: {})


# --- stored proposals -------------------------------------------------------------------


def test_proposals_round_trip_and_feed_the_live_runner(tmp_path: Path) -> None:
    run = {"version": ai.PROMPT_VERSION, "variants": {}}
    for spec in vs.VARIANTS:
        answer, _trace = ai.propose(spec, OracleModel(spec))
        run["variants"][spec.name] = {"answer": answer.model_dump(mode="json")}
    path = tmp_path / "p.json"
    path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    answers = ai.load_answers(path)
    assert answers["legacy-like"] == vb.gold_answer(vs.variant("legacy-like")).model_copy(
        update={"anchor_table": "cnu_courses"}
    )
    del run["variants"]["base"]
    path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        ai.load_answers(path)
    path.write_text(json.dumps({**run, "version": "other"}), encoding="utf-8")
    with pytest.raises(ValueError):
        ai.load_answers(path)


def test_summary_counts_every_proposer_in_a_record() -> None:
    perfect = {q["id"]: "CORRECT" for q in vb.QUESTIONS}
    item = {"fields": {"fields": 15, "column": 15, "transform": 15, "dictionary": 15, "field": 15,
                       "anchor": True, "wrong": {}},
            "execution": {"profile": "ACCEPTED", "outcomes": perfect}}
    run = {"variants": {"base": {"axis": "structure", "fingerprint": "x", "oracle": item,
                                 "rules": item, "rules+anchor": item, "ai": item}}}
    assert vb.proposers(run["variants"]["base"]) == ["oracle", "rules", "rules+anchor", "ai"]
    assert set(vb.summarize(run)) == {"oracle", "rules", "rules+anchor", "ai"}


def test_rule_anchor_arm_uses_the_section_table() -> None:
    spec = vs.variant("legacy-like")
    rules = vb.rule_based_answer(vs.spec_catalog(spec, "bench-x"), load_default_ontology(), spec.name)
    assert vb.with_section_anchor(rules).anchor_table == "cnu_courses"


def test_cli_rejects_bad_labels(capsys: pytest.CaptureFixture[str]) -> None:
    assert ai.main(["--propose", "--label", "Bad Label"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "INVALID_LABEL"


# --- review fixes (ai-mapping-v1.1) ---------------------------------------------------


def test_dictionary_colliding_in_the_runtime_is_dropped() -> None:
    column = _column("c.k", "text", ["교양", "전공"])
    output = {"transform": "none", "dictionary": [{"stored": "전공", "canonical": "교양"},
                                                  {"stored": "교양", "canonical": "일반선택"}]}
    transform, dictionary, dropped = ai.validate_value_output(
        "Course.category", ONTOLOGY["Course.category"], column, output
    )
    assert (transform, dictionary) == (None, None)
    assert dropped == ["DICTIONARY_REJECTED_BY_RUNTIME"]


@pytest.mark.parametrize(
    "column_output",
    [{"Course.code": []}, {"Course.code": "courses.course_code"}, "text", {**{n: "none" for n in vs.CANONICAL_FIELDS}, "x": "none"}],
)
def test_schema_violating_column_output_is_a_recorded_failure(column_output: Any) -> None:
    def model(prompt: str, schema: dict[str, Any]) -> Any:
        return column_output

    answer, trace = ai.propose(vs.variant("base"), model)
    assert trace["failures"] == {"columns": "MODEL_SCHEMA_VIOLATION"}
    assert all(item.table is None for item in answer.fields.values())


def test_schema_violating_value_output_keeps_the_column_and_is_recorded() -> None:
    spec = vs.variant("category-code")
    oracle = OracleModel(spec)

    def model(prompt: str, schema: dict[str, Any]) -> Any:
        if "Field: Course.category " in prompt:
            return {"transform": "none", "dictionary": [{"stored": [], "canonical": "전공"}]}
        return oracle(prompt, schema)

    answer, trace = ai.propose(spec, model)
    assert trace["failures"] == {"Course.category": "MODEL_SCHEMA_VIOLATION"}
    assert answer.fields["Course.category"] == vb.FieldAnswer(table="courses", column="category")


def test_proposals_with_failures_are_never_scored(tmp_path: Path) -> None:
    run = {"version": ai.PROMPT_VERSION, "variants": {}}
    for spec in vs.VARIANTS:
        answer, trace = ai.propose(spec, OracleModel(spec))
        run["variants"][spec.name] = {"answer": answer.model_dump(mode="json"), "trace": trace}
    run["variants"]["flat"]["trace"]["failures"] = {"columns": "MODEL_UNAVAILABLE"}
    path = tmp_path / "p.json"
    path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        ai.load_answers(path)


def test_model_json_rejects_duplicate_keys() -> None:
    model = ai.OllamaJsonModel(transport=lambda *_args: {"response": '{"transform": "none", "transform": "x"}'})
    with pytest.raises(ai.ProposalError) as caught:
        model("p", {})
    assert caught.value.code == "MODEL_INVALID_JSON"


def test_transform_help_does_not_repeat_benchmark_values() -> None:
    stored = {str(value) for spec in vs.VARIANTS for values in vs.sample_values(spec).values() for value in values}
    for text in ai.TRANSFORM_HELP.values():
        for quoted in re.findall(r"'([^']+)'", text):
            assert quoted not in stored
