"""연결 제안·검토 초안·게시 profile을 DB별 JSON으로 저장하고 읽는다.

제안→검토→게시 흐름의 기록을 원자적으로 쓰고 게시 버전은 덮어쓰지 않는다.
의미 검증과 사람의 검토 결정은 review 서비스의 책임이다.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .contracts import MappingDraft, MappingProposal, PublishedMappingProfile
from .errors import MappingWorkflowError, MappingWorkflowErrorCode


ModelT = TypeVar("ModelT", bound=BaseModel)
SAFE_DATABASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class MappingRepository:
    """JSON persistence for proposals, drafts, and immutable published versions."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def save_proposal(self, proposal: MappingProposal) -> Path:
        path = self._database_dir(proposal.database_id) / "proposal.json"
        self._atomic_write(path, proposal)
        return path

    def load_proposal(self, database_id: str) -> MappingProposal:
        return self._load(
            self._database_dir(database_id) / "proposal.json",
            MappingProposal,
        )

    def save_draft(self, draft: MappingDraft) -> Path:
        path = self._database_dir(draft.database_id) / "draft.json"
        self._atomic_write(path, draft)
        return path

    def has_draft(self, database_id: str) -> bool:
        return (self._database_dir(database_id) / "draft.json").is_file()

    def load_draft(self, database_id: str) -> MappingDraft:
        return self._load(
            self._database_dir(database_id) / "draft.json",
            MappingDraft,
        )

    def next_version(self, database_id: str) -> int:
        directory = self._database_dir(database_id)
        versions = []
        for path in directory.glob("mapping-v*.json"):
            match = re.fullmatch(r"mapping-v(\d+)\.json", path.name)
            if match:
                versions.append(int(match.group(1)))
        return max(versions, default=0) + 1

    def save_published(self, profile: PublishedMappingProfile) -> Path:
        directory = self._database_dir(profile.database_id)
        version_path = directory / f"mapping-v{profile.version:04d}.json"
        if version_path.exists():
            raise MappingWorkflowError(
                MappingWorkflowErrorCode.PROPOSAL_MISMATCH,
                f"mapping version {profile.version} already exists",
            )
        self._atomic_write(version_path, profile)
        self._atomic_write(directory / "published.json", profile)
        return version_path

    def load_published(
        self,
        database_id: str,
        version: int | None = None,
    ) -> PublishedMappingProfile:
        filename = "published.json" if version is None else f"mapping-v{version:04d}.json"
        return self._load(
            self._database_dir(database_id) / filename,
            PublishedMappingProfile,
        )

    def _database_dir(self, database_id: str) -> Path:
        if not SAFE_DATABASE_ID.fullmatch(database_id):
            raise MappingWorkflowError(
                MappingWorkflowErrorCode.INVALID_DATABASE_ID,
                "database_id must use letters, numbers, '.', '_', or '-'",
            )
        directory = self._root / database_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @staticmethod
    def _load(path: Path, model_type: type[ModelT]) -> ModelT:
        if not path.is_file():
            raise MappingWorkflowError(
                MappingWorkflowErrorCode.NOT_FOUND,
                f"mapping file not found: {path.name}",
            )
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _atomic_write(path: Path, model: BaseModel) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(model.model_dump_json(indent=2))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
