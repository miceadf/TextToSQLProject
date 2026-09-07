from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel


DatabaseWriter = Callable[[sqlite3.Connection], None]


def build_atomic_database(path: str | Path, writer: DatabaseWriter) -> Path:
    """Build a SQLite fixture beside target, then atomically replace target."""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=target.name + ".",
        suffix=".tmp",
    )
    os.close(descriptor)
    temp_path = Path(temp_name)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(temp_path)
        connection.execute("PRAGMA foreign_keys = ON")
        writer(connection)
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise RuntimeError("fixture database integrity check failed")
        connection.close()
        connection = None
        os.replace(temp_path, target)
        return target
    except Exception:
        if connection is not None:
            connection.close()
        temp_path.unlink(missing_ok=True)
        raise


def write_atomic_json(path: str | Path, model: BaseModel) -> Path:
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
            stream.write(model.model_dump_json(indent=2))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
        return target
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
