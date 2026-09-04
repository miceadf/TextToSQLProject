from __future__ import annotations

from .contracts import HarnessErrorCode, HarnessStage


class HarnessFailure(Exception):
    def __init__(
        self,
        code: HarnessErrorCode,
        message: str,
        *,
        stage: HarnessStage | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
