"""Server-owned PostgreSQL runtime registry (Phase 7-04, synthetic-only).

Backend assembly only. No UI, Ollama, real school DB, Docker, or live
PostgreSQL server is used here. Profiles can only be built in Python
server code; HTTP requests cannot inject profiles, DSN, schema,
mapping, catalog, compiler, policy, or executor.

All failures use fixed redacted messages. DSN, host, port, user,
password, schema, physical table/column, SQL, bound parameters, and
driver exception text never appear in responses, exceptions, repr,
logs, or app public state.
"""

from __future__ import annotations

import copy
import re
import threading
from typing import Any

from ..catalog.postgres import PostgresConnectionConfig
from ..harness.contracts import HarnessErrorCode, HarnessStage, MappingStatus
from ..harness.errors import HarnessFailure
from ..mapping.bridge import to_harness_mapping
from ..mapping.contracts import PublishedMappingProfile
from ..mapping.transforms import (
    FieldTransform,
    column_type_supports,
    normalize_transformed_value,
    transform_supports_data_type,
)
from ..mapping.extensions import MAX_EXTENSIONS, ExtensionField, column_fits, valid_extension
from ..mapping.values import ValueDictionary

POSTGRES_SCHEMA_VALUE = "postgres"

_DB_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _().-]{0,63}$")
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_FIXED_RUNTIME_ERROR = "postgres runtime unavailable"
_FIXED_STALE_MESSAGE = "published mapping is stale"
_FIXED_COMPILE_MESSAGE = "PostgreSQL query compilation failed"
_FIXED_POLICY_MESSAGE = "PostgreSQL query policy rejected query"
_FIXED_EXECUTION_MESSAGE = "PostgreSQL query execution failed"

__all__ = [
    "POSTGRES_SCHEMA_VALUE",
    "PostgresRuntimeProfile",
    "PostgresRuntimeError",
    "JoinPathError",
    "ValidatedPostgresContext",
    "DatabaseRoutingCompiler",
    "DatabaseRoutingPolicy",
    "DatabaseRoutingExecutor",
    "build_validated_contexts",
    "is_postgres_context_current",
]


class PostgresRuntimeError(Exception):
    """Fixed-message runtime failure (no sensitive details)."""

    def __init__(self) -> None:
        super().__init__(_FIXED_RUNTIME_ERROR)


class JoinPathError(PostgresRuntimeError):
    """Ambiguous anchored join plan, attributed only to semantic fields."""

    def __init__(self, fields: tuple[str, ...]) -> None:
        super().__init__()
        self.fields = fields


def _valid_database_id(value: Any) -> bool:
    return isinstance(value, str) and _DB_ID_PATTERN.fullmatch(value) is not None


def _valid_label(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 64
        and _LABEL_PATTERN.fullmatch(value) is not None
    )


def _copy_connection_config(
    config: PostgresConnectionConfig,
) -> PostgresConnectionConfig:
    try:
        return PostgresConnectionConfig(
            config._dsn,  # noqa: SLF001 - private server-owned snapshot
            schema=config.schema,
            connect_timeout_seconds=config.connect_timeout_seconds,
            statement_timeout_ms=config.statement_timeout_ms,
            include_views=config.include_views,
        )
    except Exception:
        raise PostgresRuntimeError() from None


class PostgresRuntimeProfile:
    """Strict immutable server-owned PostgreSQL profile.

    Only constructible in Python server code. Holds one database ID,
    one safe label, one connection config, and one explicitly approved
    published mapping. Immutable and redacted: repr/str never render
    DSN, schema, or physical identifiers.

    Phase 7-12: a profile may declare canonical fields the database cannot
    answer (``unsupported_fields``) and closed-list value transforms. With
    the defaults the full canonical field set is still required.

    Phase 7-17: a profile may declare a row ``anchor_table``; every JOIN is
    then a LEFT JOIN from that table.

    Phase 8-06: a profile may declare reviewed per-field value dictionaries.

    Phase 8-19: a profile may declare this database's extension fields.
    """

    __slots__ = (
        "_database_id",
        "_label",
        "_connection_config",
        "_published_mapping",
        "_field_transforms",
        "_unsupported_fields",
        "_anchor_table",
        "_value_dictionaries",
        "_extension_fields",
    )

    def __init__(
        self,
        *,
        database_id: str,
        label: str,
        connection_config: PostgresConnectionConfig,
        published_mapping: PublishedMappingProfile,
        field_transforms: dict[str, FieldTransform] | None = None,
        unsupported_fields: tuple[str, ...] | list[str] = (),
        anchor_table: str | None = None,
        value_dictionaries: dict[str, ValueDictionary] | None = None,
        extension_fields: dict[str, ExtensionField] | None = None,
    ) -> None:
        if not _valid_database_id(database_id):
            raise PostgresRuntimeError()
        extensions = _copy_extensions(extension_fields)
        transforms = _copy_transforms(field_transforms)
        unsupported = _copy_unsupported(unsupported_fields)
        anchor = _copy_anchor(anchor_table)
        dictionaries = _copy_dictionaries(value_dictionaries)
        if not _valid_label(label):
            raise PostgresRuntimeError()
        if not isinstance(connection_config, PostgresConnectionConfig):
            raise PostgresRuntimeError()
        if not isinstance(published_mapping, PublishedMappingProfile):
            raise PostgresRuntimeError()
        try:
            snapshot = PublishedMappingProfile.model_validate(
                published_mapping.model_dump(mode="json")
            )
        except Exception:
            raise PostgresRuntimeError() from None
        if snapshot.database_id != database_id:
            raise PostgresRuntimeError()
        object.__setattr__(self, "_database_id", database_id)
        object.__setattr__(self, "_label", label)
        object.__setattr__(
            self, "_connection_config", _copy_connection_config(connection_config)
        )
        object.__setattr__(self, "_published_mapping", snapshot)
        object.__setattr__(self, "_field_transforms", transforms)
        object.__setattr__(self, "_unsupported_fields", unsupported)
        object.__setattr__(self, "_anchor_table", anchor)
        object.__setattr__(self, "_value_dictionaries", dictionaries)
        object.__setattr__(self, "_extension_fields", extensions)

    def __setattr__(self, name: str, value: Any) -> None:
        raise PostgresRuntimeError()

    def __delattr__(self, name: str) -> None:
        raise PostgresRuntimeError()

    @property
    def database_id(self) -> str:
        return self._database_id

    @property
    def label(self) -> str:
        return self._label

    @property
    def extension_fields(self) -> dict[str, ExtensionField]:
        return dict(self._extension_fields)

    @property
    def field_transforms(self) -> dict[str, FieldTransform]:
        return dict(self._field_transforms)

    @property
    def unsupported_fields(self) -> tuple[str, ...]:
        return self._unsupported_fields

    @property
    def anchor_table(self) -> str | None:
        return self._anchor_table

    @property
    def value_dictionaries(self) -> dict[str, ValueDictionary]:
        return dict(self._value_dictionaries)

    @property
    def connection_config(self) -> PostgresConnectionConfig:
        return _copy_connection_config(self._connection_config)

    @property
    def published_mapping(self) -> PublishedMappingProfile:
        return self._published_mapping.model_copy(deep=True)

    def _redacted(self) -> str:
        return (
            "PostgresRuntimeProfile(database_id='"
            + self._database_id
            + "', label='"
            + self._label
            + "')"
        )

    def __repr__(self) -> str:
        return self._redacted()

    def __str__(self) -> str:
        return self._redacted()


def _copy_extensions(value: Any) -> dict[str, ExtensionField]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_EXTENSIONS:
        raise PostgresRuntimeError()
    copied: dict[str, ExtensionField] = {}
    for name, field in value.items():
        if not valid_extension(name, field):
            raise PostgresRuntimeError()
        copied[name] = ExtensionField(label=field.label, data_type=field.data_type)
    return copied


def _copy_transforms(value: Any) -> dict[str, FieldTransform]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PostgresRuntimeError()
    copied: dict[str, FieldTransform] = {}
    try:
        for field, transform in value.items():
            if not isinstance(field, str) or not field:
                raise PostgresRuntimeError()
            copied[field] = FieldTransform(transform)
    except PostgresRuntimeError:
        raise
    except Exception:
        raise PostgresRuntimeError() from None
    return copied


def _copy_unsupported(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise PostgresRuntimeError()
    if any(not isinstance(item, str) or not item for item in value):
        raise PostgresRuntimeError()
    if len(set(value)) != len(value):
        raise PostgresRuntimeError()
    return tuple(sorted(value))


def _copy_anchor(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 63 or value != value.strip():
        raise PostgresRuntimeError()
    return value


def _copy_dictionaries(value: Any) -> dict[str, ValueDictionary]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PostgresRuntimeError()
    copied: dict[str, ValueDictionary] = {}
    try:
        for field, dictionary in value.items():
            if not isinstance(field, str) or not field:
                raise PostgresRuntimeError()
            copied[field] = ValueDictionary.model_validate(
                dictionary.model_dump(mode="json")
                if isinstance(dictionary, ValueDictionary)
                else dictionary
            )
    except PostgresRuntimeError:
        raise
    except Exception:
        raise PostgresRuntimeError() from None
    return copied


# Exact built-in text types (optionally length-bounded); a prefix test would
# also admit user-defined types such as ``textual_state``.
_TEXT_TYPE_PATTERN = re.compile(
    r"^(?:character varying|varchar|character|char|bpchar|text)(?:\(\d{1,5}\))?$"
)
# Phase 8-21: period numbers.
# Restrict period-number time mappings to built-in integer column types.
_INTEGER_TYPE_PATTERN = re.compile(r"^(?:integer|int|int2|int4|int8|smallint|bigint)$")
# Values that identify a row in results (the chat's course key and codes)
# must reach the user unchanged, so they cannot carry a value dictionary.
_IDENTITY_FIELDS = frozenset({"Course.code", "CourseOffering.section", "Department.code"})


# Upper bound on mapped tables for the startup check that every table subset
# has exactly one anchored JOIN plan (2**8 - 1 plans at most).
_MAX_ANCHORED_TABLES = 8


def _check_anchored_plans(graph: Any, published: PublishedMappingProfile, anchor: str) -> None:
    """Every combination of mapped tables must resolve to one plan from the anchor."""
    from itertools import combinations

    from ..relations.contracts import JoinPlanStatus
    from ..relations.planner import plan_join_path

    fields_by_table: dict[str, str] = {}
    for semantic in sorted(published.fields):
        fields_by_table.setdefault(published.fields[semantic].table, semantic)
    if anchor not in fields_by_table or len(fields_by_table) > _MAX_ANCHORED_TABLES:
        raise PostgresRuntimeError()
    tables = sorted(fields_by_table)
    failing_subsets: list[tuple[str, ...]] = []
    for size in range(1, len(tables) + 1):
        for subset in combinations(tables, size):
            result = plan_join_path(
                graph,
                published,
                [fields_by_table[table] for table in subset],
                anchor_table=anchor,
            )
            if result.status is not JoinPlanStatus.RESOLVED:
                failing_subsets.append(subset)
    if failing_subsets:
        blamed_tables = set(failing_subsets[0])
        for subset in failing_subsets[1:]:
            blamed_tables.intersection_update(subset)
        blamed_tables.discard(anchor)
        if not blamed_tables:
            blamed_tables = {table for subset in failing_subsets for table in subset} - {anchor}
        blamed_fields = tuple(sorted(
            semantic for semantic, target in published.fields.items() if target.table in blamed_tables
        ))
        raise JoinPathError(blamed_fields)


class ValidatedPostgresContext:
    """Startup-validated per-database runtime (server-owned, redacted)."""

    __slots__ = (
        "_database_id",
        "_label",
        "_published_mapping",
        "_expected_fingerprint",
        "_ontology_version",
        "_adapter",
        "_executor",
        "_compiler",
        "_policy",
        "_graph",
        "_field_transforms",
        "_unsupported_fields",
        "_anchor_table",
        "_value_dictionaries",
        "_extension_fields",
    )

    def __init__(
        self,
        *,
        database_id: str,
        label: str,
        published_mapping: PublishedMappingProfile,
        expected_fingerprint: str,
        ontology_version: int,
        adapter: Any,
        executor: Any,
        compiler: Any,
        policy: Any,
        graph: Any,
        field_transforms: dict[str, FieldTransform] | None = None,
        unsupported_fields: tuple[str, ...] = (),
        anchor_table: str | None = None,
        value_dictionaries: dict[str, ValueDictionary] | None = None,
        extension_fields: dict[str, ExtensionField] | None = None,
    ) -> None:
        object.__setattr__(self, "_database_id", database_id)
        object.__setattr__(self, "_extension_fields", _copy_extensions(extension_fields))
        object.__setattr__(self, "_anchor_table", _copy_anchor(anchor_table))
        object.__setattr__(self, "_value_dictionaries", _copy_dictionaries(value_dictionaries))
        object.__setattr__(self, "_field_transforms", _copy_transforms(field_transforms))
        object.__setattr__(self, "_unsupported_fields", _copy_unsupported(unsupported_fields))
        object.__setattr__(self, "_label", label)
        object.__setattr__(self, "_published_mapping", published_mapping)
        object.__setattr__(self, "_expected_fingerprint", expected_fingerprint)
        object.__setattr__(self, "_ontology_version", int(ontology_version))
        object.__setattr__(self, "_adapter", adapter)
        object.__setattr__(self, "_executor", executor)
        object.__setattr__(self, "_compiler", compiler)
        object.__setattr__(self, "_policy", policy)
        object.__setattr__(self, "_graph", graph)

    def __setattr__(self, name: str, value: Any) -> None:
        raise PostgresRuntimeError()

    def __delattr__(self, name: str) -> None:
        raise PostgresRuntimeError()

    @property
    def database_id(self) -> str:
        return self._database_id

    @property
    def label(self) -> str:
        return self._label

    @property
    def expected_fingerprint(self) -> str:
        return self._expected_fingerprint

    @property
    def ontology_version(self) -> int:
        return self._ontology_version

    def adapter(self) -> Any:
        return self._adapter

    def executor(self) -> Any:
        return self._executor

    def compiler(self) -> Any:
        return self._compiler

    def compile_catalog(self, query_ir: Any, mapping: Any, *, member_filters: Any, distinct: bool) -> Any:
        return self._compiler.compile_catalog(query_ir, mapping, member_filters=member_filters, distinct=distinct)

    def policy(self) -> Any:
        return self._policy

    def validate_catalog(self, query: Any, *, query_ir: Any, mapping: Any, member_filters: Any, distinct: bool) -> None:
        self._policy.validate_catalog(query, query_ir=query_ir, mapping=mapping, member_filters=member_filters, distinct=distinct)

    def graph(self) -> Any:
        return self._graph

    def published_mapping(self) -> PublishedMappingProfile:
        return self._published_mapping.model_copy(deep=True)

    def field_transforms(self) -> dict[str, FieldTransform]:
        return dict(self._field_transforms)

    def unsupported_fields(self) -> tuple[str, ...]:
        """Canonical fields this database explicitly cannot answer."""
        return self._unsupported_fields

    def anchor_table(self) -> str | None:
        """Declared row anchor (physical name; server-side only)."""
        return self._anchor_table

    def value_dictionaries(self) -> dict[str, ValueDictionary]:
        return dict(self._value_dictionaries)

    def extension_fields(self) -> dict[str, ExtensionField]:
        """This database's own fields (Phase 8-19): name -> label and value type."""
        return dict(self._extension_fields)

    def normalize_rows(self, columns: Any, rows: Any) -> Any:
        """JSON-safe values for transformed fields and display labels for
        dictionary fields; other values unchanged."""
        if (not self._field_transforms and not self._value_dictionaries) or not isinstance(
            rows, list
        ):
            return rows
        normalized = []
        for row in rows:
            if not isinstance(row, dict):
                return rows
            normalized.append({key: self._normalize_value(key, value) for key, value in row.items()})
        return normalized

    def _normalize_value(self, key: Any, value: Any) -> Any:
        if key in self._field_transforms:
            return normalize_transformed_value(self._field_transforms[key], value)
        if key in self._value_dictionaries:
            return self._value_dictionaries[key].label_for(value)
        return value

    def harness_mapping(self) -> Any:
        return to_harness_mapping(self._published_mapping.model_copy(deep=True))

    def _redacted(self) -> str:
        return "ValidatedPostgresContext(database_id='" + self._database_id + "')"

    def __repr__(self) -> str:
        return self._redacted()

    def __str__(self) -> str:
        return self._redacted()


def _fields_for(mapping_version: int, expected_version: int) -> tuple[set[str], set[str]]:
    """(fields a mapping of ``mapping_version`` must cover, fields added after it)."""
    from ..ontology import load_default_ontology

    ontology = load_default_ontology()
    if not ontology.accepts_version(expected_version) or not 1 <= mapping_version <= expected_version:
        raise PostgresRuntimeError()
    covered = set(ontology.fields_at(mapping_version))
    return covered, set(ontology.fields_at(expected_version)) - covered


def _validate_one(
    profile: PostgresRuntimeProfile,
    adapter: Any,
    executor: Any,
    expected_ontology_version: int,
) -> ValidatedPostgresContext:
    if not isinstance(profile, PostgresRuntimeProfile):
        raise PostgresRuntimeError()
    if not callable(getattr(adapter, "inspect", None)):
        raise PostgresRuntimeError()
    if not callable(getattr(executor, "execute", None)):
        raise PostgresRuntimeError()
    if (
        not isinstance(expected_ontology_version, int)
        or isinstance(expected_ontology_version, bool)
        or expected_ontology_version < 1
    ):
        raise PostgresRuntimeError()
    database_id = profile.database_id
    published = profile.published_mapping
    try:
        if published.status != "PUBLISHED":  # type: ignore[comparison-overlap]
            raise PostgresRuntimeError()
    except PostgresRuntimeError:
        raise
    except Exception:
        raise PostgresRuntimeError() from None
    try:
        catalog = adapter.inspect(database_id)
    except Exception:
        raise PostgresRuntimeError() from None
    try:
        if getattr(catalog, "database_id", None) != database_id:
            raise PostgresRuntimeError()
        if getattr(catalog, "dialect", None) != "postgres":
            raise PostgresRuntimeError()
        fingerprint = str(getattr(catalog, "schema_fingerprint", ""))
        if _FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
            raise PostgresRuntimeError()
        if fingerprint != str(published.schema_fingerprint):
            raise PostgresRuntimeError()
        # Validate the historical record against its own version. Runtime
        # also treats fields retired in the requested version as unsupported.
        canonical, newer = _fields_for(int(published.ontology_version), int(expected_ontology_version))
        transforms = profile.field_transforms
        unsupported = set(profile.unsupported_fields)
        extensions = profile.extension_fields
        mapped = set(published.fields.keys()) - set(extensions)
        # Partial mapping only through an explicit unsupported list: the two
        # sets are disjoint and together cover the mapping version's fields exactly.
        if mapped & unsupported or mapped | unsupported != canonical or not mapped:
            raise PostgresRuntimeError()
        # Phase 8-19: every extension field is mapped, plain (no transform or
        # dictionary), and never named like a common field.
        if (
            not set(extensions) <= set(published.fields)
            or set(extensions) & (canonical | newer | unsupported | set(transforms))
            or set(extensions) & set(profile.value_dictionaries)
        ):
            raise PostgresRuntimeError()
        from ..ontology import load_default_ontology
        retired = {
            name for name, field in load_default_ontology().fields.items()
            if field.retired_in_version is not None and field.retired_in_version <= expected_ontology_version
        }
        unsupported |= newer | retired
        if not set(transforms).issubset(mapped):
            raise PostgresRuntimeError()
        dictionaries = profile.value_dictionaries
        if not set(dictionaries).issubset(mapped) or set(dictionaries) & set(transforms):
            raise PostgresRuntimeError()
        tables = getattr(catalog, "tables", None)
        if not isinstance(tables, list) or not tables:
            raise PostgresRuntimeError()
        columns_by_table: dict[str, set[str]] = {}
        declared_types: dict[tuple[str, str], str] = {}
        for table in tables:
            tname = str(getattr(table, "name", ""))
            cols = getattr(table, "columns", None)
            if not tname or not isinstance(cols, list):
                raise PostgresRuntimeError()
            names = [str(getattr(col, "name", "")) for col in cols]
            if any(not n for n in names) or len(names) != len(set(names)):
                raise PostgresRuntimeError()
            columns_by_table[tname] = set(names)
            for col in cols:
                declared_types[(tname, str(getattr(col, "name", "")))] = str(
                    getattr(col, "declared_type", "") or ""
                )
        physical_fields: set[tuple[str, str]] = set()
        for _, field in published.fields.items():
            tname = str(getattr(field, "table", ""))
            cname = str(getattr(field, "column", ""))
            if tname not in columns_by_table:
                raise PostgresRuntimeError()
            if cname not in columns_by_table[tname]:
                raise PostgresRuntimeError()
            physical = (tname, cname)
            if physical in physical_fields:
                raise PostgresRuntimeError()
            physical_fields.add(physical)
        for name, extension in extensions.items():
            target = published.fields[name]
            if not column_fits(extension.data_type, declared_types.get((target.table, target.column), "")):
                raise PostgresRuntimeError()
        if transforms:
            from ..ontology.loader import load_default_ontology

            ontology_fields = {
                field.name: field
                for entity in load_default_ontology().entities
                for field in entity.fields
            }
            for semantic, transform in transforms.items():
                ontology_field = ontology_fields.get(semantic)
                if ontology_field is None or not transform_supports_data_type(
                    transform, ontology_field.data_type
                ):
                    raise PostgresRuntimeError()
                target = published.fields[semantic]
                if not column_type_supports(
                    transform, declared_types.get((target.table, target.column), "")
                ):
                    raise PostgresRuntimeError()
        from ..ontology.contracts import DataType
        from ..ontology.loader import load_default_ontology

        ontology_types = {
            field.name: field.data_type
            for entity in load_default_ontology().entities
            for field in entity.fields
        }
        for semantic in dictionaries:
            # Stored values are compared as text, so both sides must be text;
            # Phase 8-21: or a period table (HH:MM labels) on an integer column.
            target = published.fields[semantic]
            declared = declared_types.get((target.table, target.column), "").lower()
            kind = ontology_types.get(semantic)
            text = kind == DataType.STRING and _TEXT_TYPE_PATTERN.fullmatch(declared) is not None
            periods = (
                kind == DataType.TIME
                and _INTEGER_TYPE_PATTERN.fullmatch(declared) is not None
                and dictionaries[semantic].time_labels()
            )
            if not (text or periods) or semantic in _IDENTITY_FIELDS:
                raise PostgresRuntimeError()
        for semantic, target in published.fields.items():
            # A time compared with an integer column fails in SQL: only a
            # period table makes such a mapping usable.
            declared = declared_types.get((target.table, target.column), "").lower()
            if (
                ontology_types.get(semantic) == DataType.TIME
                and _INTEGER_TYPE_PATTERN.fullmatch(declared) is not None
                and semantic not in dictionaries
            ):
                raise PostgresRuntimeError()
        from ..relations.physical import build_physical_relation_graph

        graph = build_physical_relation_graph(catalog)
        if (
            getattr(graph, "database_id", None) != database_id
            or str(getattr(graph, "schema_fingerprint", "")) != fingerprint
        ):
            raise PostgresRuntimeError()
        anchor = profile.anchor_table
        if anchor is not None:
            # Fail closed unless every question has one anchored JOIN path.
            _check_anchored_plans(graph, published, anchor)
    except PostgresRuntimeError:
        raise
    except Exception:
        raise PostgresRuntimeError() from None
    try:
        from ..compiler.postgres import SchemaAdaptivePostgresCompiler
        from ..policy.postgres import SchemaAdaptivePostgresPolicy

        compiler = SchemaAdaptivePostgresCompiler(
            graph,
            published,
            field_transforms=transforms,
            anchor_table=anchor,
            value_dictionaries=dictionaries,
        )
        policy = SchemaAdaptivePostgresPolicy(
            graph,
            published,
            field_transforms=transforms,
            anchor_table=anchor,
            value_dictionaries=dictionaries,
        )
    except Exception:
        raise PostgresRuntimeError() from None
    return ValidatedPostgresContext(
        database_id=database_id,
        label=profile.label,
        published_mapping=published.model_copy(deep=True),
        expected_fingerprint=fingerprint,
        ontology_version=int(expected_ontology_version),
        adapter=adapter,
        executor=executor,
        compiler=compiler,
        policy=policy,
        graph=graph,
        field_transforms=transforms,
        unsupported_fields=tuple(sorted(unsupported)),
        anchor_table=anchor,
        value_dictionaries=dictionaries,
        extension_fields=extensions,
    )


def build_validated_contexts(
    profiles: list[PostgresRuntimeProfile],
    *,
    adapters_by_id: dict[str, Any],
    executors_by_id: dict[str, Any],
    expected_ontology_version: int,
    fixed_database_ids: set[str] | frozenset[str] = frozenset(),
    max_databases: int = 8,
) -> dict[str, ValidatedPostgresContext]:
    """Validate every profile with a sample-less inspect; fail closed."""
    if not isinstance(profiles, list):
        raise PostgresRuntimeError()
    if not isinstance(adapters_by_id, dict) or not isinstance(executors_by_id, dict):
        raise PostgresRuntimeError()
    seen: set[str] = set()
    fixed = set(fixed_database_ids) if fixed_database_ids is not None else set()
    if len(fixed) + len(profiles) > int(max_databases):
        raise PostgresRuntimeError()
    contexts: dict[str, ValidatedPostgresContext] = {}
    for profile in profiles:
        if not isinstance(profile, PostgresRuntimeProfile):
            raise PostgresRuntimeError()
        database_id = profile.database_id
        if database_id in seen or database_id in fixed:
            raise PostgresRuntimeError()
        seen.add(database_id)
        adapter = adapters_by_id.get(database_id)
        executor = executors_by_id.get(database_id)
        if adapter is None or executor is None:
            raise PostgresRuntimeError()
        contexts[database_id] = _validate_one(
            profile, adapter, executor, expected_ontology_version
        )
    return contexts


class LivePostgresContexts(dict):  # type: ignore[type-arg]
    """Validated contexts that may grow while the server runs (Phase 8-15).

    Routers keep a reference to this one object instead of a copy, so a
    database published from the admin screen is served without a restart.
    Entries change only through ``add``; plain item assignment is refused.
    """

    def __init__(self, initial: dict[str, ValidatedPostgresContext] | None = None) -> None:
        super().__init__()
        self._lock = threading.Lock()
        for context in (initial or {}).values():
            self.add(context)

    def add(self, context: ValidatedPostgresContext) -> None:
        if not isinstance(context, ValidatedPostgresContext):
            raise PostgresRuntimeError()
        with self._lock:
            dict.__setitem__(self, context.database_id, context)

    def __setitem__(self, key: Any, value: Any) -> None:
        raise PostgresRuntimeError()

    def remove(self, database_id: str) -> None:
        """Forget a deleted registration in every router sharing this live set."""
        with self._lock:
            dict.pop(self, database_id, None)

    def __delitem__(self, key: Any) -> None:
        raise PostgresRuntimeError()

    def pop(self, *_args: Any) -> Any:
        raise PostgresRuntimeError()

    def popitem(self) -> Any:
        raise PostgresRuntimeError()

    def clear(self) -> None:
        raise PostgresRuntimeError()

    def update(self, *_args: Any, **_kwargs: Any) -> None:
        raise PostgresRuntimeError()

    def setdefault(self, *_args: Any) -> Any:
        raise PostgresRuntimeError()


def shared_or_copy(pg_contexts: dict[str, Any]) -> dict[str, Any]:
    """A live set is shared by reference; any other mapping is copied (frozen)."""
    return pg_contexts if isinstance(pg_contexts, LivePostgresContexts) else dict(pg_contexts)


def is_postgres_context_current(context: ValidatedPostgresContext) -> bool:
    """Re-inspect sample-less and compare fingerprints; never leaks details."""
    try:
        database_id = context.database_id
        expected = context.expected_fingerprint
        catalog = context.adapter().inspect(database_id)
    except Exception:
        return False
    try:
        if getattr(catalog, "database_id", None) != database_id:
            return False
        actual = str(getattr(catalog, "schema_fingerprint", ""))
        return (
            getattr(catalog, "dialect", None) == "postgres"
            and actual == expected
            and _FINGERPRINT_PATTERN.fullmatch(actual) is not None
        )
    except Exception:
        return False


def _stale_failure() -> HarnessFailure:
    return HarnessFailure(
        HarnessErrorCode.MAPPING_NOT_PUBLISHED,
        _FIXED_STALE_MESSAGE,
        stage=HarnessStage.EXECUTED,
    )


class DatabaseRoutingCompiler:
    """Thin server-owned router: SQLite default, postgres per mapping ID."""

    def __init__(self, sqlite_compiler: Any, pg_contexts: dict[str, ValidatedPostgresContext]) -> None:
        if not callable(getattr(sqlite_compiler, "compile", None)):
            raise PostgresRuntimeError()
        if not isinstance(pg_contexts, dict):
            raise PostgresRuntimeError()
        object.__setattr__(self, "_sqlite_compiler", sqlite_compiler)
        object.__setattr__(self, "_pg_contexts", shared_or_copy(pg_contexts))

    def __setattr__(self, name: str, value: Any) -> None:
        raise PostgresRuntimeError()

    def compile(self, query_ir: Any, mapping: Any) -> Any:
        try:
            database_id = getattr(mapping, "database_id", None)
        except Exception:
            raise _stale_failure() from None
        context = self._pg_contexts.get(database_id) if isinstance(database_id, str) else None
        if context is None:
            return self._sqlite_compiler.compile(query_ir, mapping)
        try:
            return context.compiler().compile(query_ir, mapping)
        except Exception:
            raise HarnessFailure(
                HarnessErrorCode.SQL_COMPILE_ERROR,
                _FIXED_COMPILE_MESSAGE,
                stage=HarnessStage.SQL_COMPILED,
            ) from None

    def compile_catalog(self, query_ir: Any, mapping: Any, *, member_filters: Any, distinct: bool) -> Any:
        database_id = getattr(mapping, "database_id", None)
        context = self._pg_contexts.get(database_id) if isinstance(database_id, str) else None
        if context is None:
            raise HarnessFailure(HarnessErrorCode.SQL_COMPILE_ERROR, _FIXED_COMPILE_MESSAGE, stage=HarnessStage.SQL_COMPILED)
        try:
            return context.compile_catalog(query_ir, mapping, member_filters=member_filters, distinct=distinct)
        except Exception:
            raise HarnessFailure(HarnessErrorCode.SQL_COMPILE_ERROR, _FIXED_COMPILE_MESSAGE, stage=HarnessStage.SQL_COMPILED) from None

    def __repr__(self) -> str:
        return "DatabaseRoutingCompiler()"

    def __str__(self) -> str:
        return "DatabaseRoutingCompiler()"


class DatabaseRoutingPolicy:
    """Thin server-owned router: SQLite default, postgres per mapping ID."""

    def __init__(self, sqlite_policy: Any, pg_contexts: dict[str, ValidatedPostgresContext]) -> None:
        if not callable(getattr(sqlite_policy, "validate", None)):
            raise PostgresRuntimeError()
        if not isinstance(pg_contexts, dict):
            raise PostgresRuntimeError()
        object.__setattr__(self, "_sqlite_policy", sqlite_policy)
        object.__setattr__(self, "_pg_contexts", shared_or_copy(pg_contexts))

    def __setattr__(self, name: str, value: Any) -> None:
        raise PostgresRuntimeError()

    def validate(self, query: Any, **kwargs: Any) -> None:
        mapping = kwargs.get("mapping")
        try:
            database_id = getattr(mapping, "database_id", None)
        except Exception:
            database_id = None
        context = self._pg_contexts.get(database_id) if isinstance(database_id, str) else None
        if context is None:
            self._sqlite_policy.validate(query, **kwargs)
            return
        # Drift is checked in loader/executor/catalog paths; policy itself
        # delegates to the validated per-database policy.
        try:
            context.policy().validate(query, **kwargs)
        except Exception:
            raise HarnessFailure(
                HarnessErrorCode.UNSAFE_SQL,
                _FIXED_POLICY_MESSAGE,
                stage=HarnessStage.SQL_VALIDATED,
            ) from None

    def validate_catalog(self, query: Any, *, query_ir: Any, mapping: Any, member_filters: Any, distinct: bool) -> None:
        database_id = getattr(mapping, "database_id", None)
        context = self._pg_contexts.get(database_id) if isinstance(database_id, str) else None
        if context is None:
            raise HarnessFailure(HarnessErrorCode.UNSAFE_SQL, _FIXED_POLICY_MESSAGE, stage=HarnessStage.SQL_VALIDATED)
        try:
            context.validate_catalog(query, query_ir=query_ir, mapping=mapping, member_filters=member_filters, distinct=distinct)
        except Exception:
            raise HarnessFailure(HarnessErrorCode.UNSAFE_SQL, _FIXED_POLICY_MESSAGE, stage=HarnessStage.SQL_VALIDATED) from None

    def __repr__(self) -> str:
        return "DatabaseRoutingPolicy()"

    def __str__(self) -> str:
        return "DatabaseRoutingPolicy()"


class DatabaseRoutingExecutor:
    """Thin server-owned router with pre-execute drift check for postgres."""

    def __init__(
        self,
        sqlite_executor: Any,
        pg_contexts: dict[str, ValidatedPostgresContext],
    ) -> None:
        if not callable(getattr(sqlite_executor, "execute", None)):
            raise PostgresRuntimeError()
        if not isinstance(pg_contexts, dict):
            raise PostgresRuntimeError()
        object.__setattr__(self, "_sqlite_executor", sqlite_executor)
        object.__setattr__(self, "_pg_contexts", shared_or_copy(pg_contexts))

    def __setattr__(self, name: str, value: Any) -> None:
        raise PostgresRuntimeError()

    def execute(self, database_id: str, compiled: Any) -> Any:
        context = self._pg_contexts.get(database_id) if isinstance(database_id, str) else None
        if context is None:
            return self._sqlite_executor.execute(database_id, compiled)
        try:
            current = is_postgres_context_current(context)
        except Exception:
            current = False
        if current is not True:
            raise _stale_failure()
        try:
            columns, rows = context.executor().execute(database_id, compiled)
            return columns, context.normalize_rows(columns, rows)
        except Exception:
            raise HarnessFailure(
                HarnessErrorCode.EXECUTION_ERROR,
                _FIXED_EXECUTION_MESSAGE,
                stage=HarnessStage.EXECUTED,
            ) from None

    def __repr__(self) -> str:
        return "DatabaseRoutingExecutor()"

    def __str__(self) -> str:
        return "DatabaseRoutingExecutor()"
