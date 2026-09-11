from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel

from .conversion_contracts import PrivateConvertedRecord, QueryIrConversionReport
from .report import DatasetQualityReport
from .sql_contracts import SqlAnalysisReport


def write_quality_report(
    path: str | Path,
    report: DatasetQualityReport,
    *,
    overwrite: bool = False,
) -> Path:
    """Write a complete report atomically, refusing replacement by default."""

    return _write_report(path, report, overwrite=overwrite)


def write_sql_analysis_report(
    path: str | Path,
    report: SqlAnalysisReport,
    *,
    overwrite: bool = False,
) -> Path:
    """Write a SQL analysis report with the dataset report safety policy."""

    return _write_report(path, report, overwrite=overwrite)


def write_query_ir_conversion_report(
    path: str | Path,
    report: QueryIrConversionReport,
    *,
    overwrite: bool = False,
) -> Path:
    """Write a public conversion report atomically."""

    return _write_report(path, report, overwrite=overwrite)


def write_conversion_artifacts(
    report_path: str | Path,
    report: QueryIrConversionReport,
    *,
    private_path: str | Path | None = None,
    private_records: list[PrivateConvertedRecord] | None = None,
    overwrite: bool = False,
    repository_root: str | Path | None = None,
) -> tuple[Path, Path | None]:
    """Write optional private JSONL first and the public report as commit marker."""

    report_target = Path(report_path).resolve()
    private_target = Path(private_path).resolve() if private_path is not None else None
    if private_target is None:
        return write_query_ir_conversion_report(
            report_target,
            report,
            overwrite=overwrite,
        ), None
    if private_records is None:
        raise ValueError("private_records are required with private_path")
    if repository_root is None:
        raise ValueError("repository_root is required with private_path")
    _require_private_workspace(private_target, Path(repository_root))
    if report_target == private_target:
        raise ValueError("public report and private output must be different files")
    if report_target.exists() and private_target.exists() and os.path.samefile(
        report_target,
        private_target,
    ):
        raise ValueError("public report and private output must be different files")
    if not overwrite:
        for target in (private_target, report_target):
            if target.exists():
                raise FileExistsError(
                    f"artifact already exists: {target.name}; use --force to replace it"
                )

    private_text = "".join(
        record.model_dump_json() + "\n"
        for record in sorted(private_records, key=lambda item: item.source_line)
    )
    previous_private = (
        private_target.read_bytes()
        if overwrite and private_target.exists()
        else None
    )
    _write_text(private_target, private_text, overwrite=overwrite)
    try:
        _write_report(report_target, report, overwrite=overwrite)
    except Exception:
        if previous_private is not None:
            _write_bytes(private_target, previous_private)
        else:
            private_target.unlink(missing_ok=True)
        raise
    return report_target, private_target


def _write_report(
    path: str | Path,
    report: BaseModel,
    *,
    overwrite: bool,
) -> Path:
    text = report.model_dump_json(indent=2) + "\n"
    return _write_text(path, text, overwrite=overwrite)


def _write_text(
    path: str | Path,
    text: str,
    *,
    overwrite: bool,
) -> Path:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=target.name + ".",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())

        if overwrite:
            os.replace(temp_path, target)
        else:
            try:
                os.link(temp_path, target)
            except FileExistsError as exc:
                raise FileExistsError(
                    f"report already exists: {target.name}; use --force to replace it"
                ) from exc
            temp_path.unlink()
        return target
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _require_private_workspace(path: Path, repository_root: Path) -> None:
    resolved_repository = repository_root.resolve()
    private_root = (resolved_repository / ".schema-adaptive").resolve()
    try:
        path.relative_to(private_root)
    except ValueError as exc:
        raise ValueError(
            "private output must be inside the repository's ignored "
            ".schema-adaptive directory"
        ) from exc
    relative_path = path.relative_to(resolved_repository)
    tracked = subprocess.run(
        [
            "git",
            "-C",
            str(resolved_repository),
            "ls-files",
            "--error-unmatch",
            "--",
            relative_path.as_posix(),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if tracked.returncode == 0:
        raise ValueError("private output must not target a git-tracked file")


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".rollback.tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
