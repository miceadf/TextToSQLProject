# Phase 7-08 구현 지침: server-owned Ollama startup 설정과 명시적 provider 선택

> Phase 7-10 이후: Ollama를 켤 때 `--ollama-request-format`(`model-template`|`alpaca-raw-no-system-v1`)이 필수다. 최신 계약은 [`phase7-10-runtime-ollama-request-format.md`](phase7-10-runtime-ollama-request-format.md)를 따른다.

## 목표

loopback prototype CLI(`python -m backend.adaptive.api.synthetic_prototype`)가 제한된 server-owned Ollama 설정을 명시적 option으로만 받아 기존 `OllamaQueryModel`을 조립하고, Phase 7-07 factory 인자 `ollama_model=`로 주입한다. option이 없으면 기존 결정론 `_FixedConstraintProvider`, environment·Ollama 접근 0회, network 0회를 그대로 유지한다. startup은 설정 검증과 adapter 조립만 하고 모델을 호출하지 않는다. 실제 호출은 clarification `submit`의 확인된 option 실행 시점에만 일어난다.

이번 단계는 synthetic transport로 계약만 완결한다. live Ollama 호출, availability 확인·warm-up, model 다운로드·재학습, 성능 측정은 하지 않는다.

## 구현 위치

1. 신규 `backend/adaptive/api/ollama_startup.py`
   - 설정 계약, strict parser, loopback 전용 transport, model builder.
2. `backend/adaptive/api/synthetic_prototype.py`
   - CLI option 추가, `main()` 연결, app state의 provider 종류 기록.
3. 신규 테스트 `tests/adaptive/test_ollama_startup.py`.

`OllamaQueryModel`(`backend/adaptive/harness/ollama.py`), `OllamaConstraintProvider`(`backend/adaptive/harness/ollama_constraint.py`), compiler/policy/executor/router, PostgreSQL startup loader, frontend, legacy는 변경하지 않는다. `ollama.py`의 private JSON helper(`_reject_duplicate_keys`, `_reject_non_finite`, `_finite_float`)는 import해 재사용할 수 있으나 해당 파일을 수정하지 않는다.

## 설정 계약 (`ollama-startup-config-v1`)

CLI option 세 개. 모두 선택이며 `--ollama-model-tag`가 있어야만 Ollama가 활성화된다.

| option | 계약 | 기본값 |
| --- | --- | --- |
| `--ollama-model-tag` | `recording_bundle_contracts.SAFE_LABEL_PATTERN`을 `re.fullmatch`로 만족하는 ASCII 문자열. 공백·개행·빈 값 거부 | 없음(활성화 스위치) |
| `--ollama-base-url` | 정확히 `http://127.0.0.1:<port>` 또는 `http://[::1]:<port>`. `<port>`는 선행 0 없는 10진수 1~65535. trailing slash·path·query·fragment·userinfo·`localhost`·기타 host 이름·`https`·대문자 scheme 거부 | `http://127.0.0.1:11434` |
| `--ollama-timeout` | `^[1-9][0-9]{0,2}(\.[0-9]{1,3})?$` 형식의 10진 문자열이며 1 이상 120 이하. 부호·지수·`nan`·`inf`·공백 거부 | `30` |

- prompt version은 `query-ir-v1`로 고정한다(ADR-026). option을 추가하지 않는다.
- `--ollama-base-url` 또는 `--ollama-timeout`이 `--ollama-model-tag` 없이 주어지면 설정 오류다.
- 세 option 모두 한 번만 허용한다. 같은 option 반복(`--x a --x b`, `--x=a --x=b` 포함)은 마지막 값 채택이 아니라 설정 오류다.
- parser는 `allow_abbrev=False`로 option 축약(`--ollama-model` 등)을 거부한다. 기존 `--port`, `--repository-root`, `--postgres-profile-id` 동작은 축약 금지 외에는 바꾸지 않는다.
- Ollama 설정은 환경변수·`.env`·설정 파일을 읽지 않는다. CLI 인자만 사용한다.
- 모든 argparse 오류(unknown option, 반복, 형식 오류)는 고정 문구 `synthetic prototype invalid configuration`만 stderr에 출력하고 exit code 2로 끝난다. argparse 기본 메시지처럼 입력값이나 unknown 인자를 반사하지 않는다. 이를 위해 `ArgumentParser.error`를 재정의한 subclass를 쓴다. `--help`는 그대로 둔다.

설정 객체:

- `OllamaStartupConfig`: frozen dataclass(또는 동등 immutable). field는 `base_url: str`, `model_tag: str`, `timeout_seconds: float`.
- `repr`/`str`은 고정 문자열 `OllamaStartupConfig(version='ollama-startup-config-v1')`만 반환한다. URL·port·tag·timeout을 포함하지 않는다.
- `parse_ollama_startup_config(model_tag, base_url, timeout) -> OllamaStartupConfig`: 원문 문자열만 받는다. `bool`, 숫자, `None`(기본값 적용 전의 model tag), bytes 등 문자열이 아닌 값은 coercion 없이 거부한다.
- 실패는 전용 `OllamaStartupConfigError`(고정 code `OLLAMA_STARTUP_CONFIG_INVALID`, 고정 message)로만 알린다. `raise ... from None`으로 원인 예외를 끊고 입력값을 속성·args·message에 보존하지 않는다.

## loopback 전용 transport

기존 `OllamaQueryModel`의 기본 transport는 `urllib.request.urlopen`이라 시스템/환경 proxy(`HTTP_PROXY` 등, Windows registry proxy)를 따르고 POST에 대한 301/302/303 redirect를 GET으로 따라간다. startup 경로는 이 기본 transport를 쓰지 않고 다음 transport를 `transport=`로 주입한다.

- `build_loopback_transport(config)`가 반환하는 callable은 `(url, payload, timeout)` 계약을 지킨다.
- 호출 시 `url`이 `config.base_url + "/api/generate"`와 정확히 같지 않으면 요청 없이 고정 오류를 낸다.
- opener는 `urllib.request.build_opener(ProxyHandler({}), <redirect 거부 handler>)`로 만든다. redirect 거부 handler는 `HTTPRedirectHandler` subclass로 모든 redirect를 따르지 않는다(`redirect_request`가 `None` 반환 또는 고정 오류). 결과적으로 proxy 환경변수를 읽지 않고 외부 host로 요청을 보내지 않는다.
- 요청 body는 기존 `_default_transport`와 같은 `json.dumps(payload, ensure_ascii=False, allow_nan=False)` UTF-8, `Content-Type: application/json`, `POST`이다.
- 응답은 최대 1 MiB까지만 읽는다(`read(limit + 1)`로 초과 감지 시 고정 오류). 2xx 외 status는 고정 오류다.
- 응답 JSON은 기존 helper로 duplicate key·non-finite를 거부해 parse한다.
- timeout은 기존처럼 `opener.open`의 `timeout`에 전달한다. timeout 계열(`TimeoutError`, `socket.timeout`, reason이 timeout인 `URLError`)은 원문 없는 새 `TimeoutError()`로 올려 기존 `OllamaQueryModel`이 `TIMEOUT`으로 분류하게 한다. 그 외 연결·HTTP·parse 오류는 원문 없는 고정 `OllamaLoopbackTransportError`로 바꿔 `PROVIDER_ERROR`로 분류되게 한다. 원인 예외는 연결하지 않는다.
- transport는 로그를 남기지 않고, repr에 URL·tag를 포함하지 않는다.
- 이 transport는 새 Ollama adapter가 아니다. prompt 생성·envelope 검사·QueryIR JSON parse는 모두 기존 `OllamaQueryModel`이 맡는다.

## model 조립

- `build_startup_ollama_model(config, *, transport=None) -> OllamaQueryModel`
  - `config`는 정확히 `OllamaStartupConfig` 인스턴스만 허용한다.
  - `transport`가 `None`이면 `build_loopback_transport(config)`를 사용하고, 테스트는 synthetic transport를 주입한다.
  - `OllamaQueryModel(config.base_url, config.model_tag, config.timeout_seconds, transport=..., prompt_version="query-ir-v1")`만 호출한다. 생성자 예외는 `OllamaStartupConfigError`로 정규화한다.
  - 이 함수와 transport 생성은 network·파일·환경변수 접근 0회다.

## CLI·factory 연결

- `main()` 순서:
  1. argparse strict parse(pure).
  2. Ollama option이 있으면 `parse_ollama_startup_config` → `build_startup_ollama_model`. 실패 시 고정 문구 stderr, exit 2, PostgreSQL loader·factory·uvicorn 호출 0회.
  3. 기존 PostgreSQL profile loading(Phase 7-05) 그대로.
  4. factory 호출. Ollama model이 있을 때만 `ollama_model=model` keyword를 추가한다. option이 전혀 없으면 factory를 **인자 없이** 호출해야 한다(기존 테스트 `seen["kwargs"] == {}` 유지). PostgreSQL만 있을 때는 기존과 같이 `postgres_profiles=`만 전달한다.
  5. ready 문구는 기존 고정 문자열을 유지하며 URL·tag·timeout을 출력하지 않는다.
- Ollama model 테스트 주입을 위해 `main`에 공개 인자를 추가하지 않는다. 테스트는 `ollama_startup` 모듈 함수(예: `build_loopback_transport`)를 monkeypatch해 synthetic transport를 넣는다.
- factory(`create_synthetic_clarification_prototype`)는 Phase 7-07 검증 로직을 그대로 쓴다. 추가 변경은 `app.state.synthetic_prototype_provider_kind`에 `"deterministic"` 또는 `"ollama"` 고정 문자열을 기록하는 것뿐이다. model·config·URL·tag를 app state에 새로 두지 않는다.
- module-level `app = create_synthetic_clarification_prototype()`은 그대로다. import 시 Ollama·환경변수 접근이 없어야 한다.
- 같은 provider 하나를 SQLite(flat·legacy·private)와 PostgreSQL context가 공유하고 database ID router가 compiler/policy/executor를 고른다. provider가 router를 우회하지 않는다.

## 호출 시점 계약

- import, parser 생성, config parse, model 조립, factory, app startup: transport 호출 0회.
- clarification `start`(pending 생성, no-match 포함): 0회.
- 정확한 option `submit`: 정확히 1회. 이후 기존 Phase 7-07 constraint 축소 → Phase 6-09 QueryIR·constraint 검증 → compiler → AST policy → read-only executor.
- cancel, invalid/expired/reused token, invalid selection, context drift: 0회.
- transport timeout/error/empty/non-JSON/invalid QueryIR/constraint 위반은 기존 고정 HTTP 오류 taxonomy로 redacted 변환한다.

## 비노출 계약

stdout/stderr, 예외 message·args·repr, HTTP 응답, app state의 새 항목, 로그에 다음이 나타나지 않는다: base URL·host·port, model tag, timeout 원문, 잘못된 CLI 입력값, transport/urllib 예외 원문, prompt, question, PostgreSQL DSN, physical schema/table/column, SQL.

## 테스트 (`tests/adaptive/test_ollama_startup.py`)

모든 모델 응답은 synthetic transport다. 외부 network, 실제 Ollama, 실제 PostgreSQL, CNU 자료를 쓰지 않는다. redirect/proxy 검증에 한해 테스트 안에서 `127.0.0.1` 임시 `http.server`를 띄우는 것은 허용한다(외부 host로의 실제 연결은 금지이며, redirect 대상 외부 host는 연결되지 않아야 함을 증명하는 용도다).

1. parser·config
   - 정상: tag만(기본 URL·timeout), `[::1]` URL, 경계 timeout `1`·`120`·`0.5` 거부·`120.001` 거부.
   - 거부: 빈/공백/개행 tag, 비 ASCII tag, `localhost`, `0.0.0.0`, 외부 host, `https`, trailing slash, path/query/fragment/userinfo, port 0·65536·선행 0·누락, timeout `0`·`-1`·`1e2`·`nan`·`inf`·` 30`, 문자열 아닌 값.
   - 반복 option, 축약 option, tag 없는 URL/timeout, unknown option은 exit 2와 고정 문구만 출력하고 입력값 비반사.
2. 조립
   - config repr/str 고정, error message·args·`__cause__`/`__context__` 비노출.
   - builder가 `OllamaQueryModel`을 반환하고 prompt version `query-ir-v1`, transport 호출 0회.
3. transport
   - URL 불일치 시 요청 없이 고정 오류.
   - `HTTP_PROXY`/`http_proxy`/`ALL_PROXY` 환경변수가 있어도 proxy를 쓰지 않음.
   - loopback 임시 server의 302 응답을 따라가지 않고 고정 오류.
   - 1 MiB 초과 응답, 비 2xx, duplicate key, non-finite, 비 JSON envelope 거부. 오류 원문 비노출.
4. CLI
   - option 없음: factory kwargs `{}`, 환경변수·transport 접근 0회, uvicorn loopback host.
   - Ollama option: factory에 `ollama_model` 전달, startup 동안 transport 0회, PostgreSQL option과 함께 쓰면 두 kwargs 모두 전달.
   - 설정 오류: PostgreSQL loader·factory·uvicorn 호출 0회.
   - stdout/stderr에 URL·tag·timeout 비노출.
5. 실제 factory 통합(synthetic transport 주입)
   - `provider_kind` 기본 `"deterministic"`, 주입 시 `"ollama"`.
   - startup·start 0회, submit 1회, cancel·재사용 token 0회.
   - prompt의 semantic context가 확인된 field/entity로 제한됨(Phase 7-07 계약 재확인).
   - SQLite와 주입 PostgreSQL context(기존 7-07 테스트의 fake adapter/executor 방식 재사용)가 같은 provider를 쓰되 database ID별 router 결과를 유지.
   - transport 실패가 기존 고정 HTTP 오류로 변환되고 응답에 URL·tag·예외 원문 없음.

회귀로 함께 실행할 관련 테스트:

- `tests/adaptive/test_ollama_constraint_provider.py`
- `tests/adaptive/test_ollama_query_model.py`
- `tests/adaptive/test_synthetic_clarification_prototype.py`
- `tests/adaptive/test_private_postgres_startup.py`

## 금지

- live Ollama 호출, model tag 조회, availability 확인·warm-up, 다운로드·재학습, 성능 측정.
- 환경변수·설정 파일 기반 Ollama 설정, HTTP/browser에서 model URL/tag/timeout 지정.
- raw SQL prompt/output fallback, dialect별 prompt, 모델 출력 보정.
- `OllamaQueryModel`·`OllamaConstraintProvider`·Phase 7-07 계약 완화 또는 수정.
- frontend, PostgreSQL profile/startup loader, SQLite registration/review/publish, legacy 변경.
- 실제 CNU/학교 DB, private artifact 사용.
- 계획/ADR/handoff/사람 체크포인트 문서 수정, 전체 pytest, compileall, commit, push. 이는 메인(Claude Code)이 담당한다.
