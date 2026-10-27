"""Local ``npm run dev:all`` launcher for the adaptive prototype (Phase 7-14).

The launcher reads known database, model, and display settings from
``backend/.env``, including additional ``PG_SOURCE_<NAME>`` connections.
The legacy database connection can be reused, while the QueryIR chatbot
offers separate general, legacy comparison, and optional external models:

- the legacy DSN is exported in-process as each private legacy profile's
  DSN variable (never printed, never written) only when that Git-ignored
  profile exists and the variable is not already set (Phase 7-17: the view
  profile and the base-table profile);
- Ollama is enabled only for a loopback base URL. Phase 9-08 (ADR-098): the
  QueryIR chat offers a general model (``CHAT_MODEL``, default
  ``qwen2.5-coder:7b-instruct``, model template) as its default and the
  legacy chatbot's fine-tuned ``OLLAMA_MODEL`` (no-system request format,
  ADR-059) as a comparison choice; the user picks one per question.
  Phase 9-09 (ADR-099): with ``OPENAI_API_KEY`` the ChatGPT API
  (``OPENAI_MODEL``, default ``gpt-5-mini``) is one more choice; the key is
  used only in its request header;
- upload mapping proposals (Phase 8-12) use the same loopback base URL (or the
  default one) with ``MAPPING_MODEL`` or the benchmarked default tag;
- the chat shows the executed SQL like the legacy chatbot (Phase 8-14, a
  development aid for checking answers); ``SHOW_SQL=0`` turns it off;
- PostgreSQL schemas can be registered from the admin screen (Phase 8-15)
  through server-owned connections: ``DATABASE_URL`` and ``PG_SOURCE_<NAME>``.
  Registrations published earlier are loaded again at startup.

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

from .legacy_view_profile import BASE_SPEC, VIEW_SPEC

# Legacy profiles the launcher can enable, in status order.
LEGACY_SPECS = (BASE_SPEC, VIEW_SPEC)

ENV_FILE = Path("backend/.env")
REQUEST_FORMAT = "alpaca-raw-no-system-v1"
OLLAMA_TIMEOUT = "120"
_ENV_KEYS = frozenset(
    {
        "DATABASE_URL", "OLLAMA_BASE_URL", "OLLAMA_MODEL", "CHAT_MODEL", "MAPPING_MODEL", "SHOW_SQL",
        "OPENAI_API_KEY", "OPENAI_MODEL",
    }
)
_FIXED_HOST = "127.0.0.1"

__all__ = ["main", "plan_chat_models", "plan_launch", "plan_mapping_model", "read_env_file"]


def read_env_file(path: Path) -> dict[str, str]:
    """Read only the known keys; comments and other keys are ignored."""
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
        if key in _ENV_KEYS or key.startswith("PG_SOURCE_"):
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
    for spec in LEGACY_SPECS:
        if not (repository_root / spec.profile_dir / "profile.json").is_file():
            status[spec.database_id] = "disabled (PROFILE_MISSING)"
        elif environ.get(spec.dsn_env) or env_values.get("DATABASE_URL"):
            profile_ids.append(spec.database_id)
            status[spec.database_id] = "enabled"
        else:
            status[spec.database_id] = "disabled (DSN_MISSING)"

    ollama, model_status = plan_chat_models(env_values)
    status.update(model_status)
    return profile_ids, ollama, status


class _Secret:
    """A value that never shows in a repr, str or log line (Luna 9-09 review)."""

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    def __repr__(self) -> str:
        return "_Secret(***)"

    __str__ = __repr__


def plan_chat_models(
    env_values: dict[str, str],
) -> tuple[list[dict[str, str]] | None, dict[str, str]]:
    """The chat's model choices, default first (Phase 9-08), and status lines.

    ``general`` is ``CHAT_MODEL`` or the default tag with the model template;
    ``legacy-tuned`` is the legacy chatbot's ``OLLAMA_MODEL`` with the
    no-system format. The same tag twice keeps only ``legacy-tuned``.
    ``openai`` (Phase 9-09) is added last when ``OPENAI_API_KEY`` is set.
    """
    from .chat_models import DEFAULT_CHAT_MODEL, GENERAL_MODEL_ID, LEGACY_TUNED_MODEL_ID
    from .ollama_startup import parse_ollama_startup_config

    base_url = (env_values.get("OLLAMA_BASE_URL") or "").rstrip("/")
    general = env_values.get("CHAT_MODEL") or DEFAULT_CHAT_MODEL
    tuned = env_values.get("OLLAMA_MODEL")
    # A general model is held to the QueryIR shape by a JSON schema and told
    # what the fine-tuned one learned (query-ir-v3); the fine-tuned one keeps
    # its exact pre-9-08 request.
    wanted = [] if general == tuned else [
        (GENERAL_MODEL_ID, general, "model-template", "queryir-schema-v1", "query-ir-v3")
    ]
    if tuned:
        wanted.append((LEGACY_TUNED_MODEL_ID, tuned, REQUEST_FORMAT, "none", "query-ir-v1"))
    status: dict[str, str] = {}
    choices: list[dict[str, str]] = []
    for model_id, tag, request_format, output_format, prompt_version in wanted:
        try:
            parse_ollama_startup_config(
                tag, base_url or None, OLLAMA_TIMEOUT, request_format, output_format, prompt_version
            )
        except Exception:
            status[f"chat_model.{model_id}"] = "disabled (INVALID_OR_NON_LOOPBACK_CONFIG)"
            continue
        choices.append({
            "id": model_id, "model_tag": tag, "base_url": base_url,
            "request_format": request_format, "output_format": output_format,
            "prompt_version": prompt_version,
        })
        status[f"chat_model.{model_id}"] = "enabled"
    status["ollama"] = "enabled" if choices else "disabled (INVALID_OR_NON_LOOPBACK_CONFIG)"
    api_key = env_values.get("OPENAI_API_KEY")
    if api_key:
        from ..harness.openai_chat import DEFAULT_OPENAI_MODEL, valid_openai_settings
        from .chat_models import OPENAI_MODEL_ID

        model = env_values.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
        if valid_openai_settings(api_key, model):
            choices.append(
                {"id": OPENAI_MODEL_ID, "provider": "openai", "model_tag": model, "api_key": _Secret(api_key)}
            )
            status[f"chat_model.{OPENAI_MODEL_ID}"] = "enabled"
        else:
            status[f"chat_model.{OPENAI_MODEL_ID}"] = "disabled (INVALID_CONFIG)"
    return (choices or None), status


def plan_mapping_model(env_values: dict[str, str]) -> tuple[dict[str, str] | None, str]:
    """Upload mapping proposals: (model args or None, status text)."""
    from ..mapping.ai_proposal import DEFAULT_MAPPING_BASE_URL, DEFAULT_MAPPING_MODEL
    from .ollama_startup import parse_ollama_startup_config

    tag = env_values.get("MAPPING_MODEL") or DEFAULT_MAPPING_MODEL
    base_url = (env_values.get("OLLAMA_BASE_URL") or "").rstrip("/") or DEFAULT_MAPPING_BASE_URL
    try:
        parse_ollama_startup_config(tag, base_url, OLLAMA_TIMEOUT)
    except Exception:
        return None, "disabled (INVALID_OR_NON_LOOPBACK_CONFIG)"
    return {"model_tag": tag, "base_url": base_url}, "enabled"


def _build_mapping_model(settings: dict[str, str]) -> Any:
    from ..mapping.ai_proposal import OllamaJsonModel

    return OllamaJsonModel(
        settings["model_tag"], base_url=settings["base_url"], timeout_seconds=int(OLLAMA_TIMEOUT)
    )


def _load_profiles(profile_ids: list[str], repository_root: Path) -> list[Any]:
    from .postgres_startup_profile import load_postgres_startup_profiles

    return load_postgres_startup_profiles(repository_root=repository_root, database_ids=profile_ids)


def _build_model(ollama: dict[str, str]) -> Any:
    from .ollama_startup import build_startup_ollama_model, parse_ollama_startup_config

    if ollama.get("provider") == "openai":
        from ..harness.openai_chat import OpenAIQueryModel

        return OpenAIQueryModel(ollama["api_key"].value, ollama["model_tag"])

    return build_startup_ollama_model(
        parse_ollama_startup_config(
            ollama["model_tag"], ollama["base_url"] or None, OLLAMA_TIMEOUT,
            ollama["request_format"], ollama["output_format"], ollama["prompt_version"],
        )
    )


def _build_choices(ollama: list[dict[str, str]], status: dict[str, str]) -> list[Any] | None:
    """Build each chat model on its own; one failing keeps the others."""
    from ..harness.openai_chat import OpenAIQueryModel
    from .chat_models import MODEL_LABELS, ChatModelChoice

    choices: list[Any] = []
    for spec in ollama:
        try:
            model = _build_model(spec)
        except Exception:
            status[f"chat_model.{spec['id']}"] = "disabled (STARTUP_FAILED)"
            continue
        choices.append(
            ChatModelChoice(
                id=spec["id"], label=MODEL_LABELS[spec["id"]], tag=spec["model_tag"], model=model
            )
        )
    if not any(not isinstance(choice.model, OpenAIQueryModel) for choice in choices):
        status["ollama"] = "disabled (STARTUP_FAILED)"
    if not choices:
        return None
    return choices


def _postgres_sources(env_values: dict[str, str]) -> dict[str, Any]:
    from ..registration.postgres_registrations import sources_from_env

    return sources_from_env(env_values)


def _published_postgres(repository_root: Path) -> list[tuple[str, str, str]]:
    from ..registration.postgres_registrations import published_registrations

    return published_registrations(repository_root)


def _dsn_env_for(database_id: str) -> str:
    from ..registration.postgres_registrations import dsn_env_for

    return dsn_env_for(database_id)


def _create_app(
    profiles: list[Any] | None,
    model: list[Any] | None,
    mapping_model: Any | None = None,
    show_sql: bool = False,
    postgres_sources: dict[str, Any] | None = None,
    repository_root: Path | None = None,
    chat_model_base_url: str | None = None,
) -> Any:
    from .app_factory import create_synthetic_clarification_prototype

    kwargs: dict[str, Any] = {}
    if postgres_sources is not None:
        kwargs["postgres_sources"] = postgres_sources
    if show_sql:
        kwargs["show_chat_sql"] = True
    if profiles:
        kwargs["postgres_profiles"] = profiles
    if model is not None:
        kwargs["chat_models"] = model
    if repository_root is not None and chat_model_base_url is not None:
        kwargs["chat_model_repository_root"] = repository_root
        kwargs["chat_model_base_url"] = chat_model_base_url
        kwargs["chat_model_timeout"] = OLLAMA_TIMEOUT
    if mapping_model is not None:
        kwargs["mapping_model"] = mapping_model
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
    for spec in LEGACY_SPECS:
        if spec.database_id in profile_ids and not os.environ.get(spec.dsn_env):
            # Same DSN as the legacy backend; exported only inside this process.
            os.environ[spec.dsn_env] = env_values["DATABASE_URL"]
    postgres_sources = _postgres_sources(env_values)
    status["pg_registration"] = f"enabled ({len(postgres_sources)} sources)"
    for database_id, source_id, identity in _published_postgres(repository_root):
        source = postgres_sources.get(source_id)
        if source is None:
            status[database_id] = "disabled (SOURCE_MISSING)"
            continue
        if source.identity() != identity:
            status[database_id] = "disabled (SOURCE_CHANGED)"
            continue
        # The source DSN, exported only inside this process for the loader.
        os.environ[_dsn_env_for(database_id)] = source._dsn  # noqa: SLF001
        profile_ids.append(database_id)
        status[database_id] = "enabled"

    # Each optional part is prepared on its own, so one failing never
    # disables another.
    loaded: list[tuple[str, Any]] = []
    for database_id in profile_ids:
        try:
            [profile] = _load_profiles([database_id], repository_root)
        except Exception:
            status[database_id] = "disabled (PROFILE_INVALID)"
        else:
            loaded.append((database_id, profile))
    model: list[Any] | None = None
    custom_base_url: str | None = None
    if ollama is not None:
        model = _build_choices(ollama, status)
    if model:
        from .ollama_startup import DEFAULT_OLLAMA_BASE_URL

        custom_base_url = (env_values.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL).rstrip("/")
    mapping_settings, status["mapping_ai"] = plan_mapping_model(env_values)
    show_sql = env_values.get("SHOW_SQL", "1").strip() != "0"
    status["chat_sql"] = "shown" if show_sql else "hidden"
    mapping_model: Any | None = None
    if mapping_settings is not None:
        try:
            mapping_model = _build_mapping_model(mapping_settings)
        except Exception:
            status["mapping_ai"] = "disabled (STARTUP_FAILED)"

    def create(profiles: list[Any] | None, with_model: Any | None) -> Any:
        try:
            if show_sql:
                return _create_app(
                    profiles, with_model, mapping_model, True, postgres_sources=postgres_sources,
                    repository_root=repository_root if custom_base_url is not None else None,
                    chat_model_base_url=custom_base_url,
                )
            return _create_app(
                profiles, with_model, mapping_model, postgres_sources=postgres_sources,
                repository_root=repository_root if custom_base_url is not None else None,
                chat_model_base_url=custom_base_url,
            )
        except Exception:
            return None

    # Assembly inspects the DB: an unreachable or drifted DB must not take
    # the prototype down, and one drifted profile must not disable another.
    app: Any = None
    kept: set[str] = set()
    attempts = [loaded] + ([[item] for item in loaded] if len(loaded) > 1 else [])
    for subset in attempts:
        if not subset:
            continue
        app = create([profile for _, profile in subset], model)
        if app is not None:
            kept = {database_id for database_id, _ in subset}
            break
    for database_id, _profile in loaded:
        if database_id not in kept:
            status[database_id] = "disabled (STARTUP_VALIDATION_FAILED)"
    if app is None:
        app = create(None, model)
    if app is None and model is not None:
        status["ollama"] = "disabled (STARTUP_FAILED)"
        app = create(None, None)
    if app is None:
        print("adaptive dev launcher: prototype unavailable", flush=True)
        return 2
    custom_status = getattr(getattr(app, "state", None), "custom_chat_model_status", {})
    if isinstance(custom_status, dict):
        status.update(custom_status)
    _print_status(status)
    try:
        import uvicorn

        uvicorn.run(app, host=_FIXED_HOST, port=args.port, reload=False, access_log=False)
    except Exception:
        print("adaptive dev launcher: prototype unavailable", flush=True)
        return 2
    finally:
        cleanup = getattr(getattr(app, "state", None), "adaptive_app_cleanup", None)
        if callable(cleanup):
            cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
