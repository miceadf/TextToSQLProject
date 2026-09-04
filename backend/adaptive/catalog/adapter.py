from __future__ import annotations

from typing import Protocol

from .contracts import DatabaseCatalog


class DatabaseAdapter(Protocol):
    """Read a physical database schema into the common catalog format."""

    def inspect(
        self,
        database_id: str,
        *,
        include_samples: bool = False,
    ) -> DatabaseCatalog:
        """Return a deterministic catalog without mutating the database."""
