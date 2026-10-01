"""Phase 8-19: per-database extension fields declared by administrators."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.adaptive.api.chat_query import _runtime_ontology, answer_chat_query
from backend.adaptive.api.postgres_runtime import PostgresRuntimeError
from backend.adaptive.api.value_hints import hint_fields
from backend.adaptive.harness.ollama import _prompt
from backend.adaptive.mapping.extensions import (
    ExtensionField,
    column_fits,
    dto_key,
    operators_for,
    valid_extension,
)
from backend.adaptive.ontology import Operator, load_default_ontology
from tests.adaptive import test_legacy_view_partial_mapping as view
from tests.adaptive import test_postgres_registration as base
from tests.adaptive.test_chat_query_endpoint import FakeModel
from tests.adaptive.test_postgres_registration import DB_ID, _register, _review, _review_all, client_repo  # noqa: F401

EXTENSIONS = "/api/adaptive/v1/postgres/extensions"


@pytest.fixture(autouse=True)
def extra_columns(monkeypatch: pytest.MonkeyPatch):
    columns = {**view.VIEW_COLUMNS, "classroom": "character varying(20)", "class_mode": "character varying(20)"}
    monkeypatch.setattr(view, "VIEW_COLUMNS", columns)
    monkeypatch.setattr(base, "VALUES", {**base.VALUES, "classroom": ["공5-301", "교111"], "class_mode": ["실시간"]})


def _set(client: Any, key: str = "classroom", **overrides: Any) -> Any:
    body = {
        "action": "SET", "database_id": DB_ID, "key": key, "label": "강의실", "entity": "Schedule",
        "table": view.VIEW, "column": "classroom", "data_type": "string", "reviewer": "admin",
    }
    body.update(overrides)
    return client.post(EXTENSIONS, json=body)


def _ready(client: Any) -> None:
    assert _register(client).status_code == 200
    _review_all(client)


# --- rules ------------------------------------------------------------------------------------


def test_names_types_and_operators() -> None:
    assert valid_extension("Schedule.ext_classroom", ExtensionField("강의실", "string"))
    for name in ("Schedule.classroom", "Room.ext_x", "Schedule.ext_", "Schedule.ext_Room", "Course.name"):
        assert not valid_extension(name, ExtensionField("강의실", "string"))
    assert not valid_extension("Schedule.ext_room", ExtensionField("", "string"))
    assert not valid_extension("Schedule.ext_room", ExtensionField("강의실", "date"))
    assert dto_key("Schedule.ext_classroom") == "ext_classroom"
    assert operators_for("string") == {Operator.EQUALS, Operator.CONTAINS}
    assert Operator.CONTAINS not in operators_for("integer")
    assert column_fits("string", "character varying(20)") and not column_fits("string", "integer")
    assert column_fits("integer", "integer") and not column_fits("integer", "numeric")
    assert column_fits("number", "numeric(3,1)") and column_fits("number", "integer")


# --- API --------------------------------------------------------------------------------------


def test_declare_list_and_remove(client_repo) -> None:  # noqa: F811
    client, repo, _app, _executor = client_repo
    _ready(client)
    listed = client.get(EXTENSIONS, params={"database_id": DB_ID}).json()
    assert listed["fields"] == [] and any(table["name"] == view.VIEW for table in listed["tables"])
    assert "s3cret" not in json.dumps(listed)
    added = _set(client)
    assert added.status_code == 200
    assert added.json()["fields"] == [{
        "key": "classroom", "label": "강의실", "entity": "Schedule", "table": view.VIEW,
        "column": "classroom", "data_type": "string",
    }]
    stored = repo / ".schema-adaptive" / "postgres-registrations" / DB_ID / "extensions.json"
    assert stored.exists()
    removed = client.post(EXTENSIONS, json={"action": "REMOVE", "database_id": DB_ID, "key": "classroom", "reviewer": "admin"})
    assert removed.status_code == 200 and removed.json()["fields"] == []


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"key": "Room"}, "POSTGRES_REGISTRATION_INVALID_INPUT"),
        ({"label": ""}, "POSTGRES_REGISTRATION_INVALID_INPUT"),
        ({"entity": "Room"}, "POSTGRES_REGISTRATION_INVALID_INPUT"),
        ({"data_type": "integer"}, "POSTGRES_REGISTRATION_INVALID_INPUT"),  # a text column
        ({"column": "no_such"}, "POSTGRES_REGISTRATION_INVALID_INPUT"),
        ({"column": "subject_name"}, "POSTGRES_REGISTRATION_EXTENSION_CONFLICT"),  # given to Course.name
    ],
)
def test_bad_declarations_are_refused(client_repo, overrides: dict[str, Any], code: str) -> None:  # noqa: F811
    client, _repo, _app, _executor = client_repo
    _ready(client)
    response = _set(client, **overrides)
    assert response.json() == {"error": {"code": code}}


def test_one_column_and_one_label_per_field(client_repo) -> None:  # noqa: F811
    client, _repo, _app, _executor = client_repo
    _ready(client)
    assert _set(client).status_code == 200
    assert _set(client, key="room2").json()["error"]["code"] == "POSTGRES_REGISTRATION_EXTENSION_CONFLICT"
    assert _set(client, key="mode", column="class_mode").json()["error"]["code"] == "POSTGRES_REGISTRATION_EXTENSION_CONFLICT"
    assert _set(client, key="mode", column="class_mode", label="수업방식", entity="CourseOffering").status_code == 200


# --- publish, runtime and chat ---------------------------------------------------------------


def test_publish_carries_the_extension_into_the_chat(client_repo) -> None:  # noqa: F811
    client, repo, app, executor = client_repo
    _ready(client)
    assert _set(client).status_code == 200
    published = _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"})
    assert published.status_code == 200, published.text[:300]
    profile = json.loads((repo / ".schema-adaptive" / "postgres-profiles" / DB_ID / "profile.json").read_text(encoding="utf-8"))
    assert profile["version"] == "postgres-runtime-profile-v5"
    assert profile["extension_fields"] == {"Schedule.ext_classroom": {"label": "강의실", "data_type": "string"}}
    mapping = json.loads((repo / ".schema-adaptive" / "postgres-profiles" / DB_ID / "mapping.json").read_text(encoding="utf-8"))
    assert mapping["fields"]["Schedule.ext_classroom"]["column"] == "classroom"
    context = app.state.synthetic_postgres_contexts[DB_ID]
    assert context.extension_fields() == {"Schedule.ext_classroom": ExtensionField("강의실", "string")}

    model = FakeModel({
        "entity": "Course", "select": ["Course.name"],
        "filters": [{"field": "Schedule.ext_classroom", "operator": "equals", "value": "301"}], "limit": 50,
    })
    answer = answer_chat_query(database_id=DB_ID, question="301호 수업", pg_contexts={DB_ID: context}, model=model)
    assert answer["status"] == "SUCCEEDED"
    assert answer["columns"][-1] == "ext_classroom"
    assert answer["extensions"] == [{"key": "ext_classroom", "label": "강의실"}]
    assert answer["understood"]["filters"] == [
        {"field": "Schedule.ext_classroom", "label": "강의실", "operator": "contains", "value": "301"}
    ]
    sql = executor.queries[-1].sql
    assert '"classroom"' in sql and "LIKE" in sql

    # A startup reload reads the v5 profile back.
    from backend.adaptive.api.postgres_startup_profile import load_postgres_startup_profiles

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(profile["dsn_env"], base.SECRET_DSN)
        [loaded] = load_postgres_startup_profiles(repository_root=str(repo), database_ids=[DB_ID])
    assert loaded.extension_fields == {"Schedule.ext_classroom": ExtensionField("강의실", "string")}


def _profile(extensions: dict[str, ExtensionField], *, unsupported: tuple[str, ...] | None = None) -> Any:
    from backend.adaptive.api import legacy_view_profile as lvp
    from backend.adaptive.api.postgres_runtime import PostgresRuntimeProfile
    from backend.adaptive.catalog.postgres import PostgresConnectionConfig

    fields = {name: column for name, (column, _) in lvp.VIEW_FIELDS.items()}
    fields["Schedule.ext_classroom"] = "classroom"

    return PostgresRuntimeProfile(
        database_id=view.DATABASE_ID,
        label=lvp.LABEL,
        connection_config=PostgresConnectionConfig(view.SECRET_DSN, include_views=True),
        published_mapping=view.make_published(fields),
        field_transforms=dict(lvp.FIELD_TRANSFORMS),
        unsupported_fields=lvp.UNSUPPORTED_FIELDS if unsupported is None else unsupported,
        extension_fields=extensions,
    )


def _context(profile: Any) -> Any:
    from backend.adaptive.api.postgres_runtime import build_validated_contexts

    return build_validated_contexts(
        [profile],
        adapters_by_id={view.DATABASE_ID: view.FakeAdapter(view.make_catalog())},
        executors_by_id={view.DATABASE_ID: view.FakeExecutor()},
        expected_ontology_version=load_default_ontology().version,
    )[view.DATABASE_ID]


def test_the_runtime_checks_extension_fields() -> None:
    assert _context(_profile({"Schedule.ext_classroom": ExtensionField("강의실", "string")})).extension_fields()
    with pytest.raises(PostgresRuntimeError):  # mapped but undeclared: not a common field either
        _context(_profile({}))
    with pytest.raises(PostgresRuntimeError):  # a text column declared as a number
        _context(_profile({"Schedule.ext_classroom": ExtensionField("강의실", "integer")}))
    with pytest.raises(PostgresRuntimeError):  # declared but not mapped
        _context(_profile({
            "Schedule.ext_classroom": ExtensionField("강의실", "string"),
            "Schedule.ext_other": ExtensionField("기타", "string"),
        }))
    with pytest.raises(PostgresRuntimeError):
        from backend.adaptive.api import legacy_view_profile as lvp

        _context(_profile(
            {"Schedule.ext_classroom": ExtensionField("강의실", "string")},
            unsupported=(*lvp.UNSUPPORTED_FIELDS, "Schedule.ext_classroom"),
        ))


def test_only_extension_fields_carry_a_label_into_the_prompt() -> None:
    from backend.adaptive.harness.contracts import MappingProfile

    plain, _ = _runtime_ontology()
    extended, labels = _runtime_ontology({"Schedule.ext_classroom": ExtensionField("강의실", "string")})
    assert labels["Schedule.ext_classroom"] == "강의실"
    mapping = MappingProfile.model_validate({"database_id": "db", "version": 1, "status": "PUBLISHED", "fields": {}})
    plain_prompt = _prompt("q", plain, mapping, "query-ir-v1")
    assert '"label"' not in plain_prompt
    assert '"label":"강의실"' in _prompt("q", extended, mapping, "query-ir-v1")


def test_text_extension_fields_get_value_hints() -> None:
    context = _context(_profile({"Schedule.ext_classroom": ExtensionField("강의실", "string")}))
    assert "Schedule.ext_classroom" in hint_fields(context, load_default_ontology())


def test_capture_targets_accept_the_optional_label() -> None:
    from backend.adaptive.harness.contracts import MappingProfile, Ontology, OntologyField
    from backend.adaptive.harness.live_capture import _validate_shape

    ontology = Ontology(entities={"Schedule"}, fields={
        "Schedule.day": OntologyField(name="Schedule.day", data_type="string", allowed_operators={Operator.EQUALS}),
        "Schedule.ext_room": OntologyField(
            name="Schedule.ext_room", data_type="string", allowed_operators={Operator.EQUALS}, label="강의실"
        ),
    })
    target = {
        "request": {"request_id": "r1", "database_id": "db", "question": "q"},
        "ontology": json.loads(ontology.model_dump_json()),
        "mapping": json.loads(MappingProfile.model_validate(
            {"database_id": "db", "version": 1, "status": "PUBLISHED", "fields": {}}).model_dump_json()),
    }
    target["ontology"]["entities"] = sorted(target["ontology"]["entities"])
    for field in target["ontology"]["fields"].values():
        field["allowed_operators"] = sorted(field["allowed_operators"])
    assert _validate_shape(target)
    target["ontology"]["fields"]["Schedule.day"]["label"] = 3
    assert not _validate_shape(target)


def test_a_malformed_data_type_is_invalid_input_not_a_server_error(client_repo) -> None:  # noqa: F811
    client, _repo, _app, _executor = client_repo
    _ready(client)
    for overrides in ({"data_type": []}, {"entity": ["Schedule"]}, {"key": 3}):
        response = _set(client, **overrides)
        assert response.status_code == 400, overrides
        assert response.json() == {"error": {"code": "POSTGRES_REGISTRATION_INVALID_INPUT"}}
