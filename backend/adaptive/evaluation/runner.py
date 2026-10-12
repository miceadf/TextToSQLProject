"""Run synthetic gold questions through the existing safe execution harness."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping

from ..catalog import SQLiteAdapter
from ..catalog.contracts import DatabaseCatalog
from ..compiler import SchemaAdaptiveSQLiteCompiler
from ..compiler.harness import SchemaAdaptiveSQLiteCompiler as _PreflightCompiler
from ..fixtures.contracts import FIXTURE_SUITES, FixtureBundle, FixtureSeed, FixtureVariant
from ..fixtures.seed import DEFAULT_SEED
from ..harness.contracts import (
    HarnessRequest,
    HarnessStage,
    HarnessStatus,
    Ontology,
    OntologyField,
    QueryIR,
)
from ..harness.engine import ExecutionHarness
from ..harness.executor import SQLiteReadOnlyExecutor
from ..harness.executor import SQLiteReadOnlyExecutor as _PreflightExecutor
from ..harness.model import FakeModel, QueryModel
from ..harness.recorded import RecordedModelReplayError
from ..mapping import to_harness_mapping
from ..mapping.contracts import PublishedMappingProfile
from ..ontology.contracts import DomainOntology
from ..ontology.loader import load_default_ontology
from ..policy import SchemaAdaptiveAstPolicy
from ..policy.harness import SchemaAdaptiveAstPolicy as _PreflightPolicy
from ..relations.physical import build_physical_relation_graph
from .comparison import semantic_row_multiset
from .contracts import EvaluationSet
from .coverage import build_coverage_report
from .fixture_mapping import fixture_published_mapping
from .reference import FIELD_TO_ATTRIBUTE, semantic_row
from .run_contracts import (
    CaseAttempt,
    ComparisonStatus,
    EvaluationRunConfig,
    EvaluationRunReport,
    FixtureRunMetadata,
)
from .run_report import build_run_report, deterministic_request_id
from .validator import validate_evaluation_set
from .ontology_version import recorded_ontology


class _ModelInvocationFailure(Exception):
    """Carry a controlled code, never provider exception text, into the report."""

    def __init__(self, code: str = "MODEL_EXCEPTION") -> None:
        self.code = code
        super().__init__(code)


class _ModelOutputFailure(Exception):
    """A non-finite numeric value cannot be fingerprinted or scored safely."""


class _SafeModel:
    def __init__(self, delegate: QueryModel) -> None:
        self._delegate = delegate

    def generate_ir(self, request, ontology, mapping):
        try:
            raw = self._delegate.generate_ir(
                request.model_copy(deep=True),
                ontology.model_copy(deep=True),
                mapping.model_copy(deep=True),
            )
        except RecordedModelReplayError as exc:
            raise _ModelInvocationFailure(exc.code.value) from exc
        except Exception as exc:
            raise _ModelInvocationFailure() from exc
        try:
            candidate = QueryIR.model_validate(raw)
        except Exception:
            return raw  # The existing harness assigns MODEL_INVALID_OUTPUT.
        if any(
            isinstance(item.value, float) and not math.isfinite(item.value)
            for item in candidate.filters
        ):
            raise _ModelOutputFailure()
        return raw


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _row_fingerprint(rows: Counter) -> str:
    return _fingerprint(sorted((row, count) for row, count in rows.items()))


def _runtime_ontology(ontology: DomainOntology) -> Ontology:
    return Ontology(
        entities={entity.name for entity in ontology.entities},
        fields={
            name: OntologyField(
                name=name,
                data_type=field.data_type,
                allowed_operators=field.allowed_operators,
            )
            for name, field in ontology.active_fields.items()
        },
    )


def _prepare_fixture_contexts(
    bundle: FixtureBundle,
    seed: FixtureSeed,
    ontology: DomainOntology,
    published_mappings: Mapping[FixtureVariant, PublishedMappingProfile] | None,
):
    if bundle.seed_version != seed.version:
        raise ValueError("fixture seed version drift")
    contexts = {}
    metadata = []
    runtime_ontology = _runtime_ontology(ontology)
    all_fields = sorted(set(FIELD_TO_ATTRIBUTE) & set(ontology.active_fields))
    preflight_ir = QueryIR(entity="CourseOffering", select=all_fields, limit=200)
    expected_rows = semantic_row_multiset(
        [semantic_row(offering, all_fields) for offering in seed.offerings],
        all_fields,
        ontology,
    )
    # Re-check here: a bundle mutated after validation must not pick its own variants.
    if set(bundle.artifacts) not in [set(item) for item in FIXTURE_SUITES.values()]:
        raise ValueError("fixture bundle variant drift")
    for variant in (item for item in FixtureVariant if item in bundle.artifacts):
        artifact = bundle.artifacts.get(variant)
        if artifact is None or artifact.variant is not variant:
            raise ValueError("fixture bundle variant drift")
        expected_database_id = f"fixture-{variant.value}-v{seed.version}"
        if artifact.database_id != expected_database_id:
            raise ValueError("fixture database identity drift")
        catalog = DatabaseCatalog.model_validate_json(
            artifact.catalog_path.read_text(encoding="utf-8")
        )
        if catalog.database_id != artifact.database_id:
            raise ValueError("fixture Catalog identity drift")
        live_catalog = SQLiteAdapter(
            {artifact.database_id: artifact.database_path}
        ).inspect(artifact.database_id)
        if catalog.model_dump(mode="json") != live_catalog.model_dump(mode="json"):
            raise ValueError("fixture Catalog schema fingerprint or metadata drift")
        expected_mapping = fixture_published_mapping(
            variant, catalog, ontology_version=ontology.version
        )
        mapping = (
            published_mappings[variant]
            if published_mappings is not None and variant in published_mappings
            else expected_mapping
        )
        if mapping.model_dump(mode="json") != expected_mapping.model_dump(mode="json"):
            raise ValueError("fixture Published Mapping drift")
        if mapping.schema_fingerprint != live_catalog.schema_fingerprint:
            raise ValueError("fixture mapping/Catalog fingerprint drift")
        graph = build_physical_relation_graph(catalog)
        preflight_question = "__fixture_seed_preflight__"
        preflight = ExecutionHarness(
            model=FakeModel({preflight_question: preflight_ir.model_dump(mode="json")}),
            compiler=_PreflightCompiler(graph, mapping),
            sql_policy=_PreflightPolicy(graph, mapping),
            executor=_PreflightExecutor({artifact.database_id: artifact.database_path}),
        ).run(
            HarnessRequest(
                request_id=f"fixture-preflight:{variant.value}",
                database_id=artifact.database_id,
                question=preflight_question,
            ),
            runtime_ontology,
            to_harness_mapping(mapping),
        )
        if preflight.status is not HarnessStatus.SUCCEEDED:
            raise ValueError("fixture seed content preflight failed")
        try:
            actual_rows = semantic_row_multiset(preflight.rows, all_fields, ontology)
        except ValueError as exc:
            raise ValueError("fixture seed content drift") from exc
        if actual_rows != expected_rows:
            raise ValueError("fixture seed content drift")
        contexts[variant] = (artifact, graph, mapping)
        metadata.append(FixtureRunMetadata(
            variant=variant.value,
            catalog_fingerprint=catalog.schema_fingerprint,
            mapping_fingerprint=_fingerprint(mapping.model_dump(mode="json")),
            mapping_version=mapping.version,
        ))
    return contexts, metadata


def run_evaluation(
    gold: EvaluationSet,
    model: QueryModel,
    config: EvaluationRunConfig,
    bundle: FixtureBundle,
    *,
    ontology: DomainOntology | None = None,
    seed: FixtureSeed = DEFAULT_SEED,
    published_mappings: Mapping[FixtureVariant, PublishedMappingProfile] | None = None,
) -> EvaluationRunReport:
    """Evaluate every scheduled attempt; never supply gold IR to the model."""

    domain_ontology = ontology or load_default_ontology()
    validate_evaluation_set(gold, seed, domain_ontology)
    domain_ontology = recorded_ontology(domain_ontology, version=gold.ontology_version)
    contexts, fixture_metadata = _prepare_fixture_contexts(
        bundle, seed, domain_ontology, published_mappings
    )
    if any(FixtureVariant(name) not in contexts for name in config.variants):
        raise ValueError("configured variant missing from fixture bundle")
    normalized = contexts[FixtureVariant.NORMALIZED]
    coverage = build_coverage_report(
        gold, domain_ontology, normalized[1], normalized[2]
    )
    runtime_ontology = _runtime_ontology(domain_ontology)
    executor = SQLiteReadOnlyExecutor({
        artifact.database_id: artifact.database_path
        for artifact, _, _ in contexts.values()
    })
    safe_model = _SafeModel(model)
    harnesses = {
        variant: ExecutionHarness(
            model=safe_model,
            compiler=SchemaAdaptiveSQLiteCompiler(graph, mapping),
            sql_policy=SchemaAdaptiveAstPolicy(graph, mapping),
            executor=executor,
        )
        for variant, (_, graph, mapping) in contexts.items()
    }

    attempts: list[CaseAttempt] = []
    for case in gold.cases:
        expected = semantic_row_multiset(
            case.expected.rows, case.gold_query_ir.select, domain_ontology
        )
        for variant_name in config.variants:
            variant = FixtureVariant(variant_name)
            artifact, _, published = contexts[variant]
            request_id = deterministic_request_id(
                config, gold, case.case_id, variant.value
            )
            request = HarnessRequest(
                request_id=request_id,
                database_id=artifact.database_id,
                question=case.question_ko,
            )
            try:
                result = harnesses[variant].run(
                    request, runtime_ontology, to_harness_mapping(published)
                )
            except _ModelInvocationFailure as exc:
                attempts.append(CaseAttempt(
                    case_id=case.case_id,
                    variant=variant.value,
                    request_id=request_id,
                    structural_ir_exact=None,
                    ir_fingerprint=None,
                    execution_success=False,
                    comparison_status=ComparisonStatus.NOT_EXECUTED,
                    semantic_correct=None,
                    result_fingerprint=None,
                    failure_stage=HarnessStage.IR_GENERATED,
                    failure_code=exc.code,
                ))
                continue
            except _ModelOutputFailure:
                attempts.append(CaseAttempt(
                    case_id=case.case_id,
                    variant=variant.value,
                    request_id=request_id,
                    structural_ir_exact=None,
                    ir_fingerprint=None,
                    execution_success=False,
                    comparison_status=ComparisonStatus.NOT_EXECUTED,
                    semantic_correct=None,
                    result_fingerprint=None,
                    failure_stage=HarnessStage.IR_GENERATED,
                    failure_code="MODEL_NONFINITE_OUTPUT",
                ))
                continue
            except Exception:
                attempts.append(CaseAttempt(
                    case_id=case.case_id,
                    variant=variant.value,
                    request_id=request_id,
                    structural_ir_exact=None,
                    ir_fingerprint=None,
                    execution_success=False,
                    comparison_status=ComparisonStatus.NOT_EXECUTED,
                    semantic_correct=None,
                    result_fingerprint=None,
                    failure_stage=None,
                    failure_code="RUNNER_EXCEPTION",
                ))
                continue

            predicted_ir: QueryIR | None = result.query_ir
            ir_fingerprint = (
                _fingerprint(predicted_ir.model_dump(mode="json"))
                if predicted_ir is not None else None
            )
            structural_exact = (
                _fingerprint(predicted_ir.model_dump(mode="json"))
                == _fingerprint(case.gold_query_ir.model_dump(mode="json"))
                if predicted_ir is not None else None
            )
            if result.status is not HarnessStatus.SUCCEEDED:
                attempts.append(CaseAttempt(
                    case_id=case.case_id,
                    variant=variant.value,
                    request_id=request_id,
                    structural_ir_exact=structural_exact,
                    ir_fingerprint=ir_fingerprint,
                    execution_success=False,
                    comparison_status=ComparisonStatus.NOT_EXECUTED,
                    semantic_correct=None,
                    result_fingerprint=None,
                    failure_stage=result.error.stage if result.error else None,
                    failure_code=result.error.code.value if result.error else "HARNESS_FAILURE",
                ))
                continue

            if predicted_ir is None or set(predicted_ir.select) != set(case.gold_query_ir.select):
                attempts.append(CaseAttempt(
                    case_id=case.case_id,
                    variant=variant.value,
                    request_id=request_id,
                    structural_ir_exact=structural_exact,
                    ir_fingerprint=ir_fingerprint,
                    execution_success=True,
                    comparison_status=ComparisonStatus.NOT_COMPARABLE_PROJECTION,
                    semantic_correct=None,
                    result_fingerprint=None,
                    failure_stage=HarnessStage.RESULT_VALIDATED,
                    failure_code="NOT_COMPARABLE_PROJECTION",
                ))
                continue

            try:
                actual = semantic_row_multiset(
                    result.rows, case.gold_query_ir.select, domain_ontology
                )
            except ValueError:
                attempts.append(CaseAttempt(
                    case_id=case.case_id,
                    variant=variant.value,
                    request_id=request_id,
                    structural_ir_exact=structural_exact,
                    ir_fingerprint=ir_fingerprint,
                    execution_success=True,
                    comparison_status=ComparisonStatus.COMPARISON_ERROR,
                    semantic_correct=None,
                    result_fingerprint=None,
                    failure_stage=HarnessStage.RESULT_VALIDATED,
                    failure_code="SEMANTIC_ROW_TYPE_MISMATCH",
                ))
                continue
            correct = actual == expected
            attempts.append(CaseAttempt(
                case_id=case.case_id,
                variant=variant.value,
                request_id=request_id,
                structural_ir_exact=structural_exact,
                ir_fingerprint=ir_fingerprint,
                execution_success=True,
                comparison_status=ComparisonStatus.COMPARABLE,
                semantic_correct=correct,
                result_fingerprint=_row_fingerprint(actual),
                failure_stage=None if correct else HarnessStage.RESULT_VALIDATED,
                failure_code=None if correct else "SEMANTIC_MISMATCH",
            ))

    metadata_by_variant = {item.variant: item for item in fixture_metadata}
    selected_metadata = [metadata_by_variant[name] for name in config.variants]
    return build_run_report(config, gold, selected_metadata, attempts, coverage)
