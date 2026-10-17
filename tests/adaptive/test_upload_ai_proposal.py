"""Phase 8-12: local-model column proposals in the SQLite upload flow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.adaptive.api import dev_launcher
from backend.adaptive.api.registration_upload import (
    SQLITE_UPLOAD_CONTENT_TYPE,
    SQLITE_UPLOAD_PATH,
    SQLITE_UPLOAD_VERSION,
    register_sqlite_upload_routes,
)
from backend.adaptive.api.review_publish import REVIEW_PATH
from backend.adaptive.api.app_factory import (
    SYNTHETIC_PROTOTYPE_DATABASE_ID,
    create_synthetic_clarification_prototype,
    set_private_repository_root_for_tests,
)
from backend.adaptive.api.database_registry import CANONICAL_SELECT, CATALOG_PATH
from backend.adaptive.catalog import SQLiteAdapter
from backend.adaptive.evaluation import ai_mapping
from backend.adaptive.evaluation.variant_specs import CANONICAL_FIELDS, variant
from backend.adaptive.fixtures.flat import build_flat_database
from backend.adaptive.fixtures.seed import DEFAULT_SEED
from backend.adaptive.mapping import MappingProposal, MappingSignal, RuleBasedSchemaAligner
from backend.adaptive.mapping.ai_proposal import (
    NONE,
    ProposalError,
    build_column_prompt,
    is_ai_candidate,
    propose_upload_mapping,
)
from backend.adaptive.ontology import load_default_ontology
from backend.adaptive.registration import storage as registration_storage

# The flat fixture's columns under names the rules do not know.
RENAMED = {
    "course_code": "subj_cd",
    "course_name": "subj_nm",
    "credits": "pt",
    "category": "kind",
    "department_code": "org_cd",
    "department_name": "org_nm",
    "instructor_name": "prof",
    "year": "yr",
    "semester": "term",
    "section": "cls",
    "capacity": "cap",
    "enrolled": "cnt",
    "day": "wday",
    "start_time": "t_from",
    "end_time": "t_to",
}
FLAT_COLUMN = {
    "Course.code": "course_code",
    "Course.name": "course_name",
    "Course.credits": "credits",
    "Course.category": "category",
    "Department.code": "department_code",
    "Department.name": "department_name",
    "Instructor.name": "instructor_name",
    "CourseOffering.year": "year",
    "CourseOffering.semester": "semester",
    "CourseOffering.section": "section",
    "CourseOffering.capacity": "capacity",
    "CourseOffering.enrolled": "enrolled",
    "Schedule.day": "day",
    "Schedule.start_time": "start_time",
    "Schedule.end_time": "end_time",
}
CORRECT = {field: f"lect.{RENAMED[column]}" for field, column in FLAT_COLUMN.items()}
MARKER = "ZX-SAMPLE-731"


def _make_repo(base: Path) -> Path:
    repo = base / "repo"
    repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", str(repo)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    (repo / ".gitignore").write_text(".schema-adaptive/\n", encoding="utf-8")
    return repo


def _renamed_database(base: Path, *, marker_row: bool = False) -> Path:
    flat = build_flat_database(base / "flat.sqlite3", seed=DEFAULT_SEED)
    target = base / "renamed.sqlite3"
    connection = sqlite3.connect(target)
    try:
        connection.execute("ATTACH DATABASE ? AS flat", (str(flat),))
        select = ", ".join(f"{old} AS {new}" for old, new in RENAMED.items())
        connection.execute(f"CREATE TABLE lect AS SELECT {select} FROM flat.course_offerings")
        if marker_row:
            # A distinctive stored value that may reach the prompt only.
            connection.execute("CREATE TABLE memo (note TEXT)")
            connection.execute("INSERT INTO memo VALUES (?)", (MARKER,))
        connection.commit()
        connection.execute("DETACH DATABASE flat")
    finally:
        connection.close()
    return target


class FakeModel:
    """Records prompts and answers with a fixed column choice."""

    def __init__(self, answer: Any = None, *, error: Exception | None = None) -> None:
        self.answer = dict(CORRECT) if answer is None else answer
        self.error = error
        self.prompts: list[str] = []
        self.schemas: list[dict[str, Any]] = []

    def __call__(self, prompt: str, schema: dict[str, Any]) -> Any:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        if self.error is not None:
            raise self.error
        if isinstance(self.answer, dict) and "Course.target_grade" in schema.get("required", []):
            # Phase 8-18: the fixtures store no target grade; the model answers NONE.
            return {"Course.target_grade": NONE, **self.answer}
        return self.answer


def _inputs(path: Path) -> tuple[Any, Any, MappingProposal]:
    catalog = SQLiteAdapter({"db": path}, sample_limit=10).inspect("db", include_samples=True)
    ontology = load_default_ontology()
    return catalog, ontology, RuleBasedSchemaAligner().align(catalog, ontology)


# --- proposal assembly ----------------------------------------------------------------------


def test_ai_choice_is_first_with_rule_score_and_ai_evidence(tmp_path: Path) -> None:
    catalog, ontology, rules = _inputs(_renamed_database(tmp_path))
    model = FakeModel()
    proposal = propose_upload_mapping(catalog, ontology, rules, model=model)
    assert proposal is not None and len(model.prompts) == 1
    assert model.prompts[0].startswith("Prompt version: ai-mapping-v3/columns")  # Phase 8-23
    table = next(t for t in catalog.tables if t.name == "lect")
    for field, ref in CORRECT.items():
        # ADR-101: stale AI year choices are discarded, other choices survive.
        if field in ontology.retired_fields:
            assert field not in proposal.candidates
            continue
        first = proposal.candidates[field][0]
        assert f"{first.table}.{first.column}" == ref
        assert is_ai_candidate(first)
        column = next(c for c in table.columns if c.name == first.column)
        rule = RuleBasedSchemaAligner().score_column(ontology, field, table, column)
        assert first.confidence == rule.confidence  # the model gives no calibrated score
        expected = "AUTO_APPROVE_CANDIDATE" if rule.confidence >= 0.90 else "NEEDS_REVIEW"
        assert first.review_status.value == expected
        assert first.evidence[-1].signal is MappingSignal.AI_PROPOSAL
        assert first.evidence[:-1] == rule.evidence
        rest = proposal.candidates[field][1:]
        assert (first.table, first.column) not in {(c.table, c.column) for c in rest}
        assert all(not is_ai_candidate(c) for c in rest)
        assert len(proposal.candidates[field]) <= 4
    assert proposal.unmapped_fields == ["Course.target_grade"]  # Phase 8-18: not stored


def test_none_and_reused_columns_keep_the_rule_candidates(tmp_path: Path) -> None:
    catalog, ontology, rules = _inputs(_renamed_database(tmp_path))
    answer = dict(CORRECT)
    answer["Course.name"] = NONE
    answer["Department.name"] = answer["Course.code"]  # the same column twice
    proposal = propose_upload_mapping(catalog, ontology, rules, model=FakeModel(answer))
    assert proposal is not None
    assert proposal.candidates["Course.name"] == rules.candidates["Course.name"]
    assert proposal.candidates["Department.name"] == rules.candidates["Department.name"]
    assert is_ai_candidate(proposal.candidates["Course.code"][0])
    for field in ("Course.name", "Department.name"):
        assert (field in proposal.unmapped_fields) == (field in rules.unmapped_fields)


@pytest.mark.parametrize(
    "model",
    [
        FakeModel(error=ProposalError("MODEL_UNAVAILABLE")),
        FakeModel(error=RuntimeError("boom")),
        FakeModel({"Course.code": "lect.subj_cd"}),  # required keys missing
        FakeModel({**CORRECT, "Course.code": "lect.nope"}),  # not a real column
        FakeModel({**CORRECT, "extra": NONE}),
        FakeModel(["lect.subj_cd"]),
        FakeModel({field: NONE for field in CANONICAL_SELECT}),  # nothing usable
    ],
)
def test_unusable_model_output_gives_no_proposal(tmp_path: Path, model: FakeModel) -> None:
    catalog, ontology, rules = _inputs(_renamed_database(tmp_path))
    assert propose_upload_mapping(catalog, ontology, rules, model=model) is None


def test_ambiguous_references_are_not_sent(tmp_path: Path) -> None:
    path = tmp_path / "ambiguous.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE "a.b" (c TEXT)')
    connection.execute('CREATE TABLE a ("b.c" TEXT)')
    connection.commit()
    connection.close()
    catalog, ontology, rules = _inputs(path)
    model = FakeModel()
    assert propose_upload_mapping(catalog, ontology, rules, model=model) is None
    assert model.prompts == []


def test_benchmark_prompt_is_the_product_prompt() -> None:
    ontology = ai_mapping._ontology_fields()
    spec = variant("credits-slash")
    prompt, schema = ai_mapping.column_prompt(spec, ontology, version="ai-mapping-v2")
    assert (prompt, schema) == build_column_prompt(
        ai_mapping.column_inputs(spec), ontology, CANONICAL_FIELDS, version="ai-mapping-v2"
    )
    # Pinned: moving the prompt into the product module changed no benchmark text.
    digest = hashlib.sha256(
        (prompt + json.dumps(schema, sort_keys=True, ensure_ascii=False)).encode()
    ).hexdigest()
    assert digest == BENCHMARK_V2_CREDITS_SLASH_PROMPT_SHA256


# --- registration -----------------------------------------------------------------------------


def _register(repo: Path, source: Path, database_id: str, proposer: Any = None) -> Any:
    kwargs = {} if proposer is None else {"mapping_proposer": proposer}
    registration_storage.register_database(
        repository_root=repo, source_path=source, database_id=database_id, label="Label", **kwargs
    )
    return registration_storage.load_registration(repository_root=repo, database_id=database_id)


def _proposer(model: Any) -> Any:
    return lambda catalog, ontology, rules: propose_upload_mapping(catalog, ontology, rules, model=model)


def test_examples_reach_the_prompt_but_never_the_stored_files(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    source = _renamed_database(tmp_path, marker_row=True)
    model = FakeModel()
    loaded = _register(repo, source, "ai-samples", _proposer(model))
    assert any(MARKER in prompt for prompt in model.prompts)
    stored = repo / ".schema-adaptive" / "registered-databases" / "ai-samples"
    for name in ("catalog.json", "proposal.json", "registration.json"):
        assert MARKER not in (stored / name).read_text(encoding="utf-8")
    assert is_ai_candidate(loaded.proposal.candidates["Course.code"][0])
    assert all(not column.sample_values for table in loaded.catalog.tables for column in table.columns)


@pytest.mark.parametrize(
    "proposer",
    [
        lambda catalog, ontology, rules: (_ for _ in ()).throw(RuntimeError("boom")),
        lambda catalog, ontology, rules: None,
        lambda catalog, ontology, rules: "not a proposal",
        lambda catalog, ontology, rules: rules.model_copy(update={"database_id": "other"}),
        lambda catalog, ontology, rules: rules.model_copy(
            update={"candidates": {k: v for k, v in rules.candidates.items() if k != "Course.code"}}
        ),
        lambda catalog, ontology, rules: rules.model_copy(
            update={
                "candidates": {
                    **rules.candidates,
                    "Course.code": [
                        rules.candidates["Course.code"][0].model_copy(update={"column": "ghost"})
                    ],
                }
            }
        ),
    ],
)
def test_failing_or_inconsistent_proposer_keeps_the_rule_proposal(tmp_path: Path, proposer: Any) -> None:
    repo = _make_repo(tmp_path)
    source = _renamed_database(tmp_path)
    loaded = _register(repo, source, "ai-fallback", proposer)
    _catalog, _ontology, rules = _inputs(source)
    assert loaded.proposal.candidates == rules.candidates
    assert loaded.proposal.unmapped_fields == rules.unmapped_fields


# --- HTTP -----------------------------------------------------------------------------------


@pytest.fixture
def make_client(tmp_path: Path):
    clients: list[TestClient] = []
    repo = _make_repo(tmp_path)

    def build(mapping_model: Any = None) -> TestClient:
        kwargs = {} if mapping_model is None else {"mapping_model": mapping_model}
        app = create_synthetic_clarification_prototype(**kwargs)
        app.state.sqlite_upload_repository_root = str(repo)
        app.state.mapping_review_repository_root = str(repo)
        set_private_repository_root_for_tests(repo)
        client = TestClient(app, raise_server_exceptions=False)
        clients.append(client)
        return client

    try:
        yield build, tmp_path
    finally:
        for client in clients:
            client.close()
        set_private_repository_root_for_tests(None)


def _upload(client: TestClient, database_id: str, raw: bytes) -> Any:
    return client.post(
        SQLITE_UPLOAD_PATH + f"?database_id={database_id}&label=Label",
        content=raw,
        headers={"Content-Type": SQLITE_UPLOAD_CONTENT_TYPE},
    )


def _action(client: TestClient, payload: dict[str, Any]) -> Any:
    return client.post(REVIEW_PATH, json=payload)


def test_ai_proposal_upload_review_publish_and_catalog(make_client) -> None:
    build, tmp_path = make_client
    model = FakeModel()
    client = build(model)
    raw = _renamed_database(tmp_path).read_bytes()
    uploaded = _upload(client, "ai-upload", raw)
    assert uploaded.status_code == 200, uploaded.text[:300]
    body = uploaded.json()
    assert body["version"] == SQLITE_UPLOAD_VERSION == "private-sqlite-upload-v2"
    assert body["mapping"]["proposer"] == "AI"
    by_field = {field["semantic_field"]: field for field in body["mapping"]["fields"]}
    top = by_field["Course.code"]["top_candidate"]
    assert (top["table"], top["column"]) == ("lect", "subj_cd")
    assert top["reasons"][-1]["signal"] == "AI_PROPOSAL"
    assert len(model.prompts) == 1

    initiated = _action(client, {"action": "INIT", "database_id": "ai-upload", "reviewer": "tester"})
    assert initiated.status_code == 200, initiated.text[:300]
    for field in initiated.json()["review"]["fields"]:
        sources = [candidate["source"] for candidate in field["candidates"]]
        if not sources:
            assert field["semantic_field"] == "Course.target_grade"  # Phase 8-18: not stored
            ignored = _action(client, {
                "action": "IGNORE", "database_id": "ai-upload", "semantic_field": "Course.target_grade",
                "reviewer": "tester", "reason": "이 DB에 없음",
            })
            assert ignored.status_code == 200, ignored.text[:300]
            continue
        assert sources[0] == "AI" and set(sources[1:]) <= {"RULES"}
        approved = _action(
            client,
            {
                "action": "APPROVE",
                "database_id": "ai-upload",
                "semantic_field": field["semantic_field"],
                "candidate_index": 0,
                "reviewer": "tester",
            },
        )
        assert approved.status_code == 200, approved.text[:300]
    published = _action(client, {"action": "PUBLISH", "database_id": "ai-upload", "published_by": "tester"})
    assert published.status_code == 200, published.text[:300]

    # Approving only the AI candidates reproduces the known flat catalog.
    catalog = client.get(CATALOG_PATH, params={"database_id": "ai-upload"})
    flat = client.get(CATALOG_PATH, params={"database_id": SYNTHETIC_PROTOTYPE_DATABASE_ID})
    assert catalog.status_code == 200
    assert catalog.json()["items"] == flat.json()["items"]


@pytest.mark.parametrize(
    ("model", "expected"),
    [(None, "RULES"), (FakeModel(error=ProposalError("MODEL_UNAVAILABLE")), "AI_UNAVAILABLE")],
)
def test_upload_reports_rule_proposals(make_client, model: Any, expected: str) -> None:
    build, tmp_path = make_client
    client = build(model)
    uploaded = _upload(client, "rules-upload", _renamed_database(tmp_path).read_bytes())
    assert uploaded.status_code == 200, uploaded.text[:300]
    assert uploaded.json()["mapping"]["proposer"] == expected
    initiated = _action(client, {"action": "INIT", "database_id": "rules-upload", "reviewer": "tester"})
    assert all(
        candidate["source"] == "RULES"
        for field in initiated.json()["review"]["fields"]
        for candidate in field["candidates"]
    )


def test_registration_runs_off_the_event_loop(tmp_path: Path) -> None:
    from fastapi import FastAPI

    repo = _make_repo(tmp_path)
    seen: list[bool] = []

    def register(**kwargs: Any) -> Path:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            seen.append(True)  # a worker thread: waiting here blocks no request
        else:
            seen.append(False)
        assert "mapping_proposer" in kwargs
        return registration_storage.register_database(**kwargs)

    app = FastAPI()
    register_sqlite_upload_routes(
        app,
        repository_root=repo,
        register_database=register,
        mapping_proposer=lambda catalog, ontology, rules: None,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        uploaded = _upload(client, "thread-upload", _renamed_database(tmp_path).read_bytes())
    assert uploaded.status_code == 200, uploaded.text[:300]
    assert seen == [True]
    assert uploaded.json()["mapping"]["proposer"] == "AI_UNAVAILABLE"


def test_non_callable_mapping_model_is_rejected() -> None:
    with pytest.raises(RuntimeError):
        create_synthetic_clarification_prototype(mapping_model="not callable")


# --- CLI and launcher settings ------------------------------------------------------------------


class _FakeUvicorn:
    def __init__(self) -> None:
        self.apps: list[Any] = []

    def run(self, app: Any, **_kwargs: Any) -> None:
        self.apps.append(app)


@pytest.mark.parametrize(
    ("argv", "mapping_kind", "chat_kind"),
    [
        ([], "rules", "deterministic"),
        (["--mapping-model-tag", "qwen2.5-coder:7b-instruct"], "ai", "deterministic"),
        # The base URL alone serves the mapping model and does not enable chat.
        (
            ["--mapping-model-tag", "m:1", "--ollama-base-url", "http://127.0.0.1:12345"],
            "ai",
            "deterministic",
        ),
    ],
)
def test_cli_mapping_model_is_separate_from_chat(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], mapping_kind: str, chat_kind: str
) -> None:
    import sys

    from backend.adaptive.api import app_factory as proto

    fake = _FakeUvicorn()
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    assert proto.main(argv) == 0
    [app] = fake.apps
    assert app.state.adaptive_app_mapping_proposer_kind == mapping_kind
    assert app.state.adaptive_app_provider_kind == chat_kind


@pytest.mark.parametrize(
    "argv",
    [
        ["--mapping-model-tag", "m:1", "--ollama-base-url", "http://example.com:11434"],
        ["--mapping-model-tag", "m:1", "--mapping-model-tag", "m:2"],
        ["--ollama-base-url", "http://127.0.0.1:11434"],  # unchanged: chat needs its tag
    ],
)
def test_cli_rejects_invalid_mapping_settings(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    import sys

    from backend.adaptive.api import app_factory as proto

    fake = _FakeUvicorn()
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    try:
        code = proto.main(argv)
    except SystemExit as exc:
        code = exc.code
    assert code == 2 and fake.apps == []


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, ("qwen2.5-coder:7b-instruct", "http://127.0.0.1:11434", "enabled")),
        (
            {"MAPPING_MODEL": "other:tag", "OLLAMA_BASE_URL": "http://127.0.0.1:12345/"},
            ("other:tag", "http://127.0.0.1:12345", "enabled"),
        ),
        ({"OLLAMA_BASE_URL": "http://example.com:11434"}, (None, None, "disabled (INVALID_OR_NON_LOOPBACK_CONFIG)")),
    ],
)
def test_launcher_mapping_model_settings(env: dict[str, str], expected: tuple[Any, Any, str]) -> None:
    settings, status = dev_launcher.plan_mapping_model(env)
    tag, base_url, expected_status = expected
    assert status == expected_status
    if tag is None:
        assert settings is None
    else:
        assert settings == {"model_tag": tag, "base_url": base_url}


def test_launcher_reads_mapping_model_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("MAPPING_MODEL=x:y\nOTHER=1\n", encoding="utf-8")
    assert dev_launcher.read_env_file(env) == {"MAPPING_MODEL": "x:y"}


# Taken before the prompt moved (benchmark files at commit 438553f).
BENCHMARK_V2_CREDITS_SLASH_PROMPT_SHA256 = "b17dda2d6d38573c0ac68079cae2b27c38c34d12a0f7ee65748e61219554e74c"
