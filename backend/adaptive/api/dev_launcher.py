"""Local ``npm run dev:all`` launcher for the adaptive prototype (Phase 7-14).

The legacy backend already owns ``backend/.env`` (``DATABASE_URL``,
``OLLAMA_BASE_URL``, ``OLLAMA_MODEL``). For the team demo the prototype
reuses exactly those three values so the legacy chatbot and the QueryIR
chatbot see the same database and model:

- the legacy DSN is exported in-process as the private legacy-view profile's
  DSN variable (never printed, never written) only when that Git-ignored
  profile exists and the variable is not already set;
- Ollama is enabled only for a loopback base URL, with the no-system request
  format the fine-tuned model needs (ADR-059).

Each optional part degrades on its own: a missing profile, an unreachable or
drifted database, or an invalid Ollama setting starts the prototype without
that part and prints one fixed status line. No value from ``.env`` is ever
printed. This module is a development convenience, not a deployment path.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from .legacy_view_profile import DATABASE_ID as LEGACY_VIEW_ID
from .legacy_view_profile import DSN_ENV as LEGACY_VIEW_DSN_ENV
from .legacy_view_profile import PROFILE_DIR as LEGACY_VIEW_PROFILE_DIR

ENV_FILE = Path("backend/.env")
REQUEST_FORMAT = "alpaca-raw-no-system-v1"
OLLAMA_TIMEOUT = "120"
_ENV_KEYS = frozenset({"DATABASE_URL", "OLLAMA_BASE_URL", "OLLAMA_MODEL"})
_FIXED_HOST = "127.0.0.1"

__all__ = ["main", "plan_launch", "read_env_file"]


def read_env_file(path: Path) -> dict[str, str]:
    """Read only the three known keys; comments and other keys are ignored."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return values
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if key in _ENV_KEYS:
            values[key] = value.strip().strip('"').strip("'")
    return values


def plan_launch(
    repository_root: Path,
    env_values: dict[str, str],
    environ: dict[str, str] | os._Environ[str],
) -> tuple[list[str], dict[str, str] | None, dict[str, str]]:
    """Decide the optional parts; returns (profile IDs, Ollama args, status)."""
    status: dict[str, str] = {}
    profile_ids: list[str] = []
    if not (repository_root / LEGACY_VIEW_PROFILE_DIR / "profile.json").is_file():
        status["legacy-view"] = "disabled (PROFILE_MISSING)"
    elif environ.get(LEGACY_VIEW_DSN_ENV) or env_values.get("DATABASE_URL"):
        profile_ids.append(LEGACY_VIEW_ID)
        status["legacy-view"] = "enabled"
    else:
        status["legacy-view"] = "disabled (DSN_MISSING)"

    model_tag = env_values.get("OLLAMA_MODEL")
    base_url = (env_values.get("OLLAMA_BASE_URL") or "").rstrip("/")
    ollama: dict[str, str] | None = None
    if not model_tag:
        status["ollama"] = "disabled (MODEL_MISSING)"
    else:
        try:
            from .ollama_startup import parse_ollama_startup_config

            parse_ollama_startup_config(model_tag, base_url or None, OLLAMA_TIMEOUT, REQUEST_FORMAT)
        except Exception:
            status["ollama"] = "disabled (INVALID_OR_NON_LOOPBACK_CONFIG)"
        else:
            ollama = {"model_tag": model_tag, "base_url": base_url}
            status["ollama"] = "enabled"
    return profile_ids, ollama, status


def _load_profiles(profile_ids: list[str], repository_root: Path) -> list[Any]:
    from .postgres_startup_profile import load_postgres_startup_profiles

    return load_postgres_startup_profiles(repository_root=repository_root, database_ids=profile_ids)


def _build_model(ollama: dict[str, str]) -> Any:
    from .ollama_startup import build_startup_ollama_model, parse_ollama_startup_config

    return build_startup_ollama_model(
        parse_ollama_startup_config(
            ollama["model_tag"], ollama["base_url"] or None, OLLAMA_TIMEOUT, REQUEST_FORMAT
        )
    )


def _create_app(profiles: list[Any] | None, model: Any | None) -> Any:
    from .synthetic_prototype import create_synthetic_clarification_prototype

    kwargs: dict[str, Any] = {}
    if profiles:
        kwargs["postgres_profiles"] = profiles
    if model is not None:
        kwargs["ollama_model"] = model
    return create_synthetic_clarification_prototype(**kwargs)


def _print_status(status: dict[str, str]) -> None:
    print(
        "adaptive dev launcher: "
        + ", ".join(f"{name}={value}" for name, value in sorted(status.items())),
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.adaptive.api.dev_launcher", allow_abbrev=False)
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--repository-root", default=str(Path.cwd()))
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    if not 8000 <= args.port <= 8999:
        print("adaptive dev launcher: invalid port", flush=True)
        return 2
    repository_root = Path(args.repository_root)
    env_values = read_env_file(repository_root / ENV_FILE)
    profile_ids, ollama, status = plan_launch(repository_root, env_values, os.environ)
    if profile_ids and not os.environ.get(LEGACY_VIEW_DSN_ENV):
        # Same DSN as the legacy backend; exported only inside this process.
        os.environ[LEGACY_VIEW_DSN_ENV] = env_values["DATABASE_URL"]

    # Each optional part is prepared on its own, so one failing never
    # disables the other.
    profiles: list[Any] | None = None
    if profile_ids:
        try:
            profiles = _load_profiles(profile_ids, repository_root)
        except Exception:
            status["legacy-view"] = "disabled (PROFILE_INVALID)"
    model: Any | None = None
    if ollama is not None:
        try:
            model = _build_model(ollama)
        except Exception:
            status["ollama"] = "disabled (STARTUP_FAILED)"

    app: Any = None
    try:
        app = _create_app(profiles, model)
    except Exception:
        app = None
    if app is None and profiles:
        # Assembly inspects the DB: an unreachable or drifted DB must not
        # take the prototype down.
        status["legacy-view"] = "disabled (STARTUP_VALIDATION_FAILED)"
        try:
            app = _create_app(None, model)
        except Exception:
            app = None
    if app is None and model is not None:
        status["ollama"] = "disabled (STARTUP_FAILED)"
        try:
            app = _create_app(None, None)
        except Exception:
            app = None
    if app is None:
        print("adaptive dev launcher: prototype unavailable", flush=True)
        return 2
    _print_status(status)
    try:
        import uvicorn

        uvicorn.run(app, host=_FIXED_HOST, port=args.port, reload=False, access_log=False)
    except Exception:
        print("adaptive dev launcher: prototype unavailable", flush=True)
        return 2
    finally:
        cleanup = getattr(getattr(app, "state", None), "synthetic_prototype_cleanup", None)
        if callable(cleanup):
            cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
