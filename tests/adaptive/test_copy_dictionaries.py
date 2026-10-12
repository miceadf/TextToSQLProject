"""Phase 9-20: copy reviewed dictionaries using temporary repositories and fake readers."""
from types import SimpleNamespace

import pytest

from backend.adaptive.mapping.values import ValueDictionary, ValueEntry
from backend.adaptive.registration import postgres_dictionaries as store
from backend.adaptive.registration import postgres_registrations as pg
from tests.adaptive.test_postgres_registration import DB_ID, _register, _review, _review_all, client_repo  # noqa: F401
from tests.adaptive.test_value_dictionary_editor import DICTIONARIES, _set, stored_values  # noqa: F401

SOURCES = DICTIONARIES + "/sources"


def ready(client):
    response = _register(client)
    assert response.status_code == 200, response.text
    _review_all(client)


def profile(app, fields, database_id="legacy-view"):
    dictionaries = {name: ValueDictionary(entries=tuple(entries), reviewer="source", reason="fixture")
                    for name, entries in fields.items()}
    context = SimpleNamespace(label="Legacy fixture", value_dictionaries=lambda: dictionaries)
    dict.__setitem__(app.state.postgres_registration_service._live, database_id, context)


def copy(client, source="legacy-view", **overrides):
    return client.post(DICTIONARIES, json={"action": "COPY", "database_id": DB_ID,
                       "source_database_id": source, "reviewer": "admin", **overrides})


def entry(stored, label=None, aliases=()):
    return ValueEntry(stored=stored, label=label or stored, aliases=aliases)


@pytest.mark.parametrize("source", ["legacy-view", "legacy-base"])
def test_profile_source_merges_aliases_and_keeps_target_label_without_publishing(client_repo, source):
    client, repo, app, _executor = client_repo
    ready(client)
    assert _set(client, "Department.name", "공과대학 컴퓨터융합학부", ["컴융"], "대상 뜻").status_code == 200
    profile(app, {"Department.name": [entry("공과대학 컴퓨터융합학부", "출처 뜻", ("컴융", "컴융부"))]}, source)
    listing = client.get(SOURCES)
    assert listing.status_code == 200
    assert listing.json()["sources"] == [{"database_id": source, "label": "Legacy fixture", "fields": {"Department.name": 1}}]
    assert "컴융" not in listing.text
    response = copy(client, source)
    assert response.status_code == 200, response.text
    assert response.json()["fields"] == [{"field": "Department.name", "label": "학과명", "imported": 1, "skipped": 0, "reasons": {}}]
    saved = store.load_entries(repository_root=repo, database_id=DB_ID)["Department.name"][0]
    assert saved.label == "대상 뜻" and saved.aliases == ("컴융", "컴융부")
    assert pg.load_published(repository_root=repo, database_id=DB_ID) is None
    assert DB_ID not in app.state.postgres_registration_service._live
    assert not (repo / ".schema-adaptive" / "postgres-profiles" / DB_ID).exists()


def test_published_registration_source_and_target_remain_unpublished_edits(client_repo):
    client, repo, _app, _executor = client_repo
    ready(client)
    source = "source-school"
    assert _register(client, source).status_code == 200
    _review_all(client, source)
    store._save(repo, source, "source", {"Course.category": [entry("교양", aliases=("교양과목",))]})
    assert client.get(SOURCES).json()["sources"] == []  # pending registrations cannot be sources
    published = _review(client, {"action": "PUBLISH", "database_id": source, "published_by": "admin"})
    assert published.status_code == 200, published.text
    previous = pg.load_published(repository_root=repo, database_id=source)
    assert client.get(SOURCES).json()["sources"][0]["fields"] == {"Course.category": 1}
    response = copy(client, source)
    assert response.status_code == 200, response.text
    assert response.json()["fields"][0]["imported"] == 1
    assert store.load_entries(repository_root=repo, database_id=DB_ID)["Course.category"][0].aliases == ("교양과목",)
    assert pg.load_published(repository_root=repo, database_id=DB_ID) is None
    assert pg.load_published(repository_root=repo, database_id=source) == previous


def test_missing_values_and_unmapped_ignored_retired_fields_are_skipped(client_repo):
    client, repo, app, _executor = client_repo
    ready(client)
    ignored = _review(client, {"action": "IGNORE", "database_id": DB_ID, "semantic_field": "Department.name",
                             "reviewer": "admin", "reason": "이 DB에 없음"})
    assert ignored.status_code == 200
    profile(app, {"Course.category": [entry("교양"), entry("없는 값")],
                  "Department.name": [entry("공과대학 컴퓨터융합학부")],
                  "CourseOffering.year": [entry("2026")], "Department.code": [entry("01")]})
    response = copy(client)
    assert response.status_code == 200, response.text
    fields = {field["field"]: field for field in response.json()["fields"]}
    assert fields["Course.category"]["imported"] == 1
    assert fields["Course.category"]["reasons"] == {"VALUE_NOT_PRESENT": 1}
    for name in ("Department.name", "CourseOffering.year", "Department.code"):
        assert fields[name]["reasons"] == {"FIELD_NOT_EDITABLE": 1}
    assert set(store.load_entries(repository_root=repo, database_id=DB_ID)) == {"Course.category"}


@pytest.mark.parametrize("alias,reason", [("교양", "ALIAS_CONFLICT"), ("x", "ALIAS_LENGTH")])
def test_target_alias_checks_are_reused(client_repo, alias, reason):
    client, repo, app, _executor = client_repo
    ready(client)
    profile(app, {"Department.name": [entry("공과대학 컴퓨터융합학부", aliases=(alias,))]})
    response = copy(client)
    assert response.status_code == 200
    assert response.json()["fields"][0]["reasons"] == {reason: 1}
    assert store.load_entries(repository_root=repo, database_id=DB_ID) == {}


def test_copied_entries_are_checked_against_each_other(client_repo):
    client, repo, app, _executor = client_repo
    ready(client)
    profile(app, {"Course.category": [entry("교양", aliases=("공통약어",))],
                  "Department.name": [entry("공과대학 컴퓨터융합학부", aliases=("공통약어",))]})
    response = copy(client)
    assert response.status_code == 200
    assert response.json()["fields"][1]["reasons"] == {"ALIAS_CONFLICT": 1}
    assert set(store.load_entries(repository_root=repo, database_id=DB_ID)) == {"Course.category"}


def test_large_field_uses_exact_presence_and_failed_reader_never_imports(client_repo, monkeypatch):
    client, repo, app, _executor = client_repo
    ready(client)
    profile(app, {"Department.name": [entry("공과대학 컴퓨터융합학부"), entry("없는 값")]})
    service = app.state.postgres_registration_service
    reader = SimpleNamespace(read=lambda *_args: None, present=lambda *_args: ["공과대학 컴퓨터융합학부"])
    monkeypatch.setattr(service, "_local_reader", lambda *_args: reader)
    result = copy(client)
    assert result.status_code == 200
    assert result.json()["fields"][0]["reasons"] == {"VALUE_NOT_PRESENT": 1}
    reader.present = lambda *_args: None
    result = copy(client)
    assert result.json()["fields"][0]["reasons"] == {"VALUES_UNAVAILABLE": 2}
    assert len(store.load_entries(repository_root=repo, database_id=DB_ID)["Department.name"]) == 1


def test_pending_mapping_cannot_receive_entries(client_repo):
    client, repo, app, _executor = client_repo
    response = _register(client)
    assert response.status_code == 200, response.text
    assert _review(client, {"action": "INIT", "database_id": DB_ID, "reviewer": "admin"}).status_code == 200
    profile(app, {"Department.name": [entry("공과대학 컴퓨터융합학부")]})
    response = copy(client)
    assert response.status_code == 200
    assert response.json()["fields"][0]["reasons"] == {"FIELD_NOT_EDITABLE": 1}
    assert store.load_entries(repository_root=repo, database_id=DB_ID) == {}


def test_copy_into_published_target_changes_only_draft(client_repo):
    client, repo, app, _executor = client_repo
    ready(client)
    published = _review(client, {"action": "PUBLISH", "database_id": DB_ID, "published_by": "admin"})
    assert published.status_code == 200, published.text
    folder = repo / ".schema-adaptive" / "postgres-profiles" / DB_ID
    before = {name: (folder / name).read_bytes() for name in ("mapping.json", "profile.json")}
    record = pg.load_published(repository_root=repo, database_id=DB_ID)
    live = app.state.postgres_registration_service._live[DB_ID]
    profile(app, {"Department.name": [entry("공과대학 컴퓨터융합학부", aliases=("컴융",))]})
    assert copy(client).status_code == 200
    assert store.load_entries(repository_root=repo, database_id=DB_ID)["Department.name"][0].aliases == ("컴융",)
    assert pg.load_published(repository_root=repo, database_id=DB_ID) == record
    assert app.state.postgres_registration_service._live[DB_ID] is live
    assert live.value_dictionaries() == {}
    assert {name: (folder / name).read_bytes() for name in before} == before


@pytest.mark.parametrize("overrides", [{"source_database_id": "missing"}, {"source_database_id": DB_ID},
    {"source_database_id": "../legacy-view"}, {"source_database_id": []}, {"reviewer": " "},
    {"reviewer": "a" * 65}, {"extra": 1}])
def test_invalid_copies_do_not_write(client_repo, overrides):
    client, repo, app, _executor = client_repo
    ready(client)
    profile(app, {"Course.category": [entry("교양")]})
    assert copy(client, **overrides).status_code == 400
    assert store.load_entries(repository_root=repo, database_id=DB_ID) == {}
