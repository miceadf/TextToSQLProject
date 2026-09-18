# 다음 개발 프롬프트: Phase 5-04 Recording capture session 코어

다음 단계인 **메모리 내 recording capture session 코어만** 개발해줘. 완료 후 멈추고 파일 저장, CLI, Ollama 호출은 시작하지 마.

## 먼저 확인

- `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5와 현재 체크포인트
- `docs/human-validation-checkpoints.md`의 E3, L1, L3, L5
- `backend/adaptive/harness/model.py`, `contracts.py`, `recorded.py`
- `tests/adaptive/test_recorded_model.py`
- 최근 Conventional Commit 형식과 시작 시 `git status --short`

## 목표

외부 provider adapter가 전달한 성공 JSON 또는 명시적 실패 코드를 기존 `recorded-model-v1`의 `ModelRecording`으로 안전하고 결정적으로 조립하는 capture session을 만든다.

이번 단계는 provider를 호출하지 않는다. 파일도 쓰지 않는다. recording provenance를 증명하지도 않는다. 후속 storage·provider 계층이 재사용할 작은 코어 계약을 만드는 것이 목적이다.

## 이번 범위

- `model_run_id`로 시작하는 `RecordingCaptureSession` 계약
- `record_success(request, mapping, response)`
- `record_failure(request, mapping, failure_code)`
- request에서 `request_id`, `database_id`, question fingerprint를 생성
- runtime Mapping Profile에서 mapping fingerprint를 생성
- 기존 `RecordedSuccess`, `RecordedFailure`, `ModelRecording` 계약 재사용
- capture 순서를 보존한 결정적 `finalize()`
- 성공 response의 JSON 구조·타입과 값을 수정 없이 보존
- caller가 capture 후 원본 response를 변경해도 session 내부 기록은 변하지 않도록 방어적 복사
- 중복 request ID, 비정상 JSON 값, 비유한 숫자, 잘못된 실패 코드의 명시적 거부
- finalize 후 추가 capture 차단
- 빈 session finalize 차단
- 오류 메시지에서 질문·응답·literal 원문 비노출
- 단위 테스트와 계획 문서 갱신

## capture 계약

- hash를 호출자가 직접 전달하게 하지 않는다. session이 기존 `question_fingerprint()`와 `mapping_fingerprint()`로 계산한다.
- 성공 response는 Pydantic `JsonValue`로 손실 없이 검증 가능한 값만 허용한다.
- 문자열 response는 잘못된 Query IR일 수 있어도 유효한 JSON 값이므로 그대로 기록한다. Query IR 유효성 검사는 capture 계층이 대신하지 않는다.
- `NaN`, `Infinity`, bytes, 임의 객체 등 JSON recording으로 재현할 수 없는 값은 문자열로 변환하지 않고 안전한 capture 오류로 거부한다.
- provider 예외를 session이 추측해 분류하지 않는다. 호출 계층이 기존 `RecordedFailureCode` 중 하나를 명시적으로 전달한다.
- 같은 request ID를 다시 기록해 기존 결과를 덮어쓰거나 first/last wins로 처리하지 않는다.
- 성공과 실패가 같은 request ID로 들어와도 중복으로 거부한다.
- capture 순서가 `ModelRecording.records` 순서다. 자동 정렬하지 않는다.
- `finalize()`는 session을 seal하고 불변 `ModelRecording`을 반환한다. 반복 호출 허용 여부는 하나로 정해 테스트로 고정하되, 서로 다른 결과를 만들면 안 된다.
- 원본 request, mapping, response를 변경하지 않는다.

## 오류와 개인정보 경계

- 별도의 안전한 `RecordingCaptureError` 또는 동등한 통제 오류를 정의한다.
- 오류 code가 필요하면 enum으로 고정하고 원문 예외·payload를 message에 복제하지 않는다.
- 질문·response·literal·mapping sample value를 로그로 출력하지 않는다.
- capture session은 recording 내용을 report나 stdout으로 출력하지 않는다.
- 이번 단계에서는 thread safety나 여러 프로세스 동시 capture를 지원한다고 주장하지 않는다.

## 최소 테스트

- 성공 object, string, number, boolean, null, list의 JSON 타입 보존
- failure code별 `RecordedFailure` 생성
- question/database/mapping identity hash 정확성
- capture 순서와 동일 입력의 결정적 `ModelRecording`
- capture 후 caller response mutation이 결과를 바꾸지 않음
- 중복 request ID 거부와 기존 record 불변
- 성공 후 실패 및 실패 후 성공의 중복 차단
- 비유한 숫자, bytes, 임의 객체 거부
- 잘못된 failure code 거부
- 빈 finalize와 finalize 후 capture 차단
- 오류 문자열에 실제 질문·응답·literal 비노출
- 생성한 `ModelRecording`을 기존 `RecordedModel`로 정상 재생 가능
- 기존 recorded model·evaluation tests 회귀 없음
- 관련 테스트 후 전체 `tests/` 최종 1회, `compileall`, `git diff --check` 통과

## 제외 범위

- recording JSON 파일 저장, atomic write, overwrite 정책
- provenance manifest 또는 bundle
- model name·digest, prompt hash, temperature, seed, provider version 수집
- QueryModel wrapper, provider 예외 자동 분류
- Ollama·HTTP·PostgreSQL 연결
- capture·evaluation CLI 변경
- `run-recorded` 변경
- 실제 질문·응답 수집과 40×3 benchmark
- Query IR 수정·자동 보정·검증 정책 변경
- 관리자 UI, repair loop, 레거시 API 연결
- 루트 `pytest`의 기존 import 문제 수정

## 사용량 절감 규칙

- 작은 코어 작업이므로 **서브 에이전트를 사용하지 않는다**.
- 위에 지정한 파일과 직접 관련된 테스트만 읽는다. 저장소 전체 구조를 다시 조사하지 않는다.
- 기존 recording Pydantic 계약과 fingerprint 함수를 재사용한다.
- 새 파일은 가능하면 코어 1개와 테스트 1개 이내로 제한한다.
- 개발 중에는 관련 테스트만 실행하고 전체 `tests/`는 마지막에 한 번만 실행한다. 프론트엔드 검사는 실행하지 않는다.
- storage나 provider 변경이 필요해지면 범위를 넓히지 말고 후속 단계로 보고한다.
- 완료 보고는 변경 파일, 검증 결과, 남은 한계와 사람 확인만 간결하게 작성한다.

## 사람 확인

합성 response 기반 코어 테스트에는 새 사람 승인이 필요 없다. 실제 모델 응답을 capture하기 전 E3·L5를 확인한다. recording 기반 결과를 모델 성능으로 확정하기 전 L1·L3를 통과해야 한다. 승인 상태는 자동 변경하지 않는다.

## 완료·커밋

- 기존 `recorded-model-v1`과 호환되는 결정적 capture session 완성
- payload를 수정·보정·노출하지 않고 성공·실패를 기록
- 기존 replay와 evaluation 경로 회귀 없음
- `compileall`, `git diff --check` 통과, working tree clean
- 과거 Conventional Commit 형식으로 core, 테스트, 문서를 적절히 분리 커밋
- 각 커밋 전 staged diff 확인, 원격 push 금지

다음 작업은 **private recording atomic storage와 provenance manifest 계약**으로 남긴다. 그 이후에 provider adapter와 실제 수집 CLI를 개발해줘.
