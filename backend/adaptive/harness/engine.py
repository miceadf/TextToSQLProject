from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from .compiler import QueryCompiler, SingleTableSQLiteCompiler
from .contracts import (
    CompiledQuery,
    HarnessError,
    HarnessErrorCode,
    HarnessRequest,
    HarnessResult,
    HarnessStage,
    HarnessStatus,
    MappingProfile,
    Ontology,
    QueryIR,
    TraceEvent,
)
from .errors import HarnessFailure
from .executor import QueryExecutor, SQLiteReadOnlyExecutor
from .model import QueryModel
from .policy import ConservativeSqlPolicy, SqlPolicy
from .validator import validate_mapping_ready, validate_query_ir


class ExecutionHarness:
    def __init__(
        self,
        model: QueryModel,
        executor: QueryExecutor,
        *,
        compiler: QueryCompiler | None = None,
        sql_policy: SqlPolicy | None = None,
    ) -> None:
        self._model = model
        self._executor = executor
        self._compiler = compiler or SingleTableSQLiteCompiler()
        self._sql_policy = sql_policy or ConservativeSqlPolicy()

    def run(
        self,
        request_data: HarnessRequest | dict[str, Any],
        ontology: Ontology,
        mapping: MappingProfile,
    ) -> HarnessResult:
        trace: list[TraceEvent] = []
        request_id = self._request_id(request_data)
        query_ir: QueryIR | None = None
        compiled: CompiledQuery | None = None

        try:
            request = self._stage(
                trace,
                HarnessStage.REQUEST_VALIDATED,
                lambda: HarnessRequest.model_validate(request_data),
            )
            request_id = request.request_id

            self._stage(
                trace,
                HarnessStage.CONTEXT_READY,
                lambda: validate_mapping_ready(request.database_id, mapping),
                details={"mapping_version": mapping.version},
            )

            raw_ir = self._stage(
                trace,
                HarnessStage.IR_GENERATED,
                lambda: self._model.generate_ir(request, ontology, mapping),
            )
            try:
                query_ir = QueryIR.model_validate(raw_ir)
            except ValidationError as exc:
                raise HarnessFailure(
                    HarnessErrorCode.MODEL_INVALID_OUTPUT,
                    "model output is not valid QueryIR",
                    stage=HarnessStage.IR_GENERATED,
                ) from exc

            self._stage(
                trace,
                HarnessStage.IR_VALIDATED,
                lambda: validate_query_ir(query_ir, ontology, mapping),
            )
            compiled = self._stage(
                trace,
                HarnessStage.SQL_COMPILED,
                lambda: self._compiler.compile(query_ir, mapping),
            )
            self._stage(
                trace,
                HarnessStage.SQL_VALIDATED,
                lambda: self._sql_policy.validate(
                    compiled,
                    query_ir=query_ir,
                    mapping=mapping,
                ),
            )
            columns, rows = self._stage(
                trace,
                HarnessStage.EXECUTED,
                lambda: self._executor.execute(request.database_id, compiled),
            )
            self._stage(
                trace,
                HarnessStage.RESULT_VALIDATED,
                lambda: self._validate_result(columns, compiled.expected_columns),
                details={"row_count": len(rows)},
            )

            return HarnessResult(
                request_id=request_id,
                status=HarnessStatus.SUCCEEDED,
                query_ir=query_ir,
                sql=compiled.sql,
                columns=columns,
                rows=rows,
                trace=trace,
            )
        except ValidationError as exc:
            failure = HarnessFailure(
                HarnessErrorCode.INVALID_REQUEST,
                f"request is invalid: {exc.errors(include_url=False)}",
                stage=HarnessStage.REQUEST_VALIDATED,
            )
            self._append_failed_stage(trace, failure)
            return self._failure_result(request_id, query_ir, compiled, failure, trace)
        except HarnessFailure as failure:
            self._append_failed_stage(trace, failure)
            return self._failure_result(request_id, query_ir, compiled, failure, trace)

    @staticmethod
    def _request_id(request_data: HarnessRequest | dict[str, Any]) -> str:
        if isinstance(request_data, HarnessRequest):
            return request_data.request_id
        return str(request_data.get("request_id") or "unknown")

    @staticmethod
    def _stage(
        trace: list[TraceEvent],
        stage: HarnessStage,
        operation: Callable[[], Any],
        *,
        details: dict[str, Any] | None = None,
    ) -> Any:
        started = time.perf_counter()
        result = operation()
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        trace.append(
            TraceEvent(
                stage=stage,
                success=True,
                elapsed_ms=elapsed_ms,
                details=details or {},
            )
        )
        return result

    @staticmethod
    def _append_failed_stage(trace: list[TraceEvent], failure: HarnessFailure) -> None:
        if failure.stage is None:
            return
        if trace and trace[-1].stage is failure.stage and not trace[-1].success:
            return
        trace.append(
            TraceEvent(
                stage=failure.stage,
                success=False,
                elapsed_ms=0,
                details={"error_code": failure.code.value},
            )
        )

    @staticmethod
    def _validate_result(columns: list[str], expected_columns: list[str]) -> None:
        if columns != expected_columns:
            raise HarnessFailure(
                HarnessErrorCode.RESULT_SCHEMA_MISMATCH,
                f"expected columns {expected_columns}, got {columns}",
                stage=HarnessStage.RESULT_VALIDATED,
            )

    @staticmethod
    def _failure_result(
        request_id: str,
        query_ir: QueryIR | None,
        compiled: CompiledQuery | None,
        failure: HarnessFailure,
        trace: list[TraceEvent],
    ) -> HarnessResult:
        return HarnessResult(
            request_id=request_id,
            status=HarnessStatus.FAILED,
            query_ir=query_ir,
            sql=compiled.sql if compiled else None,
            error=HarnessError(
                code=failure.code,
                stage=failure.stage,
                message=failure.message,
            ),
            trace=trace,
        )
