# Phase 7-09 구현 지침: 합성 prototype Ollama end-to-end live smoke

> Phase 7-10 이후: Ollama를 켤 때 `--ollama-request-format`(`model-template`|`alpaca-raw-no-system-v1`)이 필수다. 최신 계약은 [`phase7-10-runtime-ollama-request-format.md`](phase7-10-runtime-ollama-request-format.md)를 따른다.

## 목표

Phase 7-08 server-owned Ollama 설정으로 조립한 합성 clarification prototype을 로컬 Ollama에 실제로 연결해, 완전 합성 질문으로 `start → option 선택 → submit → QueryIR 검증 → compile → policy → read-only 실행` 전체 경계가 live 환경에서 유지되는지 확인한다. 이 단계는 **형식·경계 smoke**이며 모델 정확도·성능 평가가 아니다.

## 범위

1. 신규 `backend/adaptive/api/ollama_prototype_smoke.py`: opt-in CLI와 순수 실행 함수.
2. 신규 `tests/adaptive/test_ollama_prototype_smoke.py`: synthetic transport 테스트.
3. 구현·검토 뒤 메인이 로컬 Ollama로 1회 실행하고 결과를 `docs/phase7-09-ollama-prototype-live-smoke-results-2026-09-28.md`에 기록한다.

Phase 7-07/7-08 모듈(`ollama_startup.py`, `ollama_constraint.py`, `ollama.py`), prototype factory·CLI, compiler/policy/executor, frontend, legacy, PostgreSQL 코드는 변경하지 않는다.

## CLI 계약

```text
python -m backend.adaptive.api.ollama_prototype_smoke (--check | --live-synthetic)
    --ollama-model-tag TAG [--ollama-base-url URL] [--ollama-timeout SECONDS]
```

- `--check`와 `--live-synthetic` 중 정확히 하나가 필수다. 둘 다 없거나 둘 다 있으면 설정 오류다.
- Ollama option 세 개는 Phase 7-08의 `parse_ollama_startup_config`와 같은 계약(loopback URL, strict tag, 1~120초)을 재사용한다. 반복·축약·unknown option은 Phase 7-08 prototype CLI와 같이 고정 문구로 거부하고 입력을 반사하지 않는다.
- 설정은 CLI 인자만 쓴다. 환경변수·설정 파일·private artifact를 읽지 않는다.
- model은 `build_startup_ollama_model(config, transport=<counting wrapper>)`로 조립한다. wrapper는 Phase 7-08 `build_loopback_transport(config)`가 만든 transport에 그대로 위임하고 호출 횟수만 센다. payload·응답을 보관하지 않는다.
- app은 `create_synthetic_clarification_prototype(ollama_model=model)`만 사용한다. PostgreSQL profile은 주입하지 않는다(PostgreSQL routing은 7-04 live·7-07/7-08 합성 테스트가 담당).
- HTTP는 `fastapi.testclient.TestClient`로 in-process 호출한다. port를 열지 않는다. 종료 시 `app.state.synthetic_prototype_cleanup`을 반드시 호출한다.

## 고정 합성 case

질문은 모두 코드에 고정된 완전 합성 문자열이다. 실제 과목명·교수명·CNU 자료를 쓰지 않는다. ambiguity rule trigger는 prototype의 `강의`다.

| case ID | DB | 동작 | 기대 model 호출 |
| --- | --- | --- | ---: |
| `p709-flat-course` | flat | `Course.name` option submit | 1 |
| `p709-flat-instructor` | flat | `Instructor.name` option submit | 1 |
| `p709-legacy-course` | legacy | `Course.name` option submit | 1 |
| `p709-legacy-instructor` | legacy | `Instructor.name` option submit | 1 |
| `p709-flat-cancel` | flat | pending 뒤 cancel | 0 |
| `p709-flat-no-match` | flat | trigger 없는 질문, `NOT_REQUIRED` | 0 |

- 각 case는 고유 request ID를 쓰고 case마다 새 session을 만든다. retry·재제출·model 대체는 없다.
- start 단계 호출은 항상 0이어야 한다.

## case별 공개 관찰 항목

case마다 다음만 기록한다.

- `case_id`, `database`(`flat`|`legacy`), `action`, `selected_ref`(submit case만)
- `start_status`(`PENDING`|`NOT_REQUIRED`), `http_status`
- `outcome`: 성공 시 `EXECUTED_SUCCEEDED`, 실행 실패 시 기존 고정 execution code, HTTP 오류 시 기존 고정 error code, cancel은 `CANCELLED`, no-match는 `NOT_REQUIRED`
- `columns_match_constraint`(성공 case만): 결과 columns가 `[selected_ref]`와 정확히 같은지
- `row_count`(성공 case만), `model_calls`(case 동안 증가분), `elapsed_ms`(정수)
- `boundary_ok`: 호출 예산 일치, 응답이 기존 strict shape·고정 code taxonomy 안에 있음, 성공 columns가 비어 있지 않고 canonical semantic field(`Course.name`·`Instructor.name`)만 포함, 응답 본문에 model tag·base URL·질문 원문·`SELECT`·합성 mapping의 physical table/column 이름이 없음
- `columns_match_constraint`는 **관찰값**이며 `boundary_ok`에 넣지 않는다. Phase 6-09 계약은 선택 field가 `select` 또는 `filter`에 쓰이면 준수로 보므로, columns가 `[selected_ref]`와 다른 성공은 경계 위반이 아니라 모델 출력 정밀도 관찰이다.

질문·prompt·model 원문 응답·QueryIR·SQL·행 값·session token·option ID·base URL은 출력·저장하지 않는다.

## 전체 결과

stdout에 한 줄 JSON만 출력한다.

- `status`: 모든 case의 `boundary_ok`가 참이고 총 호출이 정확히 4이면 `COMPLETED`, 아니면 `BOUNDARY_VIOLATION`.
- `summary`: submit 4건 중 `EXECUTED_SUCCEEDED` 수, `columns_match_constraint` 참 수, 총 `model_calls`.
- 메타데이터: smoke version `ollama-prototype-smoke-v1`, `model_tag`, `prompt_version`, `timeout_seconds`. base URL은 출력하지 않는다.
- model 출력이 틀려 422/502가 나도 경계가 지켜졌다면 `COMPLETED`다. 성공 수는 관찰값일 뿐 정확도 주장이 아니다.
- exit code: `COMPLETED` 0, `BOUNDARY_VIOLATION` 1, 설정 오류·미실행 2.
- `--check`: 설정·model·app 조립 뒤 provider 종류가 `ollama`인지 확인하고 cleanup한다. 호출 0회, `{"status":"CHECKED",...,"model_calls":0}`, exit 0.
- 설정·조립 실패: 고정 `{"status":"CONFIG_ERROR"}`만 stdout에 출력하고 exit 2. 원문 예외 없음.
- 예기치 않은 내부 오류: 고정 `{"status":"SMOKE_ERROR"}`, exit 1, cleanup 보장.

## 테스트 (synthetic transport만)

`ollama_startup.build_loopback_transport`를 monkeypatch해 synthetic transport를 주입한다. network·실제 Ollama를 쓰지 않는다.

- 정상 transport: `COMPLETED`, 총 호출 4, cancel·no-match·start 0회, 성공 4건, `columns_match_constraint` 4건.
- 전부 실패하는 transport(timeout·비 JSON·constraint 위반 혼합): 고정 code로 기록되고 `COMPLETED`, 성공 0건.
- `--check`: 호출 0회, transport 생성은 되지만 호출 없음, cleanup 호출.
- mode 누락·중복, 잘못된 URL·tag·timeout, 반복 option: exit 2, 고정 출력, 입력 비반사.
- stdout에 질문 원문·base URL·token·option ID·`SELECT`·행 값이 없음.
- boundary 위반 감지: 호출을 두 번 하는 가짜 provider 등 예산 위반을 만들면 `BOUNDARY_VIOLATION`과 exit 1.
- 예외 경로에서도 cleanup이 호출됨.
- 회귀: `tests/adaptive/test_ollama_startup.py`, `tests/adaptive/test_ollama_constraint_provider.py`, `tests/adaptive/test_synthetic_clarification_prototype.py`.

## live 실행 (메인만)

- 구현·Luna 검토·전체 테스트가 끝난 뒤 메인이 `--check` 후 `--live-synthetic`을 각 1회 실행한다.
- 대상은 `/api/tags`로 확인한 로컬 정확한 tag와 기본 loopback URL, timeout 60초다. 실패해도 재시도하지 않고 그대로 기록한다.
- 결과 문서에는 환경(Ollama version, tag, digest), 공개 관찰 항목 표, 해석 한계만 적는다. 원문 응답·prompt·QueryIR·SQL·행은 기록하지 않는다.

## 사람 관문

완전 합성 질문과 합성 fixture만 쓰므로 E3·L5 대상 자료가 없다. 관찰된 성공 수는 L1/L3 이전의 비확정 관찰이며 성능 주장에 쓰지 않는다. 실제 학교 DB(L2)·UX 확정(L4)과 무관하다. 승인 상태를 바꾸지 않는다.

## 금지

- 실제 CNU·학교 DB·private artifact, PostgreSQL 연결, 외부 host.
- retry, model 대체, raw SQL fallback, 모델 출력 보정.
- 응답·prompt 저장, recording bundle 생성.
- 기존 Phase 7-07/7-08 계약·prototype factory 변경, frontend·legacy 변경.
