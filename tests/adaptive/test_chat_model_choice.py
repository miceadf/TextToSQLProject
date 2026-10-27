"""Phase 9-08 (ADR-098): the user picks the chat model among the server's choices."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.adaptive.api.chat_models import (
    CHAT_MODELS_PATH,
    CHAT_MODELS_VERSION,
    ChatModelChoice,
    MAX_CHOICES,
    validate_choices,
)
from backend.adaptive.api.chat_query import CHAT_QUERY_PATH, register_chat_query_routes
from tests.adaptive.test_chat_query_endpoint import (
    GOOD_IR,
    QUESTION,
    FakeModel,
    RecordingExecutor,
)
from tests.adaptive.test_legacy_view_partial_mapping import DATABASE_ID, make_context


def choices(general: Any, tuned: Any) -> list[ChatModelChoice]:
    return [
        ChatModelChoice(id="general", label="일반 모델", tag="qwen2.5-coder:7b-instruct", model=general),
        ChatModelChoice(id="legacy-tuned", label="레거시 학습 모델(비교용)", tag="text2sql_qwen:latest", model=tuned),
    ]


def make_client(*, model: Any = None, models: Any = None) -> TestClient:
    app = FastAPI()
    context = make_context(executor=RecordingExecutor())
    register_chat_query_routes(app, pg_contexts={DATABASE_ID: context}, model=model, models=models)
    return TestClient(app, raise_server_exceptions=False)


def ask(client: TestClient, **extra: Any):
    return client.post(CHAT_QUERY_PATH, json={"database_id": DATABASE_ID, "query": QUESTION, **extra})


def test_the_listing_names_choices_default_first_and_no_connection_detail() -> None:
    client = make_client(models=choices(FakeModel(GOOD_IR), FakeModel(GOOD_IR)))
    response = client.get(CHAT_MODELS_PATH)
    assert response.status_code == 200
    assert response.json() == {
        "version": CHAT_MODELS_VERSION,
        "default": "general",
        "models": [
            {"id": "general", "label": "일반 모델", "tag": "qwen2.5-coder:7b-instruct"},
            {"id": "legacy-tuned", "label": "레거시 학습 모델(비교용)", "tag": "text2sql_qwen:latest"},
        ],
    }
    assert "11434" not in response.text and "http" not in response.text


def test_without_a_choice_the_default_answers_once() -> None:
    general, tuned = FakeModel(GOOD_IR), FakeModel(GOOD_IR)
    response = ask(make_client(models=choices(general, tuned)))
    assert response.status_code == 200 and response.json()["status"] == "SUCCEEDED"
    assert len(general.calls) == 1 and tuned.calls == []


def test_the_chosen_model_alone_answers_once() -> None:
    general, tuned = FakeModel(GOOD_IR), FakeModel(GOOD_IR)
    client = make_client(models=choices(general, tuned))
    response = ask(client, model="legacy-tuned")
    assert response.status_code == 200 and response.json()["status"] == "SUCCEEDED"
    assert general.calls == [] and len(tuned.calls) == 1
    # The answer's shape is unchanged: no model key is added.
    assert "model" not in response.json()


@pytest.mark.parametrize(
    "value", ["gpt-4", "text2sql_qwen:latest", "", "General", 1, None, ["general"], "a" * 40]
)
def test_a_model_the_server_does_not_offer_is_refused(value: Any) -> None:
    general, tuned = FakeModel(GOOD_IR), FakeModel(GOOD_IR)
    response = ask(make_client(models=choices(general, tuned)), model=value)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CHAT_INVALID_REQUEST"
    assert general.calls == [] and tuned.calls == []


def test_other_extra_keys_are_still_refused() -> None:
    general = FakeModel(GOOD_IR)
    response = ask(make_client(models=choices(general, FakeModel(GOOD_IR))), temperature=1)
    assert response.status_code == 400 and general.calls == []


def test_a_single_model_passed_the_old_way_is_the_only_choice() -> None:
    model = FakeModel(GOOD_IR)
    client = make_client(model=model)
    listing = client.get(CHAT_MODELS_PATH).json()
    assert listing["default"] == "default"
    assert listing["models"] == [{"id": "default", "label": "기본 모델", "tag": None}]
    assert ask(client).status_code == 200 and ask(client, model="default").status_code == 200
    assert ask(client, model="general").status_code == 400
    assert len(model.calls) == 2


def test_without_any_model_the_listing_is_empty_and_the_chat_unavailable() -> None:
    client = make_client()
    assert client.get(CHAT_MODELS_PATH).json() == {
        "version": CHAT_MODELS_VERSION, "default": None, "models": []
    }
    assert ask(client).status_code == 503


def test_model_and_models_together_are_refused() -> None:
    with pytest.raises(ValueError):
        make_client(model=FakeModel(GOOD_IR), models=choices(FakeModel(GOOD_IR), FakeModel(GOOD_IR)))


@pytest.mark.parametrize(
    "bad",
    [
        [],
        "general",
        [ChatModelChoice(id="Bad Id", label="x", tag="t", model=FakeModel())],
        [ChatModelChoice(id="general", label=" ", tag="t", model=FakeModel())],
        [ChatModelChoice(id="general", label="x", tag="tag with space", model=FakeModel())],
        [ChatModelChoice(id="general", label="x", tag="t", model=object())],
        [ChatModelChoice(id="general", label="x", tag="t", model=FakeModel())] * 2,
        [ChatModelChoice(id=f"m{index}", label="x", tag="t", model=FakeModel()) for index in range(MAX_CHOICES + 1)],
    ],
)
def test_invalid_choice_lists_are_refused(bad: Any) -> None:
    with pytest.raises(ValueError):
        validate_choices(bad)


def test_the_prototype_takes_choices_and_serves_the_default_to_clarification() -> None:
    from backend.adaptive.api.ollama_startup import (
        build_startup_ollama_model,
        parse_ollama_startup_config,
    )
    from backend.adaptive.api.app_factory import create_synthetic_clarification_prototype

    def build(tag: str, request_format: str) -> Any:
        config = parse_ollama_startup_config(tag, None, "30", request_format)
        return build_startup_ollama_model(config, transport=lambda *_args: {})

    general = build("qwen2.5-coder:7b-instruct", "model-template")
    tuned = build("text2sql_qwen:latest", "alpaca-raw-no-system-v1")
    app = create_synthetic_clarification_prototype(chat_models=choices(general, tuned))
    try:
        client = TestClient(app)
        assert [item["id"] for item in client.get(CHAT_MODELS_PATH).json()["models"]] == [
            "general", "legacy-tuned"
        ]
        assert app.state.adaptive_app_provider_kind == "ollama"
        assert app.state.adaptive_app_provider._model is general  # noqa: SLF001
    finally:
        app.state.adaptive_app_cleanup()
    with pytest.raises(RuntimeError):
        create_synthetic_clarification_prototype(ollama_model=general, chat_models=choices(general, tuned))
    with pytest.raises(RuntimeError):
        # Every choice must be a real Ollama model, not only the default.
        create_synthetic_clarification_prototype(chat_models=choices(general, FakeModel(GOOD_IR)))


# --- the general model's output format (queryir-schema-v1) -------------------------------------

IR_TEXT = '{"entity":"Course","select":["Course.name"],"filters":[],"limit":50}'


def _schema_model(output_format: str | None, sent: list[dict[str, Any]], output: str = IR_TEXT) -> Any:
    from backend.adaptive.harness import OllamaQueryModel

    def transport(_url: str, payload: dict[str, Any], _timeout: float) -> Any:
        sent.append(payload)
        return {"response": output}

    return OllamaQueryModel(
        "http://127.0.0.1:11434", "qwen2.5-coder:7b-instruct", 30,
        transport=transport, output_format=output_format,
    )


def _case() -> Any:
    from tests.adaptive.test_ollama_query_model import OllamaQueryModelTests

    case = OllamaQueryModelTests()
    case.setUp()
    return case


def test_the_general_model_is_sent_the_databases_queryir_schema() -> None:
    case = _case()
    sent: list[dict[str, Any]] = []
    generation = _schema_model("queryir-schema-v1", sent).generate_observed(case.request, case.ontology, case.mapping)
    assert generation.json_parsed is True and generation.value["entity"] == "Course"
    [payload] = sent
    schema = payload["format"]
    assert schema["required"] == ["entity", "select", "filters", "limit"]
    properties = schema["properties"]
    assert properties["entity"]["enum"] == ["Course", "Schedule"]
    # Every field the prompt lists, mapped or not: an unmapped one must stay
    # nameable so the chat can refuse it as unsupported.
    assert properties["select"]["items"]["enum"] == ["Course.credits", "Course.name"]
    # One shape per value type: an integer field takes a number, never text.
    integer, text = [shape["properties"] for shape in properties["filters"]["items"]["anyOf"]]
    assert integer["field"]["enum"] == ["Course.credits"] and integer["value"] == {"type": "integer"}
    assert integer["operator"]["enum"] == ["greater_than"]
    assert text["field"]["enum"] == ["Course.name"] and text["value"] == {"type": "string"}
    assert text["operator"]["enum"] == ["contains", "equals"]
    # Luna 9-08 review: select is unique as QueryIR requires. The filter bound
    # is far above a stated question (6 could cut one); without any bound a
    # repeating generation ran to the timeout.
    assert properties["select"]["uniqueItems"] is True
    assert properties["filters"]["maxItems"] == 12
    assert properties["limit"] == {"type": "integer", "minimum": 1, "maximum": 200}
    # No physical name reaches the request.
    assert "PRIVATE" not in json.dumps(payload, ensure_ascii=False)


def test_the_fine_tuned_models_request_is_unchanged() -> None:
    case = _case()
    plain: list[dict[str, Any]] = []
    declared: list[dict[str, Any]] = []
    _schema_model(None, plain).generate_observed(case.request, case.ontology, case.mapping)
    _schema_model("none", declared).generate_observed(case.request, case.ontology, case.mapping)
    assert "format" not in plain[0] and plain == declared


def test_a_database_with_no_mapped_field_gets_no_schema() -> None:
    from backend.adaptive.harness import MappingProfile, MappingStatus
    from backend.adaptive.harness.ollama import queryir_schema

    case = _case()
    empty = MappingProfile(database_id="synthetic-db", version=1, status=MappingStatus.PUBLISHED, fields={})
    assert queryir_schema(case.ontology, empty) is None


def test_the_schema_is_shape_only_and_validation_still_decides() -> None:
    # A schema-shaped answer naming a field the database does not map is
    # passed on unrepaired; the chat's checks answer it as unsupported.
    case = _case()
    odd = '{"entity":"Course","select":["Course.credits"],"filters":[],"limit":50}'
    generation = _schema_model("queryir-schema-v1", [], odd).generate_observed(case.request, case.ontology, case.mapping)
    assert generation.value["select"] == ["Course.credits"]  # passed on as is, not repaired


def test_unknown_output_formats_are_refused() -> None:
    from backend.adaptive.api.ollama_startup import OllamaStartupConfigError, parse_ollama_startup_config

    with pytest.raises(ValueError):
        _schema_model("loose-json", [])
    with pytest.raises(OllamaStartupConfigError):
        parse_ollama_startup_config("qwen2.5-coder:7b-instruct", None, "30", "model-template", "loose")
    config = parse_ollama_startup_config(
        "qwen2.5-coder:7b-instruct", None, "30", "model-template", "queryir-schema-v1"
    )
    assert config.output_format == "queryir-schema-v1"
    assert parse_ollama_startup_config("text2sql_qwen", None, "30").output_format == "none"


def test_the_declared_provenance_only_changes_when_the_schema_is_used() -> None:
    from backend.adaptive.harness import OllamaQueryModel

    def provenance(output_format: str | None) -> str:
        model = OllamaQueryModel(
            "http://127.0.0.1:11434", "m", 30, transport=lambda *_args: {}, output_format=output_format
        )
        return model.declared_provenance("0.1").generation_config_sha256

    assert provenance(None) == provenance("none")
    assert provenance("queryir-schema-v1") != provenance(None)


def test_prompt_v3_is_v2_plus_the_two_instructions_and_only_for_the_general_model() -> None:
    from backend.adaptive.api.ollama_startup import OllamaStartupConfigError, parse_ollama_startup_config
    from backend.adaptive.harness import ollama

    v2 = ollama._versioned_template("query-ir-v2")  # noqa: SLF001
    v3 = ollama._versioned_template("query-ir-v3")  # noqa: SLF001
    assert "Add a filter only for a condition the question states" in v3
    assert "use each hint's field and value for its text" in v3
    assert "Add a filter only" not in v2 and "Add a filter only" not in ollama._versioned_template("query-ir-v1")  # noqa: SLF001
    assert v3.replace("query-ir-v3", "query-ir-v2").startswith(v2.split("Return JSON only;")[0])
    assert parse_ollama_startup_config("m", None, "30", prompt_version="query-ir-v3").prompt_version == "query-ir-v3"
    assert parse_ollama_startup_config("m", None, "30").prompt_version == "query-ir-v1"
    with pytest.raises(OllamaStartupConfigError):
        parse_ollama_startup_config("m", None, "30", prompt_version="query-ir-v9")


def test_every_data_type_has_the_validators_json_type() -> None:
    from backend.adaptive.harness import DataType
    from backend.adaptive.harness.ollama import _JSON_TYPES

    assert set(_JSON_TYPES) == set(DataType)
