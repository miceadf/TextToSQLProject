"""Teammate environment check: what ``npm run dev:all`` and the evaluation tools need (Phase 9-03).

Usage::

    python -m backend.adaptive.env_check

Read-only. PostgreSQL sessions are read-only with a 5-second connect
timeout and run only catalog reads; Ollama is asked for its model list on a
loopback URL without proxies; a temporary file in ``.schema-adaptive/`` is
created and removed. DSNs, passwords and ``.env`` values are never printed:
a failed connection shows only the error type, because driver messages can
carry the host or user.

Every line is ``[OK]``, ``[WARN]`` or ``[FAIL]`` with a fix; any ``FAIL``
makes the exit code 1. Passing this check is not the L7 reproducibility
gate, which a person runs on a fresh machine.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib import request

# Standard library only at import time: the check must run while the
# packages it reports on are missing. These copies are pinned to their
# originals by tests (dev_launcher.read_env_file, ollama_startup's base URL
# rule, ai_proposal.DEFAULT_MAPPING_MODEL, chat_models.DEFAULT_CHAT_MODEL).
ENV_KEYS = frozenset(
    {
        "DATABASE_URL", "OLLAMA_BASE_URL", "OLLAMA_MODEL", "CHAT_MODEL", "MAPPING_MODEL", "SHOW_SQL",
        "OPENAI_API_KEY", "OPENAI_MODEL",
    }
)
OLLAMA_BASE_URL_PATTERN = re.compile(r"http://(?:127\.0\.0\.1|\[::1\]):([1-9][0-9]{0,4})")
DEFAULT_MAPPING_MODEL = "qwen2.5-coder:7b-instruct"
DEFAULT_CHAT_MODEL = "qwen2.5-coder:7b-instruct"

OK, WARN, FAIL = "OK", "WARN", "FAIL"
SYNTHETIC_DSN_ENV = "SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
CONNECT_TIMEOUT = 5
HTTP_TIMEOUT = 5.0
PORTS = ((8000, "레거시 백엔드"), (8123, "adaptive 서버"), (5173, "화면(vite)"))
MAX_SCHEMAS_SHOWN = 20
# Installed-or-not only (find_spec): nothing is imported.
ADAPTIVE_MODULES = ("fastapi", "uvicorn", "pydantic", "dotenv", "yaml", "psycopg2", "rapidfuzz", "sqlglot")
LEGACY_MODULES = ("transformers", "torch", "numpy", "safetensors", "redis")  # torch is not in requirements.txt
_SYSTEM_SCHEMA = re.compile(r"^(pg_.*|information_schema)$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.:/ -]{1,128}$")

__all__ = ["Check", "main", "run_checks"]


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    message: str
    fix: str = ""

    def line(self) -> str:
        text = f"[{self.status}] {self.name}: {self.message}"
        return f"{text}\n       → {self.fix}" if self.fix and self.status != OK else text


def _shown(name: str) -> str:
    """A catalog or model name fit to print; anything odd is masked."""
    return name if _SAFE_NAME.fullmatch(name) else "(표시하지 않는 이름)"


def read_env_file(path: Path) -> dict[str, str]:
    """The keys ``dev_launcher`` reads, the same way; other keys are ignored."""
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
        if key in ENV_KEYS or key.startswith("PG_SOURCE_"):
            values[key] = value.strip().strip('"').strip("'")
    return values


def _valid_ollama_url(value: str) -> bool:
    match = OLLAMA_BASE_URL_PATTERN.fullmatch(value)
    return match is not None and 1 <= int(match.group(1)) <= 65535


def _synthetic_dsn_problem(dsn: str) -> str | None:
    """The synthetic DSN rule's failure code; ``None`` when it passes."""
    from .harness.postgres_live_smoke import SmokeFailure, validate_dsn

    try:
        validate_dsn(dsn)
    except SmokeFailure as exc:
        return exc.code
    return None


# --- default dependencies --------------------------------------------------------------------


def _connect(dsn: str) -> Any:
    import psycopg2  # type: ignore[import-not-found]

    connection = psycopg2.connect(dsn, connect_timeout=CONNECT_TIMEOUT)
    try:
        connection.set_session(readonly=True, autocommit=True)
    except BaseException:
        _close(connection)
        raise
    return connection


class _NoRedirect(request.HTTPRedirectHandler):
    """A redirect is an error: the next request could leave loopback."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _http_get_json(url: str) -> Any:
    opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
    with opener.open(url, timeout=HTTP_TIMEOUT) as response:
        if response.status != 200 or response.geturl() != url:
            raise OSError("unexpected response")
        return json.loads(response.read(1_000_000).decode("utf-8"))


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _run(command: list[str]) -> str:
    executable = shutil.which(command[0])
    if executable is None:
        raise FileNotFoundError(command[0])
    done = subprocess.run([executable, *command[1:]], capture_output=True, text=True, timeout=20, check=True)
    return done.stdout.strip()


# --- the checks ------------------------------------------------------------------------------


def _python(root: Path) -> list[Check]:
    version = sys.version_info
    checks = [
        Check(OK, "Python", f"{version.major}.{version.minor}.{version.micro} (개발 PC: 3.13)")
        if version >= (3, 10)
        else Check(FAIL, "Python", f"{version.major}.{version.minor}", "Python 3.10 이상(개발 PC 3.13)을 설치한다")
    ]
    venv = _venv_python(root)
    if not venv.exists():
        checks.append(Check(FAIL, ".venv", "저장소의 .venv가 없다(npm run dev:all이 .venv의 Python을 쓴다)",
                            "python -m venv .venv 후 .venv의 pip로 pip install -r backend/requirements.txt pytest"))
    elif os.name != "nt":
        checks.append(Check(WARN, ".venv", "있지만 npm run dev:all은 Windows 경로(.venv\\Scripts\\python.exe)를 쓴다",
                            "이 PC에서는 package.json의 dev 스크립트 명령을 .venv/bin/python으로 바꿔 직접 실행한다"))
    else:
        checks.append(Check(OK, ".venv", "저장소의 가상환경이 있다"))
    return checks


def _venv_python(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


_MISSING_SCRIPT = (
    "import importlib.util, json, sys; "
    "print(json.dumps([n for n in sys.argv[1:] if importlib.util.find_spec(n) is None]))"
)


def _venv_finder(venv: Path, run: Callable[[list[str]], str]) -> Callable[[str], Any] | None:
    """``find_spec`` as the launcher's ``.venv`` Python sees it; ``None`` if it cannot answer."""
    try:
        missing = json.loads(run([str(venv), "-c", _MISSING_SCRIPT, *ADAPTIVE_MODULES, *LEGACY_MODULES]))
    except Exception:
        return None
    if not isinstance(missing, list):
        return None
    gone = {name for name in missing if isinstance(name, str)}
    return lambda name: None if name in gone else True


def _modules(find: Callable[[str], Any], where: str) -> list[Check]:
    missing = [name for name in ADAPTIVE_MODULES if find(name) is None]
    legacy = [name for name in LEGACY_MODULES if find(name) is None]
    checks = [
        Check(OK, "Python 패키지", f"adaptive 서버에 필요한 패키지가 있다({where})")
        if not missing
        else Check(FAIL, "Python 패키지", f"없음({where}): " + ", ".join(missing),
                   ".venv의 pip로 pip install -r backend/requirements.txt")
    ]
    if legacy:
        checks.append(Check(WARN, "레거시 백엔드 패키지", f"없음({where}): " + ", ".join(legacy),
                            "레거시 서버(8000)를 켜려면 pip install -r backend/requirements.txt, torch는 따로 설치한다"))
    return checks


def _node(root: Path, run: Callable[[list[str]], str]) -> list[Check]:
    checks: list[Check] = []
    for tool in ("node", "npm"):
        try:
            version = run([tool, "--version"])
            checks.append(Check(OK, tool, _shown(version)))
        except Exception:
            checks.append(Check(FAIL, tool, "실행할 수 없다", "Node.js(개발 PC: 24)를 설치한다"))
    bins = root / "node_modules" / ".bin"
    missing = [
        name
        for name in ("vite", "concurrently")
        if not any((bins / (name + suffix)).is_file() for suffix in ("", ".cmd", ".ps1"))
    ]
    checks.append(
        Check(OK, "node_modules", "vite, concurrently가 있다")
        if not missing
        else Check(FAIL, "node_modules", "없음: " + ", ".join(missing), "저장소 루트에서 npm install")
    )
    return checks


def _env(values: dict[str, str], exists: bool) -> list[Check]:
    if not exists:
        return [Check(FAIL, "backend/.env", "파일이 없다",
                      "backend/.env를 만들고 DATABASE_URL, OLLAMA_BASE_URL, OLLAMA_MODEL을 넣는다(값은 팀에서 받는다)")]
    checks = [
        Check(OK, "DATABASE_URL", "있음(값은 표시하지 않음)")
        if values.get("DATABASE_URL")
        else Check(FAIL, "DATABASE_URL", "없다", "backend/.env에 레거시 PostgreSQL 연결을 넣는다"),
        Check(OK, "OLLAMA_MODEL", _shown(values["OLLAMA_MODEL"]))
        if values.get("OLLAMA_MODEL")
        else Check(WARN, "OLLAMA_MODEL", "없다(레거시 챗봇과 비교용 레거시 학습 모델이 꺼진다)",
                   "backend/.env에 레거시 챗봇 모델 이름을 넣는다"),
        # Phase 9-09: optional; the key itself is never shown.
        Check(OK, "OPENAI_API_KEY", "있음(값은 표시하지 않음). 챗봇에 ChatGPT 선택지가 생긴다")
        if values.get("OPENAI_API_KEY")
        else Check(OK, "OPENAI_API_KEY", "없음(선택). ChatGPT 선택지는 꺼진다"),
    ]
    sources = sorted(key[len("PG_SOURCE_"):].lower().replace("_", "-") for key in values if key.startswith("PG_SOURCE_"))
    checks.append(
        Check(OK, "PG_SOURCE_*", "추가 연결: " + ", ".join(_shown(name) for name in sources))
        if sources
        else Check(WARN, "PG_SOURCE_*", "추가 연결이 없다(평가용 DB를 등록하려면 필요)",
                   "backend/.env에 PG_SOURCE_<이름>=<합성 DB 연결>을 넣는다")
    )
    if values.get("SHOW_SQL") == "0":
        checks.append(Check(OK, "SHOW_SQL", "0(챗봇 SQL 표시 끔)"))
    return checks


def _schemas(connection: Any) -> list[str]:
    cursor = connection.cursor()
    cursor.execute("SELECT schema_name FROM information_schema.schemata ORDER BY schema_name")
    return [row[0] for row in cursor.fetchall() if isinstance(row[0], str) and not _SYSTEM_SCHEMA.match(row[0])]


def _close(connection: Any) -> None:
    try:
        connection.close()
    except Exception:
        pass


def _postgres(name: str, dsn: str, connect: Callable[[str], Any], *, public_count: bool) -> Check:
    try:
        connection = connect(dsn)
    except ModuleNotFoundError:
        return Check(WARN, name, "지금 Python에 psycopg2가 없어 접속을 확인하지 못했다",
                     ".venv의 Python으로 다시 실행한다: .venv\\Scripts\\python.exe -m backend.adaptive.env_check")
    except Exception as exc:
        return Check(FAIL, name, f"접속 실패({type(exc).__name__})",
                     "PostgreSQL이 켜져 있는지, 연결 정보(호스트·포트·DB·사용자)가 맞는지 확인한다")
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchall()
        if public_count:
            cursor.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
            )
            count = int(cursor.fetchall()[0][0])
            return Check(OK, name, f"접속됨(읽기 전용), public schema table·view {count}개")
        schemas = _schemas(connection)
        shown = ", ".join(_shown(item) for item in schemas[:MAX_SCHEMAS_SHOWN])
        more = f" 외 {len(schemas) - MAX_SCHEMAS_SHOWN}개" if len(schemas) > MAX_SCHEMAS_SHOWN else ""
        return Check(OK, name, f"접속됨(읽기 전용), schema: {shown or '(없음)'}{more}")
    except Exception as exc:
        return Check(FAIL, name, f"조회 실패({type(exc).__name__})", "읽기 권한이 있는 사용자인지 확인한다")
    finally:
        _close(connection)


def _databases(values: dict[str, str], environ: dict[str, str], connect: Callable[[str], Any]) -> list[Check]:
    checks: list[Check] = []
    if values.get("DATABASE_URL"):
        checks.append(_postgres("레거시 DB(DATABASE_URL)", values["DATABASE_URL"], connect, public_count=True))
    for key in sorted(item for item in values if item.startswith("PG_SOURCE_")):
        source = key[len("PG_SOURCE_"):].lower().replace("_", "-")
        checks.append(_postgres(f"연결 {_shown(source)}", values[key], connect, public_count=False))
    dsn = environ.get(SYNTHETIC_DSN_ENV, "")
    if not dsn:
        checks.append(Check(WARN, SYNTHETIC_DSN_ENV, "없다(합성 학교 schema 만들기·mapping 점검에만 필요)",
                            "필요할 때 loopback의 schema_adaptive_synthetic DB 연결을 이 환경변수에 넣는다"))
        return checks
    try:
        problem = _synthetic_dsn_problem(dsn)
    except ImportError:
        checks.append(Check(WARN, SYNTHETIC_DSN_ENV, "규칙을 검사하지 못했다(Python 패키지 없음)",
                            "Python 패키지를 설치한 뒤 다시 실행한다"))
        return checks
    if problem is not None:
        checks.append(Check(FAIL, SYNTHETIC_DSN_ENV, f"규칙 위반({problem})",
                            "loopback 호스트와 schema_adaptive_synthetic DB만 쓸 수 있다"))
        return checks
    checks.append(_postgres(f"합성 DB({SYNTHETIC_DSN_ENV})", dsn, connect, public_count=False))
    return checks


def _model_names(tags: Any) -> set[str]:
    models = tags.get("models") if isinstance(tags, dict) else None
    names: set[str] = set()
    for item in models if isinstance(models, list) else []:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str):
            names.add(name)
            if name.endswith(":latest"):
                names.add(name[: -len(":latest")])
    return names


def _ollama(values: dict[str, str], http_get_json: Callable[[str], Any]) -> list[Check]:
    base = values.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_URL
    if not _valid_ollama_url(base):
        return [Check(FAIL, "Ollama", "OLLAMA_BASE_URL이 규칙에 맞지 않다(loopback의 http://127.0.0.1:포트만 허용)",
                      "OLLAMA_BASE_URL=http://127.0.0.1:11434 처럼 넣는다")]
    try:
        names = _model_names(http_get_json(base.rstrip("/") + "/api/tags"))
    except Exception as exc:
        return [Check(FAIL, "Ollama", f"응답 없음({type(exc).__name__})", "Ollama를 켠다(ollama serve 또는 앱 실행)")]
    checks = [Check(OK, "Ollama", f"응답함, 모델 {len(names)}개")]
    # Phase 9-08: the chat's default is the general model; OLLAMA_MODEL is the
    # legacy chatbot's fine-tuned model, a comparison choice in the new chat.
    wanted = [
        ("챗봇 일반 모델", values.get("CHAT_MODEL") or DEFAULT_CHAT_MODEL),
        ("레거시 학습 모델", values.get("OLLAMA_MODEL")),
        ("mapping 모델", values.get("MAPPING_MODEL") or DEFAULT_MAPPING_MODEL),
    ]
    for label, tag in wanted:
        if not tag:
            continue
        checks.append(
            Check(OK, label, _shown(tag))
            if tag in names
            else Check(FAIL, label, f"{_shown(tag)}이(가) Ollama에 없다",
                       "ollama pull로 받거나, 팀에서 받은 모델 파일을 ollama create로 등록한다")
        )
    return checks


def _ports(in_use: Callable[[int], bool]) -> list[Check]:
    checks = []
    for port, label in PORTS:
        try:
            busy = in_use(port)
        except Exception:
            busy = False
        checks.append(
            Check(WARN, f"포트 {port}", f"사용 중({label})", "이미 켜 둔 dev:all이면 괜찮다. 아니면 그 프로그램을 끈다")
            if busy
            else Check(OK, f"포트 {port}", f"비어 있음({label})")
        )
    return checks


def _workspace(root: Path) -> Check:
    folder = root / ".schema-adaptive"
    try:
        folder.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, prefix="env-check-", delete=True):
            pass
        return Check(OK, ".schema-adaptive", "쓸 수 있다(등록·평가 결과가 저장되는 곳, git 밖)")
    except OSError as exc:
        return Check(FAIL, ".schema-adaptive", f"쓸 수 없다({type(exc).__name__})", "저장소 폴더의 쓰기 권한을 확인한다")


def run_checks(
    root: Path,
    *,
    environ: dict[str, str] | None = None,
    connect: Callable[[str], Any] = _connect,
    http_get_json: Callable[[str], Any] = _http_get_json,
    port_in_use: Callable[[int], bool] = _port_in_use,
    run: Callable[[list[str]], str] = _run,
    find_module: Callable[[str], Any] | None = None,
) -> list[Check]:
    """``find_module`` defaults to the ``.venv`` Python's view (the launcher's), else this one's."""
    env_path = root / "backend" / ".env"
    values = read_env_file(env_path)
    where = "지금 Python 기준"
    if find_module is None:
        venv = _venv_python(root)
        here = Path(sys.executable).resolve()
        if venv.exists() and venv.resolve() != here:
            find_module = _venv_finder(venv, run)
            where = ".venv 기준"
        if find_module is None:
            find_module, where = importlib.util.find_spec, "지금 Python 기준"
    return [
        *_python(root),
        *_modules(find_module, where),
        *_node(root, run),
        *_env(values, env_path.exists()),
        *_databases(values, dict(os.environ if environ is None else environ), connect),
        *_ollama(values, http_get_json),
        *_ports(port_in_use),
        _workspace(root),
    ]


def report(checks: Iterable[Check]) -> tuple[str, int]:
    items = list(checks)
    failed = sum(item.status == FAIL for item in items)
    warned = sum(item.status == WARN for item in items)
    lines = [item.line() for item in items]
    lines.append(f"\n결과: FAIL {failed}개, WARN {warned}개" + (" — FAIL부터 고친다" if failed else " — 시작할 수 있다"))
    return "\n".join(lines), 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    if argv:
        print("usage: python -m backend.adaptive.env_check")
        return 2
    root = Path(__file__).resolve().parents[2]
    text, code = report(run_checks(root))
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
