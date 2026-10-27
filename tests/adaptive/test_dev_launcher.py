"""Phase 7-14 dev:all launcher: legacy .env reuse with per-part degradation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.adaptive.api import dev_launcher
from backend.adaptive.api.legacy_view_profile import (
    BASE_DSN_ENV,
    BASE_PROFILE_DIR,
    DSN_ENV,
    PROFILE_DIR,
)

SECRET_DSN = "postgresql://svc:s3cret-pw-714@127.0.0.1:5432/legacy714"


def _write_env(root: Path, text: str) -> None:
    (root / "backend").mkdir(parents=True, exist_ok=True)
    (root / "backend" / ".env").write_text(text, encoding="utf-8")


def _write_profile(root: Path, directory: Path = PROFILE_DIR) -> None:
    (root / directory).mkdir(parents=True, exist_ok=True)
    (root / directory / "profile.json").write_text("{}", encoding="utf-8")


def test_read_env_file_keeps_only_known_keys(tmp_path: Path) -> None:
    _write_env(
        tmp_path,
        "# comment\n"
        f'DATABASE_URL="{SECRET_DSN}"\n'
        "export OLLAMA_BASE_URL=http://127.0.0.1:11434/\n"
        "OLLAMA_MODEL='text2sql_qwen'\n"
        "REDIS_URL=redis://secret\n"
        "garbage line\n",
    )
    values = dev_launcher.read_env_file(tmp_path / "backend" / ".env")
    assert values == {
        "DATABASE_URL": SECRET_DSN,
        "OLLAMA_BASE_URL": "http://127.0.0.1:11434/",
        "OLLAMA_MODEL": "text2sql_qwen",
    }
    assert dev_launcher.read_env_file(tmp_path / "missing.env") == {}


def test_plan_enables_both_parts_when_configured(tmp_path: Path) -> None:
    _write_profile(tmp_path)
    env = {
        "DATABASE_URL": SECRET_DSN,
        "OLLAMA_BASE_URL": "http://127.0.0.1:11434/",
        "OLLAMA_MODEL": "text2sql_qwen",
    }
    profile_ids, ollama, status = dev_launcher.plan_launch(tmp_path, env, {})
    assert profile_ids == ["legacy-view"]
    # Phase 9-08: the general model first (the default), the legacy-tuned one to compare.
    assert ollama == [
        {"id": "general", "model_tag": "qwen2.5-coder:7b-instruct",
         "base_url": "http://127.0.0.1:11434", "request_format": "model-template",
         "output_format": "queryir-schema-v1", "prompt_version": "query-ir-v3"},
        {"id": "legacy-tuned", "model_tag": "text2sql_qwen",
         "base_url": "http://127.0.0.1:11434", "request_format": "alpaca-raw-no-system-v1",
         "output_format": "none", "prompt_version": "query-ir-v1"},
    ]
    assert status == {
        "legacy-base": "disabled (PROFILE_MISSING)",
        "legacy-view": "enabled",
        "ollama": "enabled",
        "chat_model.general": "enabled",
        "chat_model.legacy-tuned": "enabled",
    }
    _write_profile(tmp_path, BASE_PROFILE_DIR)
    profile_ids, _ollama, status = dev_launcher.plan_launch(tmp_path, env, {})
    assert profile_ids == ["legacy-base", "legacy-view"]
    assert status["legacy-base"] == "enabled"


@pytest.mark.parametrize(
    ("profile", "env", "environ", "expected"),
    [
        (False, {"DATABASE_URL": SECRET_DSN}, {}, "disabled (PROFILE_MISSING)"),
        (True, {}, {}, "disabled (DSN_MISSING)"),
        (True, {}, {DSN_ENV: SECRET_DSN}, "enabled"),
    ],
)
def test_plan_legacy_view_part(
    tmp_path: Path, profile: bool, env: dict[str, str], environ: dict[str, str], expected: str
) -> None:
    if profile:
        _write_profile(tmp_path)
    profile_ids, _ollama, status = dev_launcher.plan_launch(tmp_path, env, environ)
    assert status["legacy-view"] == expected
    assert profile_ids == (["legacy-view"] if expected == "enabled" else [])


def test_plan_ollama_part_rejects_a_non_loopback_url(tmp_path: Path) -> None:
    env = {"OLLAMA_MODEL": "text2sql_qwen", "OLLAMA_BASE_URL": "http://gpu-box:11434"}
    _profile_ids, ollama, status = dev_launcher.plan_launch(tmp_path, env, {})
    assert ollama is None and status["ollama"] == "disabled (INVALID_OR_NON_LOOPBACK_CONFIG)"


@pytest.mark.parametrize(
    ("env", "ids", "dropped"),
    [
        # Without OLLAMA_MODEL the chat still runs on the general model.
        ({}, ["general"], None),
        ({"OLLAMA_MODEL": "bad tag with spaces", "OLLAMA_BASE_URL": "http://127.0.0.1:11434"},
         ["general"], "chat_model.legacy-tuned"),
        ({"CHAT_MODEL": "bad tag", "OLLAMA_MODEL": "text2sql_qwen"}, ["legacy-tuned"], "chat_model.general"),
        # The same tag twice: only the fine-tuned format, which that model needs.
        ({"CHAT_MODEL": "text2sql_qwen", "OLLAMA_MODEL": "text2sql_qwen"}, ["legacy-tuned"], None),
        ({"CHAT_MODEL": "llama3.1:8b", "OLLAMA_MODEL": "text2sql_qwen"}, ["general", "legacy-tuned"], None),
    ],
)
def test_plan_chat_model_choices(
    tmp_path: Path, env: dict[str, str], ids: list[str], dropped: str | None
) -> None:
    _profile_ids, ollama, status = dev_launcher.plan_launch(tmp_path, env, {})
    assert ollama is not None and [item["id"] for item in ollama] == ids
    assert status["ollama"] == "enabled"
    if dropped:
        assert status[dropped] == "disabled (INVALID_OR_NON_LOOPBACK_CONFIG)"
    if "CHAT_MODEL" in env and "general" in ids:
        assert ollama[0]["model_tag"] == env["CHAT_MODEL"]


def test_one_chat_model_failing_keeps_the_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_env(tmp_path, "OLLAMA_MODEL=text2sql_qwen\nOLLAMA_BASE_URL=http://127.0.0.1:11434\n")
    seen: list[Any] = []

    def build(spec: dict[str, str]) -> Any:
        if spec["id"] == "general":
            raise RuntimeError("not pulled")
        return "tuned-model"

    def fake_create(
        profiles: Any, model: Any, mapping_model: Any = None, show_sql: bool = False, postgres_sources: Any = None,
        **_custom_chat_models: Any,  # Phase 9-31: repository root and Ollama URL for user models
    ) -> Any:
        seen.append(model)
        return _App()

    monkeypatch.setattr(dev_launcher, "_build_model", build)
    monkeypatch.setattr(dev_launcher, "_create_app", fake_create)
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    assert dev_launcher.main(["--repository-root", str(tmp_path)]) == 0
    assert [(choice.id, choice.tag, choice.model) for choice in seen[0]] == [
        ("legacy-tuned", "text2sql_qwen", "tuned-model")
    ]
    out = capsys.readouterr().out
    assert "chat_model.general=disabled (STARTUP_FAILED)" in out and "ollama=enabled" in out
    assert "text2sql_qwen" not in out  # .env values are never printed


class _App:
    class state:  # noqa: N801 - mimics FastAPI app.state
        adaptive_app_cleanup = None


def test_main_degrades_legacy_view_and_never_prints_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_profile(tmp_path)
    _write_env(tmp_path, f"DATABASE_URL={SECRET_DSN}\nOLLAMA_MODEL=text2sql_qwen\n"
                         "OLLAMA_BASE_URL=http://127.0.0.1:11434\n")
    monkeypatch.delenv(DSN_ENV, raising=False)
    calls: list[tuple[bool, bool]] = []

    def fake_create(
        profiles: Any, model: Any, mapping_model: Any = None, show_sql: bool = False, postgres_sources: Any = None,
        **_custom_chat_models: Any,  # Phase 9-31: repository root and Ollama URL for user models
    ) -> Any:
        calls.append((bool(profiles), model is not None))
        if profiles:
            raise RuntimeError("drifted " + SECRET_DSN)
        return _App()

    ran: list[Any] = []
    monkeypatch.setattr(dev_launcher, "_load_profiles", lambda ids, root: ["profile"])
    monkeypatch.setattr(dev_launcher, "_build_model", lambda ollama: "model")
    monkeypatch.setattr(dev_launcher, "_create_app", fake_create)
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: ran.append(kwargs))
    assert dev_launcher.main(["--repository-root", str(tmp_path)]) == 0
    assert calls == [(True, True), (False, True)]
    assert ran and ran[0]["host"] == "127.0.0.1" and ran[0]["port"] == 8123
    out = capsys.readouterr()
    assert "legacy-view=disabled (STARTUP_VALIDATION_FAILED)" in out.out
    assert "ollama=enabled" in out.out
    assert "s3cret" not in out.out + out.err and "11434" not in out.out
    # The DSN is exported in-process only for the profile loader.
    import os

    assert os.environ.get(DSN_ENV) == SECRET_DSN


def test_ollama_failure_keeps_legacy_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_profile(tmp_path)
    _write_env(tmp_path, f"DATABASE_URL={SECRET_DSN}\nOLLAMA_MODEL=text2sql_qwen\n"
                         "OLLAMA_BASE_URL=http://127.0.0.1:11434\n")
    monkeypatch.delenv(DSN_ENV, raising=False)
    calls: list[tuple[bool, bool]] = []

    def broken_model(_ollama: Any) -> Any:
        raise RuntimeError("transport")

    def fake_create(
        profiles: Any, model: Any, mapping_model: Any = None, show_sql: bool = False, postgres_sources: Any = None,
        **_custom_chat_models: Any,  # Phase 9-31: repository root and Ollama URL for user models
    ) -> Any:
        calls.append((bool(profiles), model is not None))
        return _App()

    monkeypatch.setattr(dev_launcher, "_load_profiles", lambda ids, root: ["profile"])
    monkeypatch.setattr(dev_launcher, "_build_model", broken_model)
    monkeypatch.setattr(dev_launcher, "_create_app", fake_create)
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    assert dev_launcher.main(["--repository-root", str(tmp_path)]) == 0
    assert calls == [(True, False)]
    out = capsys.readouterr().out
    assert "legacy-view=enabled" in out and "ollama=disabled (STARTUP_FAILED)" in out


def test_invalid_profile_keeps_ollama(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_profile(tmp_path)
    _write_env(tmp_path, f"DATABASE_URL={SECRET_DSN}\nOLLAMA_MODEL=text2sql_qwen\n"
                         "OLLAMA_BASE_URL=http://127.0.0.1:11434\n")
    monkeypatch.delenv(DSN_ENV, raising=False)
    calls: list[tuple[bool, bool]] = []

    def broken_profiles(_ids: Any, _root: Any) -> Any:
        raise RuntimeError("bad profile")

    def fake_create(
        profiles: Any, model: Any, mapping_model: Any = None, show_sql: bool = False, postgres_sources: Any = None,
        **_custom_chat_models: Any,  # Phase 9-31: repository root and Ollama URL for user models
    ) -> Any:
        calls.append((bool(profiles), model is not None))
        return _App()

    monkeypatch.setattr(dev_launcher, "_load_profiles", broken_profiles)
    monkeypatch.setattr(dev_launcher, "_build_model", lambda ollama: "model")
    monkeypatch.setattr(dev_launcher, "_create_app", fake_create)
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    assert dev_launcher.main(["--repository-root", str(tmp_path)]) == 0
    assert calls == [(False, True)]
    out = capsys.readouterr().out
    assert "legacy-view=disabled (PROFILE_INVALID)" in out and "ollama=enabled" in out


def test_drifted_base_profile_keeps_the_view_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_profile(tmp_path)
    _write_profile(tmp_path, BASE_PROFILE_DIR)
    _write_env(tmp_path, f"DATABASE_URL={SECRET_DSN}\n")
    monkeypatch.delenv(DSN_ENV, raising=False)
    monkeypatch.delenv(BASE_DSN_ENV, raising=False)
    calls: list[list[str]] = []

    def fake_create(
        profiles: Any, model: Any, mapping_model: Any = None, show_sql: bool = False, postgres_sources: Any = None,
        **_custom_chat_models: Any,  # Phase 9-31: repository root and Ollama URL for user models
    ) -> Any:
        calls.append(list(profiles or []))
        if profiles and "legacy-base" in profiles:
            raise RuntimeError("drifted")
        return _App()

    monkeypatch.setattr(dev_launcher, "_load_profiles", lambda ids, root: list(ids))
    monkeypatch.setattr(dev_launcher, "_create_app", fake_create)
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    assert dev_launcher.main(["--repository-root", str(tmp_path)]) == 0
    assert calls == [["legacy-base", "legacy-view"], ["legacy-base"], ["legacy-view"]]
    out = capsys.readouterr().out
    assert "legacy-base=disabled (STARTUP_VALIDATION_FAILED)" in out
    assert "legacy-view=enabled" in out
    assert "s3cret" not in out
    import os

    # Each profile has its own DSN variable, both exported in-process.
    assert os.environ.get(DSN_ENV) == SECRET_DSN and os.environ.get(BASE_DSN_ENV) == SECRET_DSN


def test_one_invalid_profile_keeps_the_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_profile(tmp_path)
    _write_profile(tmp_path, BASE_PROFILE_DIR)
    _write_env(tmp_path, f"DATABASE_URL={SECRET_DSN}\n")
    monkeypatch.delenv(DSN_ENV, raising=False)
    monkeypatch.delenv(BASE_DSN_ENV, raising=False)
    calls: list[list[str]] = []

    def load(ids: Any, _root: Any) -> Any:
        if ids == ["legacy-view"]:
            raise RuntimeError("bad profile")
        return list(ids)

    def fake_create(
        profiles: Any, model: Any, mapping_model: Any = None, show_sql: bool = False, postgres_sources: Any = None,
        **_custom_chat_models: Any,  # Phase 9-31: repository root and Ollama URL for user models
    ) -> Any:
        calls.append(list(profiles or []))
        return _App()

    monkeypatch.setattr(dev_launcher, "_load_profiles", load)
    monkeypatch.setattr(dev_launcher, "_create_app", fake_create)
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    assert dev_launcher.main(["--repository-root", str(tmp_path)]) == 0
    assert calls == [["legacy-base"]]
    out = capsys.readouterr().out
    assert "legacy-base=enabled" in out and "legacy-view=disabled (PROFILE_INVALID)" in out


def test_main_rejects_out_of_range_port(capsys: pytest.CaptureFixture[str]) -> None:
    assert dev_launcher.main(["--port", "80"]) == 2
    assert "invalid port" in capsys.readouterr().out


def test_main_does_not_probe_ollama_for_custom_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_env(tmp_path, "OLLAMA_MODEL=text2sql_qwen\nOLLAMA_BASE_URL=http://127.0.0.1:11434\n")
    monkeypatch.setattr(dev_launcher, "_build_model", lambda _spec: "model")
    monkeypatch.setattr(dev_launcher, "_create_app", lambda *_args, **_kwargs: _App())
    from backend.adaptive.api import custom_chat_models

    def fail_probe(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("launcher must not probe Ollama")

    monkeypatch.setattr(custom_chat_models, "installed_tags", fail_probe)
    monkeypatch.setattr(custom_chat_models, "load_live_custom", fail_probe)
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    assert dev_launcher.main(["--repository-root", str(tmp_path)]) == 0
