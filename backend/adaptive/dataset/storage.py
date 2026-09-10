from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .report import DatasetQualityReport


def write_quality_report(
    path: str | Path,
    report: DatasetQualityReport,
    *,
    overwrite: bool = False,
) -> Path:
    """Write a complete report atomically, refusing replacement by default."""

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
            stream.write(report.model_dump_json(indent=2))
            stream.write("\n")
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
