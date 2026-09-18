# 다음 개발 프롬프트: Phase 5-06 Provider-neutral capture orchestrator

다음 단계인 **provider-neutral recording capture orchestrator와 명시적 실패 분류 계약만** 개발해줘. 완료 후 멈추고 파일 저장, CLI, Ollama·HTTP 호출은 시작하지 마.

## 먼저 확인

- `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5와 현재 체크포인트
- `docs/human-validation-checkpoints.md`의 E3, L1, L3, L5
- `backend/adaptive/harness/model.py`, `contracts.py`, `recorded.py`
- `backend/adaptive/harness/recording_capture.py`
- `backend/adaptive/harness/recording_bundle_contracts.py`, `recording_bundle_storage.py`
- `tests/adaptive/test_recording_capture.py`
- 최근 Conventional Commit 형식과 시작 시 `git status --short`

## 목표

외부 provider adapter를 주입받아 정해진 request를 순서대로 호출하고, 각 성공 JSON 또는 명시적 실패를 기존 `RecordingCaptureSession`에 기록하는 작은 orchestration 코어를 만든다.

이번 단계는 provider-neutral 경계와 실패 의미만 고정한다. 실제 provider를 호출하는 adapter, recording 파일 저장, provenance 생성, CLI는 구현하지 않는다.

## 이번 범위

- provider 호출을 나타내는 최소 Protocol 또는 callable 계약
- 기존 `HarnessRequest`, `Ontology`, `MappingProfile`을 묶는 capture target 계약
- 기존 `RecordedFailureCode`만 사용하는 typed provider failure 계약
- 입력 순서를 보존하는 `capture_recording(...)` 또는 동등한 orchestration API
- 각 target마다 provider를 정확히 한 번 호출
- 성공 반환값을 보정하지 않고 `RecordingCaptureSession.record_success()`로 전달
- 통제된 provider 실패를 `record_failure()`로 변환하고 다음 target 계속 처리
- 예상하지 못한 provider 예외를 payload 비노출 `PROVIDER_ERROR`로 기록하고 계속 처리
- invalid JSON/non-finite response의 명시적 실패 처리
- 중복 request ID와 request/mapping database 불일치의 호출 전 차단
- 완료 시 기존 `ModelRecording` 반환
- 단위 테스트와 계획 문서 갱신

## provider와 실패 계약

- provider는 `request`, `ontology`, `mapping`을 받고 Python JSON 값 또는 JSON으로 검증될 값을 반환한다.
- 문자열, 숫자, boolean, `null`, list, object는 Query IR로 유효한지와 무관하게 성공 응답으로 기록한다. Query IR 검증은 이 계층의 책임이 아니다.
- `None`은 JSON `null`이므로 orchestrator가 빈 응답으로 추측하지 않는다. 실제 빈 응답은 adapter가 typed `EMPTY_RESPONSE` 실패로 알려야 한다.
- timeout, empty response, cancellation, 일반 provider failure는 기존 `RecordedFailureCode.TIMEOUT`, `EMPTY_RESPONSE`, `CANCELLED`, `PROVIDER_ERROR` 중 하나로만 전달한다.
- 예외 문자열, HTTP body, prompt, 질문, 모델 응답을 실패 code나 message에 복제하지 않는다.
- 문자열 matching으로 timeout이나 provider 종류를 추측하지 않는다.
- provider가 bytes, 임의 객체, `NaN`, `Infinity` 등 기록할 수 없는 값을 반환하면 문자열로 바꾸지 않고 해당 attempt를 `PROVIDER_ERROR`로 남긴다.
- 한 attempt의 실패는 다음 attempt를 막지 않는다. 반면 입력 계약 자체가 잘못된 경우에는 provider를 한 번도 호출하지 않고 전체 orchestration을 거부한다.

## 결정성과 실행 정책

- target 순서가 provider 호출 순서이자 `ModelRecording.records` 순서다. 자동 정렬하지 않는다.
- retry, backoff, concurrency, timeout 측정은 이번 단계에서 구현하지 않는다.
- request ID를 자동 생성하거나 교체하지 않는다. caller가 결정적인 ID를 제공해야 한다.
- gold Query IR, 기대 결과 또는 fallback response를 사용하지 않는다.
- mapping fingerprint와 question fingerprint는 기존 `RecordingCaptureSession`이 계산하게 하며 중복 구현하지 않는다.
- provider 반환값, request, ontology, mapping을 수정하지 않는다.
- 전체 target이 실패하더라도 각 실패 record가 존재하면 유효한 `ModelRecording`을 반환한다.
- 빈 target 목록은 provider 호출 없이 명시적으로 거부한다.

## 오류·개인정보 경계

- orchestration 입력 오류와 provider attempt 실패를 구분한다.
- 통제 오류가 필요하면 고정 enum code를 사용하고 안전한 message만 제공한다.
- 질문, response, literal, prompt, mapping sample value, provider 예외 원문을 로그·stdout·오류 문자열에 넣지 않는다.
- recording을 파일에 쓰거나 bundle storage를 자동 호출하지 않는다.
- model/provider provenance를 생성하거나 검증했다고 주장하지 않는다.

## 최소 테스트

- object·string·number·boolean·null·list 성공 응답의 순서와 타입 보존
- typed timeout·empty·cancelled·provider failure가 해당 `RecordedFailureCode`로 기록됨
- 실패 다음 target도 호출되고 전체 record 수가 유지됨
- 예상 밖 provider 예외가 원문 비노출 `PROVIDER_ERROR`가 되고 계속 진행됨
- invalid JSON 값과 non-finite number가 보정 없이 `PROVIDER_ERROR`로 기록됨
- 문자열 형태의 잘못된 Query IR도 성공 JSON으로 보존됨
- provider는 target당 정확히 한 번 호출되고 retry하지 않음
- 중복 request ID, database 불일치, 빈 target을 provider 호출 전에 거부
- 입력 순서와 동일 입력의 결정적 `ModelRecording`
- request·ontology·mapping·response 원본 불변
- 오류 문자열에 실제 질문·응답·provider 예외 원문 비노출
- 생성 결과를 기존 `RecordedModel`로 재생 가능
- 기존 capture·bundle·replay·evaluation 테스트 회귀 없음
- 관련 테스트 후 전체 `tests/` 최종 1회, `compileall`, `git diff --check` 통과

## 제외 범위

- Ollama·HTTP·OpenAI-compatible provider adapter
- 실제 timeout 구현, retry, concurrency, rate limit
- recording bundle 저장·load 변경
- provenance manifest 생성·수정
- capture 또는 evaluation CLI
- 실제 모델 응답과 CNU 자료 수집
- 전체 40×3 benchmark와 모델 성능 측정
- Query IR 검증·수정·자동 보정
- PostgreSQL, 관리자 UI, repair loop, 레거시 API 연결
- 루트 `pytest`의 기존 import 문제 수정

## 사용량·서브 에이전트 규칙

- 이번 범위는 orchestrator 코어 1개와 테스트 1개 수준이므로 **메인 에이전트 단독 구현을 기본값**으로 한다.
- 이는 서브 에이전트 전면 금지가 아니다. 조사 과정에서 provider 경계와 기존 capture 계약을 함께 바꿔야 하는 예상 밖의 독립 작업이 발견되어 품질상 분리가 필요할 때만 리뷰 에이전트 1명을 사용할 수 있다.
- 지정한 파일과 직접 관련된 테스트만 먼저 읽고 저장소 전체를 다시 분석하지 않는다.
- 기존 `RecordingCaptureSession`과 `RecordedFailureCode`를 재사용하고 유사 계약을 중복 생성하지 않는다.
- 개발 중에는 관련 테스트만 실행하고 전체 `tests/`는 마지막에 한 번만 실행한다. 프론트엔드 검사는 실행하지 않는다.
- storage, CLI, 실제 adapter 변경이 필요해지면 범위를 넓히지 말고 후속 단계로 보고한다.
- 완료 보고는 변경 파일, 검증 결과, 남은 한계와 사람 확인만 간결하게 작성한다.

## 사람 확인

합성 provider 테스트에는 새 사람 승인이 필요 없다. 실제 모델을 호출·기록·저장하기 전 E3·L5를 확인한다. recording 기반 결과를 모델 성능으로 확정하기 전 L1·L3를 통과해야 한다. 승인 상태는 자동 변경하지 않는다.

## 완료·커밋

- provider-neutral orchestrator가 성공·실패를 누락 없이 기존 `ModelRecording`으로 조립
- attempt 실패 후 계속 진행하면서 입력 계약 오류는 호출 전에 차단
- payload 비노출과 결정적 순서 검증
- 기존 capture·bundle·replay·evaluation 회귀 없음
- `compileall`, `git diff --check` 통과, working tree는 이 프롬프트 작성 전 변경을 제외하고 깨끗함
- 과거 Conventional Commit 형식으로 core, 테스트, 문서를 적절히 분리 커밋
- 각 커밋 전 staged diff 확인, 원격 push 금지

다음 작업은 **Ollama adapter의 최소 호출·오류 변환 계약**으로 남긴다. 실제 수집 CLI와 40×3 모델 평가는 그 이후 데스크톱 단계에서 개발해줘.
