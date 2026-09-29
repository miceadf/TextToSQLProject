"""Phase 5-19: live paired arm CLI, verified with synthetic transports only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import backend.adaptive.api.ollama_startup as startup
from backend.adaptive.catalog.contracts import DatabaseCatalog
from backend.adaptive.evaluation import arm_live_cli as cli
from backend.adaptive.evaluation.direct_sql import build_direct_sql_prompt
from backend.adaptive.evaluation.loader import load_evaluation_set
from backend.adaptive.evaluation.ollama_request_format import (
    ALPACA_NO_SYSTEM_PREFIX,
    ALPACA_NO_SYSTEM_SUFFIX,
    RequestFormatError,
    apply_request_format,
)
from backend.adaptive.evaluation.runner import _prepare_fixture_contexts
from backend.adaptive.fixtures import EXTENDED_VARIANTS, build_fixture_bundle
from backend.adaptive.fixtures.contracts import FixtureVariant
from backend.adaptive.fixtures.seed import DEFAULT_SEED
from backend.adaptive.ontology.loader import load_default_ontology
from tests.adaptive.test_direct_sql_arm import _oracle_sql

TAG = "synthetic-p519:latest"
SECRET = "p519secret"


@pytest.fixture(scope="module")
def oracle_transport(tmp_path_factory: Any) -> Any:
    ontology = load_default_ontology()
    gold = load_evaluation_set(cli.DEVELOPMENT_SET_PATH, ontology=ontology)
    # Extended catalogs include the core ones, so one transport serves both suites.
    bundle = build_fixture_bundle(
        tmp_path_factory.mktemp("p519") / "databases", ontology=ontology, variants=EXTENDED_VARIANTS
    )
    contexts, _ = _prepare_fixture_contexts(bundle, DEFAULT_SEED, ontology, None)
    by_db_question = _oracle_sql(gold, contexts)
    sql_by_prompt: dict[str, str] = {}
    for variant in contexts:
        artifact = contexts[variant][0]
        catalog = DatabaseCatalog.model_validate_json(artifact.catalog_path.read_text(encoding="utf-8"))
        for case in gold.cases:
            prompt = build_direct_sql_prompt(catalog, case.question_ko)
            sql_by_prompt[prompt] = by_db_question[(artifact.database_id, case.question_ko)]
    ir_by_question = {case.question_ko: case.gold_query_ir.model_dump(mode="json") for case in gold.cases}

    class _Transport:
        def __init__(self) -> None:
            self.calls = 0
            self.raw_flags: list[Any] = []

        def __call__(self, url: str, payload: dict[str, Any], timeout: float) -> Any:
            self.calls += 1
            prompt = payload["prompt"]
            self.raw_flags.append(payload.get("raw"))
            if payload.get("raw"):
                assert prompt.startswith(ALPACA_NO_SYSTEM_PREFIX) and prompt.endswith(ALPACA_NO_SYSTEM_SUFFIX)
                prompt = prompt[len(ALPACA_NO_SYSTEM_PREFIX):-len(ALPACA_NO_SYSTEM_SUFFIX)]
            if prompt in sql_by_prompt:
                return {"response": sql_by_prompt[prompt]}
            question = prompt.rsplit("Question: ", 1)[1]
            return {"response": json.dumps(ir_by_question[question], ensure_ascii=False)}

    return _Transport


def _run(argv: list[str], capsys: Any) -> tuple[int, dict[str, Any], str]:
    try:
        code = cli.main(argv)
    except SystemExit as exc:
        code = exc.code
    out, err = capsys.readouterr()
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) <= 1
    return code, (json.loads(lines[0]) if lines else {}), out + err


def test_check_mode_makes_no_calls_or_files(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    def _explode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("no transport in check mode")

    monkeypatch.setattr(startup, "build_loopback_transport", _explode)
    code, payload, _ = _run(
        ["--check", "--ollama-model-tag", TAG, "--repository-root", str(tmp_path), "--output-name", "run-a"],
        capsys,
    )
    assert code == 0
    assert payload == {"status": "CHECKED", "cases": 40, "variants": ["flat", "normalized", "legacy"], "model_calls": 0}
    assert not (tmp_path / ".schema-adaptive").exists()


def test_live_run_with_oracle_transport_writes_three_reports(
    tmp_path: Path, monkeypatch: Any, capsys: Any, oracle_transport: Any
) -> None:
    transport = oracle_transport()
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: transport)
    code, payload, text = _run(
        ["--live-synthetic", "--ollama-model-tag", TAG, "--repository-root", str(tmp_path), "--output-name", "run-b"],
        capsys,
    )
    assert code == 0, payload
    assert transport.calls == 240
    assert payload["status"] == "COMPLETED"
    assert payload["model_label"] == "synthetic-p519-latest"
    assert payload["paired"]["denominator"] == 120
    assert payload["paired"]["both_correct"] == 120
    assert payload["ir_arm"]["semantic_correct"] == {"count": 120, "denominator": 120}
    assert payload["sql_arm"]["execution_correct"] == {"count": 120, "denominator": 120}
    assert "GOLD_NEEDS_HUMAN_REVIEW" in payload["warnings"]
    out_dir = tmp_path / ".schema-adaptive" / "reports" / "run-b"
    assert sorted(p.name for p in out_dir.iterdir()) == ["comparison.json", "consistency.json", "ir-report.json", "sql-report.json"]
    blob = text + "".join(p.read_text(encoding="utf-8") for p in out_dir.iterdir())
    for marker in ("127.0.0.1", "11434", "SELECT ", "개설된 모든 강좌"):
        assert marker not in blob


@pytest.mark.parametrize(
    "argv",
    [
        ["--ollama-model-tag", TAG, "--output-name", "x"],
        ["--check", "--live-synthetic", "--ollama-model-tag", TAG, "--output-name", "x"],
        ["--live-synthetic", "--output-name", "x"],
        ["--live-synthetic", "--ollama-model-tag", TAG],
        ["--live-synthetic", "--ollama-model-tag", TAG, "--output-name", "../escape"],
        ["--live-synthetic", "--ollama-model-tag", TAG, "--output-name", "Upper"],
        ["--live-synthetic", "--ollama-model-tag", TAG, "--output-name", "x", "--ollama-base-url", f"http://{SECRET}:1"],
        ["--live-synthetic", "--ollama-model-tag", f"{SECRET} bad", "--output-name", "x"],
    ],
)
def test_config_errors_are_fixed_and_call_nothing(
    tmp_path: Path, monkeypatch: Any, capsys: Any, argv: list[str]
) -> None:
    def _explode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("must not call")

    monkeypatch.setattr(startup, "build_loopback_transport", _explode)
    code, payload, text = _run([*argv, "--repository-root", str(tmp_path)], capsys)
    assert code == 2
    assert payload == {"status": "CONFIG_ERROR"}
    assert SECRET not in text


def test_existing_output_directory_is_refused(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    (tmp_path / ".schema-adaptive" / "reports" / "taken").mkdir(parents=True)
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _c: (_ for _ in ()).throw(AssertionError()))
    code, payload, _ = _run(
        ["--live-synthetic", "--ollama-model-tag", TAG, "--repository-root", str(tmp_path), "--output-name", "taken"],
        capsys,
    )
    assert code == 2 and payload == {"status": "CONFIG_ERROR"}


@pytest.mark.parametrize(
    "argv",
    [
        ["--check", "--ollama-model-tag", TAG, "--ollama-model-tag", TAG, "--output-name", "x"],
        ["--check", "--ollama-model", TAG, "--output-name", "x"],
        ["--check", f"--unknown-{SECRET}"],
    ],
)
def test_argparse_errors_are_fixed(capsys: Any, argv: list[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(argv)
    assert info.value.code == 2
    out, err = capsys.readouterr()
    assert err.strip() == "arm comparison invalid configuration"
    assert SECRET not in out + err


def test_repeated_mode_flag_is_refused(capsys: Any) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["--check", "--check", "--ollama-model-tag", TAG, "--output-name", "x"])
    assert info.value.code == 2


def test_symlinked_reports_directory_is_refused(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    repo = tmp_path / "repo"
    (repo / ".schema-adaptive").mkdir(parents=True)
    try:
        (repo / ".schema-adaptive" / "reports").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation not permitted")
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _c: (_ for _ in ()).throw(AssertionError()))
    code, payload, _ = _run(
        ["--live-synthetic", "--ollama-model-tag", TAG, "--repository-root", str(repo), "--output-name", "run-s"],
        capsys,
    )
    assert code == 2 and payload == {"status": "CONFIG_ERROR"}
    assert list(outside.iterdir()) == []


def test_publish_failure_leaves_no_partial_output(
    tmp_path: Path, monkeypatch: Any, capsys: Any, oracle_transport: Any
) -> None:
    transport = oracle_transport()
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: transport)
    real_rename = cli.os.rename

    def _fail(*_a: Any, **_k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(cli.os, "rename", _fail)
    code, payload, text = _run(
        ["--live-synthetic", "--ollama-model-tag", TAG, "--repository-root", str(tmp_path), "--output-name", "run-f"],
        capsys,
    )
    monkeypatch.setattr(cli.os, "rename", real_rename)
    assert code == 1 and payload == {"status": "RUN_ERROR"}
    reports = tmp_path / ".schema-adaptive" / "reports"
    assert list(reports.iterdir()) == []
    assert "disk full" not in text


def test_no_system_format_applies_to_both_arms(
    tmp_path: Path, monkeypatch: Any, capsys: Any, oracle_transport: Any
) -> None:
    transport = oracle_transport()
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: transport)
    code, payload, _ = _run(
        ["--live-synthetic", "--ollama-model-tag", TAG, "--ollama-request-format", "alpaca-raw-no-system-v1",
         "--repository-root", str(tmp_path), "--output-name", "run-n"],
        capsys,
    )
    assert code == 0, payload
    assert transport.calls == 240
    assert transport.raw_flags == [True] * 240
    assert payload["request_format"] == "alpaca-raw-no-system-v1"
    assert payload["model_label"] == "synthetic-p519-latest.nosys"
    assert payload["paired"]["both_correct"] == 120
    comparison = json.loads((tmp_path / ".schema-adaptive" / "reports" / "run-n" / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["ir_model_run_id"] == "p520-nosys-ir-synthetic-p519-latest.nosys"
    assert comparison["sql_model_run_id"] == "p520-nosys-sql-synthetic-p519-latest.nosys"


def test_default_format_run_ids_cannot_collide_with_no_system(
    tmp_path: Path, monkeypatch: Any, capsys: Any, oracle_transport: Any
) -> None:
    transport = oracle_transport()
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: transport)
    code, _payload, _ = _run(
        ["--live-synthetic", "--ollama-model-tag", "synthetic-p519-latest.nosys",
         "--repository-root", str(tmp_path), "--output-name", "run-c"],
        capsys,
    )
    assert code == 0
    comparison = json.loads((tmp_path / ".schema-adaptive" / "reports" / "run-c" / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["ir_model_run_id"] == "p519-ir-synthetic-p519-latest.nosys"
    assert comparison["ir_model_run_id"] != "p520-nosys-ir-synthetic-p519-latest.nosys"


def test_default_format_sends_no_raw_flag(
    tmp_path: Path, monkeypatch: Any, capsys: Any, oracle_transport: Any
) -> None:
    transport = oracle_transport()
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: transport)
    code, payload, _ = _run(
        ["--live-synthetic", "--ollama-model-tag", TAG, "--repository-root", str(tmp_path), "--output-name", "run-d"],
        capsys,
    )
    assert code == 0
    assert transport.raw_flags == [None] * 240
    assert payload["request_format"] == "model-template"
    assert payload["model_label"] == "synthetic-p519-latest"


def test_invalid_request_format_is_config_error(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _c: (_ for _ in ()).throw(AssertionError()))
    code, payload, _ = _run(
        ["--live-synthetic", "--ollama-model-tag", TAG, "--ollama-request-format", "raw",
         "--repository-root", str(tmp_path), "--output-name", "run-x"],
        capsys,
    )
    assert code == 2 and payload == {"status": "CONFIG_ERROR"}


def test_no_system_wrapper_exact_bytes_and_no_mutation() -> None:
    seen: list[Any] = []
    wrapped = apply_request_format(lambda u, p, t: seen.append((u, p, t)) or {"response": "x"}, "alpaca-raw-no-system-v1")
    original = {"model": "m", "prompt": "질문 본문", "stream": False, "options": {"temperature": 0}}
    wrapped("http://127.0.0.1:1/api/generate", original, 5.0)
    assert original == {"model": "m", "prompt": "질문 본문", "stream": False, "options": {"temperature": 0}}
    sent = seen[0][1]
    assert sent["raw"] is True
    assert sent["prompt"] == (
        "Below is an instruction that describes a task, paired with an input that provides further context. "
        "Write a response that appropriately completes the request.\r\n\r\n### Instruction:\r\n\r\n\r\n"
        "### Input:\r\n질문 본문\r\n\r\n### Response:\r\n"
    )
    for bad in ({"prompt": 1}, {"prompt": "x", "raw": False}, {"prompt": "x", "system": "s"}, "not-a-dict"):
        with pytest.raises(RequestFormatError):
            wrapped("u", bad, 1.0)  # type: ignore[arg-type]
    with pytest.raises(RequestFormatError):
        apply_request_format(lambda *a: None, "other")


@pytest.mark.parametrize("extra", [["--ollama-request-format", ""], ["--repository-root", ""]])
def test_empty_values_fail_closed(tmp_path: Path, monkeypatch: Any, capsys: Any, extra: list[str]) -> None:
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _c: (_ for _ in ()).throw(AssertionError()))
    argv = ["--live-synthetic", "--ollama-model-tag", TAG, "--output-name", "run-e", *extra]
    if "--repository-root" not in extra:
        argv += ["--repository-root", str(tmp_path)]
    code, payload, _ = _run(argv, capsys)
    assert code == 2 and payload == {"status": "CONFIG_ERROR"}



def _fencing(transport: Any) -> Any:
    """Wrap the oracle transport so IR answers arrive inside a code fence."""

    def call(url: str, payload: dict[str, Any], timeout: float) -> Any:
        envelope = transport(url, payload, timeout)
        if payload["prompt"].startswith("Prompt version:"):
            envelope = {"response": "```json" + chr(10) + envelope["response"] + chr(10) + "```"}
        return envelope

    return call


@pytest.mark.parametrize("rule,ir_correct", [(None, 0), ("single-fence-v1", 120)])
def test_ir_extract_rule_controls_fenced_ir(
    tmp_path: Path, monkeypatch: Any, capsys: Any, oracle_transport: Any, rule: Any, ir_correct: int
) -> None:
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: _fencing(oracle_transport()))
    name = "run-f0" if rule is None else "run-f1"
    argv = ["--live-synthetic", "--ollama-model-tag", TAG, "--repository-root", str(tmp_path), "--output-name", name]
    if rule:
        argv += ["--ir-extract", rule]
    code, payload, _ = _run(argv, capsys)
    assert code == 0, payload
    assert payload["ir_arm"]["semantic_correct"]["count"] == ir_correct
    assert payload["sql_arm"]["execution_correct"]["count"] == 120
    assert payload["ir_extract"] == (rule or "none")
    comparison = json.loads((tmp_path / ".schema-adaptive" / "reports" / name / "comparison.json").read_text(encoding="utf-8"))
    if rule:
        assert payload["model_label"] == "synthetic-p519-latest.fence"
        assert comparison["ir_model_run_id"] == "p522-fence-ir-synthetic-p519-latest.fence"
    else:
        assert payload["model_label"] == "synthetic-p519-latest"
        assert comparison["ir_model_run_id"] == "p519-ir-synthetic-p519-latest"


def test_ir_extract_with_no_system_prefix(tmp_path: Path, monkeypatch: Any, capsys: Any, oracle_transport: Any) -> None:
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _config: oracle_transport())
    code, payload, _ = _run(
        ["--live-synthetic", "--ollama-model-tag", TAG, "--ollama-request-format", "alpaca-raw-no-system-v1",
         "--ir-extract", "single-fence-v1", "--repository-root", str(tmp_path), "--output-name", "run-nf"],
        capsys,
    )
    assert code == 0
    comparison = json.loads((tmp_path / ".schema-adaptive" / "reports" / "run-nf" / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["ir_model_run_id"] == "p522-nosys-fence-ir-synthetic-p519-latest.nosys.fence"


@pytest.mark.parametrize("extra", [["--ir-extract", ""], ["--ir-extract", "strip"], ["--ir-extract", "none", "--ir-extract", "none"]])
def test_ir_extract_invalid_values(tmp_path: Path, monkeypatch: Any, capsys: Any, extra: list[str]) -> None:
    monkeypatch.setattr(startup, "build_loopback_transport", lambda _c: (_ for _ in ()).throw(AssertionError()))
    argv = ["--live-synthetic", "--ollama-model-tag", TAG, "--repository-root", str(tmp_path), "--output-name", "run-i", *extra]
    try:
        code = cli.main(argv)
    except SystemExit as exc:
        code = exc.code
    capsys.readouterr()
    assert code == 2
