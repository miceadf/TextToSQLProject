from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from sqlglot import exp

from backend.adaptive.harness.contracts import FilterIR, QueryIR
from backend.adaptive.harness.validator import validate_query_ir
from backend.adaptive.ontology.contracts import DataType, DomainOntology, Operator

from .contracts import DatasetRecord
from .conversion_contracts import (
    ConversionReasonCode,
    ConversionStatus,
    DatasetConversionResult,
    DatasetSchemaProfile,
    DuplicateProvenance,
    IntentFilterSignature,
    IntentSignature,
    LimitProvenance,
    SourceColumnMapping,
    SourceLiteralType,
)
from .source_profile import build_runtime_validation_context
from .sql_classifier import classify_ir_compatibility
from .sql_contracts import IrCompatibilityStatus, IrReasonCode
from .sql_features import extract_sql_features
from .sql_parser import parse_and_canonicalize


_INTEGER = re.compile(r"\d+\Z")
_COMPARISONS: tuple[tuple[type[exp.Expression], Operator], ...] = (
    (exp.EQ, Operator.EQUALS),
    (exp.NEQ, Operator.NOT_EQUALS),
    (exp.GT, Operator.GREATER_THAN),
    (exp.GTE, Operator.GREATER_THAN_OR_EQUAL),
    (exp.LT, Operator.LESS_THAN),
    (exp.LTE, Operator.LESS_THAN_OR_EQUAL),
)
_REVIEW_REASONS = {
    ConversionReasonCode.QUESTION_SQL_CONFLICT,
    ConversionReasonCode.UNMAPPED_SOURCE_COLUMN,
    ConversionReasonCode.AMBIGUOUS_QUOTED_IDENTIFIER,
    ConversionReasonCode.DUPLICATE_SEMANTIC_PROJECTION,
    ConversionReasonCode.OPERATOR_NOT_ALLOWED_FOR_FIELD,
    ConversionReasonCode.NON_LITERAL_FILTER_VALUE,
    ConversionReasonCode.LITERAL_TYPE_MISMATCH,
    ConversionReasonCode.UNSAFE_LITERAL_COERCION,
    ConversionReasonCode.UNSUPPORTED_LIKE_PATTERN,
    ConversionReasonCode.EMPTY_CONTAINS_VALUE,
    ConversionReasonCode.QUERY_IR_VALIDATION_FAILED,
    ConversionReasonCode.INTENT_SIGNATURE_MISMATCH,
}


def convert_record(
    record: DatasetRecord,
    profile: DatasetSchemaProfile,
    ontology: DomainOntology,
    *,
    duplicate_provenance: DuplicateProvenance | None = None,
    default_limit: int = 50,
) -> DatasetConversionResult:
    """Conservatively convert one approved legacy SQL record into QueryIR.

    SQL is parsed but never executed. Failure details are represented only by
    fixed reason codes so source SQL and literal values cannot leak through an
    exception message.
    """

    provenance = duplicate_provenance or DuplicateProvenance()
    try:
        parsed, _ = parse_and_canonicalize(record, dialect=profile.dialect)
    except Exception:
        parsed = None
    if parsed is None:
        parse_reasons = [ConversionReasonCode.CONVERSION_ERROR]
        parse_status = ConversionStatus.UNSUPPORTED
        if provenance.question_conflict_group_id is not None:
            parse_reasons.append(ConversionReasonCode.QUESTION_SQL_CONFLICT)
            parse_status = ConversionStatus.REVIEW_REQUIRED
        return _result(
            record,
            status=parse_status,
            reasons=parse_reasons,
            analyzer_status=IrCompatibilityStatus.UNSUPPORTED_OR_UNSAFE,
            provenance=provenance,
        )

    try:
        features = extract_sql_features(parsed.expression)
        compatibility = classify_ir_compatibility(parsed.expression, features)
    except Exception:
        analysis_reasons = [ConversionReasonCode.CONVERSION_ERROR]
        analysis_status = ConversionStatus.UNSUPPORTED
        if provenance.question_conflict_group_id is not None:
            analysis_reasons.append(ConversionReasonCode.QUESTION_SQL_CONFLICT)
            analysis_status = ConversionStatus.REVIEW_REQUIRED
        return _result(
            record,
            status=analysis_status,
            reasons=analysis_reasons,
            analyzer_status=IrCompatibilityStatus.UNSUPPORTED_OR_UNSAFE,
            provenance=provenance,
            canonical_hash=parsed.canonical_hash,
            shape_hash=parsed.shape_hash,
        )
    common = {
        "canonical_hash": parsed.canonical_hash,
        "shape_hash": parsed.shape_hash,
        "analyzer_status": compatibility.status,
        "analyzer_reason_codes": compatibility.reason_codes,
    }

    gate_reasons: set[ConversionReasonCode] = set()
    if provenance.question_conflict_group_id is not None:
        gate_reasons.add(ConversionReasonCode.QUESTION_SQL_CONFLICT)
    if compatibility.status is not IrCompatibilityStatus.CURRENT_IR_CANDIDATE:
        gate_reasons.add(ConversionReasonCode.ANALYZER_NOT_CURRENT_IR_CANDIDATE)
        if compatibility.status is IrCompatibilityStatus.MANUAL_REVIEW:
            gate_reasons.update(
                _diagnose_manual_review(
                    parsed.expression,
                    compatibility.reason_codes,
                )
            )
    if compatibility.status is not IrCompatibilityStatus.CURRENT_IR_CANDIDATE:
        status = ConversionStatus.REVIEW_REQUIRED if (
            provenance.question_conflict_group_id is not None
            or compatibility.status is IrCompatibilityStatus.MANUAL_REVIEW
        ) else ConversionStatus.UNSUPPORTED
        return _result(
            record,
            status=status,
            reasons=_ordered_reasons(gate_reasons),
            provenance=provenance,
            **common,
        )

    try:
        converted = _convert_candidate(
            record,
            parsed.expression,
            profile,
            ontology,
            provenance,
            default_limit,
            **common,
        )
        if provenance.question_conflict_group_id is not None:
            return _result(
                record,
                status=ConversionStatus.REVIEW_REQUIRED,
                reasons=_ordered_reasons(
                    {
                        *(
                            reason
                            for reason in converted.reason_codes
                            if reason
                            is not ConversionReasonCode.DEFAULT_LIMIT_APPLIED
                        ),
                        ConversionReasonCode.QUESTION_SQL_CONFLICT,
                    }
                ),
                provenance=provenance,
                **common,
            )
        return converted
    except Exception:
        failure_reasons = {ConversionReasonCode.CONVERSION_ERROR}
        failure_status = ConversionStatus.UNSUPPORTED
        if provenance.question_conflict_group_id is not None:
            failure_reasons.add(ConversionReasonCode.QUESTION_SQL_CONFLICT)
            failure_status = ConversionStatus.REVIEW_REQUIRED
        return _result(
            record,
            status=failure_status,
            reasons=_ordered_reasons(failure_reasons),
            provenance=provenance,
            **common,
        )


def _convert_candidate(
    record: DatasetRecord,
    expression: exp.Expression,
    profile: DatasetSchemaProfile,
    ontology: DomainOntology,
    provenance: DuplicateProvenance,
    default_limit: int,
    **common: object,
) -> DatasetConversionResult:
    reasons: set[ConversionReasonCode] = set()
    if not 1 <= default_limit <= 200:
        reasons.add(ConversionReasonCode.INVALID_SOURCE_LIMIT)
    if not ontology.accepts_version(profile.ontology_version) or profile.root_entity not in {
        entity.name for entity in ontology.entities
    }:
        reasons.add(ConversionReasonCode.SOURCE_PROFILE_MISMATCH)

    select = expression if isinstance(expression, exp.Select) else None
    if select is None:
        reasons.add(ConversionReasonCode.UNSUPPORTED_PROJECTION)
        return _failed_result(record, provenance, reasons, **common)

    table_name, qualifiers, table_reasons = _resolve_source_table(select, profile)
    reasons.update(table_reasons)
    semantic_select: list[str] = []
    if table_name is not None:
        for projection in select.expressions:
            if not isinstance(projection, exp.Column) or isinstance(
                projection.this, exp.Star
            ):
                reasons.add(ConversionReasonCode.UNSUPPORTED_PROJECTION)
                continue
            mapping, mapping_reason = _resolve_column_mapping(
                projection,
                table_name,
                qualifiers,
                profile,
            )
            if mapping_reason is not None:
                reasons.add(mapping_reason)
            elif mapping is not None:
                semantic_select.append(mapping.semantic_field)

    if len(semantic_select) != len(set(semantic_select)):
        reasons.add(ConversionReasonCode.DUPLICATE_SEMANTIC_PROJECTION)

    semantic_filters: list[IntentFilterSignature] = []
    where = select.args.get("where")
    if where is not None and table_name is not None:
        leaves, boolean_supported = _flatten_and(where.this)
        if not boolean_supported:
            reasons.add(ConversionReasonCode.UNSUPPORTED_BOOLEAN_STRUCTURE)
        else:
            for leaf in leaves:
                converted, leaf_reasons = _convert_filter(
                    leaf,
                    table_name,
                    qualifiers,
                    profile,
                    ontology,
                )
                reasons.update(leaf_reasons)
                if converted is not None:
                    semantic_filters.append(converted)

    limit, limit_provenance, limit_reason = _convert_limit(select, default_limit)
    if limit_reason is not None:
        reasons.add(limit_reason)

    if reasons:
        return _failed_result(record, provenance, reasons, **common)

    assert limit is not None
    source_signature = IntentSignature(
        root_entity=profile.root_entity,
        select=semantic_select,
        filters=semantic_filters,
        limit=limit,
        limit_provenance=limit_provenance,
    )
    query_ir = QueryIR(
        entity=profile.root_entity,
        select=semantic_select,
        filters=[
            FilterIR(field=item.field, operator=item.operator, value=item.value)
            for item in semantic_filters
        ],
        limit=limit,
    )

    try:
        runtime_ontology, runtime_mapping = build_runtime_validation_context(
            profile,
            ontology,
        )
        validate_query_ir(query_ir, runtime_ontology, runtime_mapping)
    except Exception:
        return _failed_result(
            record,
            provenance,
            {ConversionReasonCode.QUERY_IR_VALIDATION_FAILED},
            **common,
        )

    rebuilt_signature = IntentSignature(
        root_entity=query_ir.entity,
        select=list(query_ir.select),
        filters=[
            IntentFilterSignature(
                field=item.field,
                operator=item.operator,
                value=item.value,
            )
            for item in query_ir.filters
        ],
        limit=query_ir.limit,
        limit_provenance=limit_provenance,
    )
    if rebuilt_signature != source_signature:
        return _failed_result(
            record,
            provenance,
            {ConversionReasonCode.INTENT_SIGNATURE_MISMATCH},
            **common,
        )

    reason_codes: list[ConversionReasonCode] = []
    status = ConversionStatus.CONVERTED_EXACT
    if limit_provenance is LimitProvenance.EXECUTION_POLICY_DEFAULT:
        status = ConversionStatus.CONVERTED_WITH_EXECUTION_POLICY
        reason_codes.append(ConversionReasonCode.DEFAULT_LIMIT_APPLIED)
    return _result(
        record,
        status=status,
        reasons=reason_codes,
        provenance=provenance,
        query_ir=query_ir,
        intent_signature=source_signature,
        **common,
    )


def _resolve_source_table(
    select: exp.Select,
    profile: DatasetSchemaProfile,
) -> tuple[str | None, set[str], set[ConversionReasonCode]]:
    tables = list(select.find_all(exp.Table))
    if len(tables) != 1:
        return None, set(), {ConversionReasonCode.UNKNOWN_SOURCE_TABLE}
    table = tables[0]
    table_name = table.name
    if (
        table_name not in profile.table_allowlist
        or table.db
        or table.catalog
    ):
        return None, set(), {ConversionReasonCode.UNKNOWN_SOURCE_TABLE}
    qualifiers = {table.alias} if table.alias else {table_name}
    return table_name, qualifiers, set()


def _diagnose_manual_review(
    expression: exp.Expression,
    analyzer_reasons: list[IrReasonCode],
) -> set[ConversionReasonCode]:
    """Add safe, converter-specific reasons without attempting conversion."""

    reasons: set[ConversionReasonCode] = set()
    select = expression if isinstance(expression, exp.Select) else None
    if select is None:
        return reasons
    if any(
        not isinstance(item, exp.Column)
        or isinstance(item.this, exp.Star)
        for item in select.expressions
    ):
        reasons.add(ConversionReasonCode.UNSUPPORTED_PROJECTION)

    where = select.args.get("where")
    if where is not None:
        leaves, supported = _flatten_and(where.this)
        if not supported:
            reasons.add(ConversionReasonCode.UNSUPPORTED_BOOLEAN_STRUCTURE)
        for leaf in leaves:
            leaf = _unwrap_parentheses(leaf)
            if not isinstance(leaf, (*[item[0] for item in _COMPARISONS], exp.Like)):
                reasons.add(ConversionReasonCode.UNSUPPORTED_OPERATOR)
                continue
            right = _unwrap_parentheses(leaf.expression)
            if isinstance(right, exp.Column):
                identifier = right.this
                if (
                    isinstance(identifier, exp.Identifier)
                    and identifier.args.get("quoted")
                ):
                    reasons.add(
                        ConversionReasonCode.AMBIGUOUS_QUOTED_IDENTIFIER
                    )
                else:
                    reasons.add(ConversionReasonCode.NON_LITERAL_FILTER_VALUE)
            elif isinstance(leaf, exp.Like):
                _, reason = _convert_contains_literal(right)
                if reason is not None:
                    reasons.add(reason)

    if IrReasonCode.NON_LITERAL_FILTER_VALUE in analyzer_reasons and not any(
        reason
        in {
            ConversionReasonCode.NON_LITERAL_FILTER_VALUE,
            ConversionReasonCode.AMBIGUOUS_QUOTED_IDENTIFIER,
        }
        for reason in reasons
    ):
        reasons.add(ConversionReasonCode.NON_LITERAL_FILTER_VALUE)

    projection_reasons = {
        IrReasonCode.WILDCARD_PROJECTION,
        IrReasonCode.RESULT_ALIAS,
        IrReasonCode.FUNCTION_PROJECTION,
        IrReasonCode.EXPRESSION_PROJECTION,
    }
    if projection_reasons.intersection(analyzer_reasons):
        reasons.add(ConversionReasonCode.UNSUPPORTED_PROJECTION)

    limit = select.args.get("limit")
    if limit is not None:
        _, _, reason = _convert_limit(select, 50)
        if reason is not None:
            reasons.add(reason)
    return reasons


def _resolve_column_mapping(
    column: exp.Column,
    table_name: str,
    qualifiers: set[str],
    profile: DatasetSchemaProfile,
) -> tuple[SourceColumnMapping | None, ConversionReasonCode | None]:
    if column.table and column.table not in qualifiers:
        return None, ConversionReasonCode.UNKNOWN_SOURCE_TABLE
    mapping = profile.mappings_by_physical.get((table_name, column.name))
    if mapping is None:
        identifier = column.this
        if isinstance(identifier, exp.Identifier) and identifier.args.get("quoted"):
            return None, ConversionReasonCode.AMBIGUOUS_QUOTED_IDENTIFIER
        return None, ConversionReasonCode.UNMAPPED_SOURCE_COLUMN
    return mapping, None


def _flatten_and(
    expression: exp.Expression,
) -> tuple[list[exp.Expression], bool]:
    node = _unwrap_parentheses(expression)
    if isinstance(node, exp.And):
        left, left_ok = _flatten_and(node.this)
        right, right_ok = _flatten_and(node.expression)
        return [*left, *right], left_ok and right_ok
    if isinstance(node, (exp.Or, exp.Not)):
        return [], False
    return [node], True


def _convert_filter(
    node: exp.Expression,
    table_name: str,
    qualifiers: set[str],
    profile: DatasetSchemaProfile,
    ontology: DomainOntology,
) -> tuple[IntentFilterSignature | None, set[ConversionReasonCode]]:
    node = _unwrap_parentheses(node)
    operator: Operator | None = None
    for node_type, candidate in _COMPARISONS:
        if isinstance(node, node_type):
            operator = candidate
            break
    is_like = isinstance(node, exp.Like)
    if is_like:
        operator = Operator.CONTAINS
    if operator is None:
        return None, {ConversionReasonCode.UNSUPPORTED_OPERATOR}

    left = _unwrap_parentheses(node.this)
    right = _unwrap_parentheses(node.expression)
    if not isinstance(left, exp.Column):
        return None, {ConversionReasonCode.NON_LITERAL_FILTER_VALUE}
    mapping, mapping_reason = _resolve_column_mapping(
        left,
        table_name,
        qualifiers,
        profile,
    )
    if mapping_reason is not None:
        return None, {mapping_reason}
    assert mapping is not None

    if isinstance(right, exp.Column):
        identifier = right.this
        if isinstance(identifier, exp.Identifier) and identifier.args.get("quoted"):
            return None, {ConversionReasonCode.AMBIGUOUS_QUOTED_IDENTIFIER}
        return None, {ConversionReasonCode.NON_LITERAL_FILTER_VALUE}

    if is_like:
        like_value, like_reason = _convert_contains_literal(right)
        if like_reason is not None:
            return None, {like_reason}
        value: str | int | float | bool = like_value
        literal_type = SourceLiteralType.STRING
    else:
        value, literal_type, literal_reason = _extract_literal(right)
        if literal_reason is not None:
            return None, {literal_reason}

    ontology_field = ontology.fields.get(mapping.semantic_field)
    if ontology_field is None:
        return None, {ConversionReasonCode.SOURCE_PROFILE_MISMATCH}
    reasons: set[ConversionReasonCode] = set()
    if operator not in ontology_field.allowed_operators:
        reasons.add(ConversionReasonCode.OPERATOR_NOT_ALLOWED_FOR_FIELD)
    if literal_type not in mapping.allowed_literal_types:
        reasons.add(ConversionReasonCode.LITERAL_TYPE_MISMATCH)
    if not _literal_matches_data_type(value, ontology_field.data_type):
        reasons.add(ConversionReasonCode.LITERAL_TYPE_MISMATCH)
    if reasons:
        return None, reasons
    return (
        IntentFilterSignature(
            field=mapping.semantic_field,
            operator=operator,
            value=value,
        ),
        set(),
    )


def _convert_contains_literal(
    node: exp.Expression,
) -> tuple[str, ConversionReasonCode | None]:
    if not isinstance(node, exp.Literal) or not node.is_string:
        return "", ConversionReasonCode.NON_LITERAL_FILTER_VALUE
    pattern = str(node.this)
    if (
        len(pattern) < 2
        or not pattern.startswith("%")
        or not pattern.endswith("%")
        or pattern.count("%") != 2
        or "_" in pattern
        or "\\" in pattern
    ):
        return "", ConversionReasonCode.UNSUPPORTED_LIKE_PATTERN
    value = pattern[1:-1]
    if value == "":
        return "", ConversionReasonCode.EMPTY_CONTAINS_VALUE
    return value, None


def _extract_literal(
    node: exp.Expression,
) -> tuple[
    str | int | float | bool,
    SourceLiteralType,
    ConversionReasonCode | None,
]:
    node = _unwrap_parentheses(node)
    if isinstance(node, exp.Boolean):
        return bool(node.this), SourceLiteralType.BOOLEAN, None
    if isinstance(node, exp.Literal) and node.is_string:
        return str(node.this), SourceLiteralType.STRING, None

    sign = 1
    if isinstance(node, exp.Neg):
        sign = -1
        node = _unwrap_parentheses(node.this)
    if not isinstance(node, exp.Literal) or node.is_string:
        return "", SourceLiteralType.STRING, ConversionReasonCode.NON_LITERAL_FILTER_VALUE
    text = str(node.this)
    signed_text = f"-{text}" if sign < 0 else text
    if _INTEGER.fullmatch(text):
        return int(signed_text), SourceLiteralType.INTEGER, None
    try:
        decimal_value = Decimal(signed_text)
        float_value = float(decimal_value)
    except (InvalidOperation, ValueError, OverflowError):
        return 0.0, SourceLiteralType.DECIMAL, ConversionReasonCode.UNSAFE_LITERAL_COERCION
    if not float_value == float_value or Decimal(str(float_value)) != decimal_value:
        return 0.0, SourceLiteralType.DECIMAL, ConversionReasonCode.UNSAFE_LITERAL_COERCION
    return float_value, SourceLiteralType.DECIMAL, None


def _literal_matches_data_type(
    value: str | int | float | bool,
    data_type: DataType,
) -> bool:
    if data_type is DataType.STRING:
        return isinstance(value, str)
    if data_type is DataType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if data_type is DataType.NUMBER:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if data_type is DataType.BOOLEAN:
        return isinstance(value, bool)
    if data_type in {DataType.DATE, DataType.TIME}:
        return isinstance(value, str)
    return False


def _convert_limit(
    select: exp.Select,
    default_limit: int,
) -> tuple[int | None, LimitProvenance, ConversionReasonCode | None]:
    limit = select.args.get("limit")
    if limit is None:
        if 1 <= default_limit <= 200:
            return default_limit, LimitProvenance.EXECUTION_POLICY_DEFAULT, None
        return None, LimitProvenance.EXECUTION_POLICY_DEFAULT, (
            ConversionReasonCode.INVALID_SOURCE_LIMIT
        )
    value = _unwrap_parentheses(limit.expression)
    if not isinstance(value, exp.Literal) or value.is_string:
        return None, LimitProvenance.SOURCE, ConversionReasonCode.INVALID_SOURCE_LIMIT
    text = str(value.this)
    if not _INTEGER.fullmatch(text):
        return None, LimitProvenance.SOURCE, ConversionReasonCode.INVALID_SOURCE_LIMIT
    parsed = int(text)
    if not 1 <= parsed <= 200:
        return None, LimitProvenance.SOURCE, ConversionReasonCode.INVALID_SOURCE_LIMIT
    return parsed, LimitProvenance.SOURCE, None


def _unwrap_parentheses(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _failed_result(
    record: DatasetRecord,
    provenance: DuplicateProvenance,
    reasons: set[ConversionReasonCode],
    **common: object,
) -> DatasetConversionResult:
    ordered = _ordered_reasons(reasons)
    status = (
        ConversionStatus.REVIEW_REQUIRED
        if any(reason in _REVIEW_REASONS for reason in reasons)
        else ConversionStatus.UNSUPPORTED
    )
    return _result(
        record,
        status=status,
        reasons=ordered,
        provenance=provenance,
        **common,
    )


def _ordered_reasons(
    reasons: set[ConversionReasonCode] | list[ConversionReasonCode],
) -> list[ConversionReasonCode]:
    reason_set = set(reasons)
    return [reason for reason in ConversionReasonCode if reason in reason_set]


def _result(
    record: DatasetRecord,
    *,
    status: ConversionStatus,
    reasons: list[ConversionReasonCode],
    analyzer_status: IrCompatibilityStatus,
    provenance: DuplicateProvenance,
    canonical_hash: str | None = None,
    shape_hash: str | None = None,
    analyzer_reason_codes: list[object] | None = None,
    query_ir: QueryIR | None = None,
    intent_signature: IntentSignature | None = None,
) -> DatasetConversionResult:
    return DatasetConversionResult(
        source_line=record.source_line,
        canonical_hash=canonical_hash,
        shape_hash=shape_hash,
        status=status,
        reason_codes=_ordered_reasons(reasons),
        analyzer_status=analyzer_status,
        analyzer_reason_codes=analyzer_reason_codes or [],
        duplicate_provenance=provenance,
        query_ir=query_ir,
        intent_signature=intent_signature,
    )
