"""Phase 7-14 dev:all launcher: legacy .env reuse with per-part degradation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.adaptive.api import dev_launcher
from backend.adaptive.api.legacy_view_profile import DSN_ENV, PROFILE_DIR

SECRET_DSN = "postgresql://svc:s3cret-pw-714@127.0.0.1:5432/legacy714"


def _write_env(root: Path, text: str) -> None:
    (root / "backend").mkdir(parents=True, exist_ok=True)
    (root / "backend" / ".env").write_text(text, encoding="utf-8")


def _write_profile(root: Path) -> None:
    (root / PROFILE_DIR).mkdir(parents=True, exist_ok=True)
    (root / PROFILE_DIR / "profile.json").write_text("{}", encoding="utf-8")


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
    assert ollama == {"model_tag": "text2sql_qwen", "base_url": "http://127.0.0.1:11434"}
    assert status == {"legacy-view": "enabled", "ollama": "enabled"}


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


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, "disabled (MODEL_MISSING)"),
        ({"OLLAMA_MODEL": "text2sql_qwen", "OLLAMA_BASE_URL": "http://gpu-box:11434"},
         "disabled (INVALID_OR_NON_LOOPBACK_CONFIG)"),
        ({"OLLAMA_MODEL": "bad tag with spaces", "OLLAMA_BASE_URL": "http://127.0.0.1:11434"},
         "disabled (INVALID_OR_NON_LOOPBACK_CONFIG)"),
    ],
)
def test_plan_ollama_part(tmp_path: Path, env: dict[str, str], expected: str) -> None:
    _profile_ids, ollama, status = dev_launcher.plan_launch(tmp_path, env, {})
    assert ollama is None and status["ollama"] == expected


class _App:
    class state:  # noqa: N801 - mimics FastAPI app.state
        synthetic_prototype_cleanup = None


def test_main_degrades_legacy_view_and_never_prints_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_profile(tmp_path)
    _write_env(tmp_path, f"DATABASE_URL={SECRET_DSN}\nOLLAMA_MODEL=text2sql_qwen\n"
                         "OLLAMA_BASE_URL=http://127.0.0.1:11434\n")
    monkeypatch.delenv(DSN_ENV, raising=False)
    calls: list[tuple[bool, bool]] = []

    def fake_create(profiles: Any, model: Any) -> Any:
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

    def fake_create(profiles: Any, model: Any) -> Any:
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

    def fake_create(profiles: Any, model: Any) -> Any:
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


def test_main_rejects_out_of_range_port(capsys: pytest.CaptureFixture[str]) -> None:
    assert dev_launcher.main(["--port", "80"]) == 2
    assert "invalid port" in capsys.readouterr().out
