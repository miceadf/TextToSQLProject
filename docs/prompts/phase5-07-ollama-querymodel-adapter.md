# 다음 개발 프롬프트: Phase 5-07 Ollama QueryModel adapter

**Ollama `QueryModel` adapter의 최소 호출·오류 변환 계약만** 구현해줘. 합성 transport 테스트까지 완료하고 멈춰. 실제 응답 수집·저장 CLI, 40×3 평가, PostgreSQL 통합은 다음 단계로 남겨줘.

## 먼저 확인

- `AGENTS.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5·다음 시작점
- `docs/desktop-test-handoff.md`, `docs/desktop-smoke-results-2026-09-19.md`
- `docs/human-validation-checkpoints.md`의 E3, L1, L3, L5
- `backend/adaptive/harness/model.py`, `contracts.py`, `recording_orchestrator.py`, `recorded.py`, `engine.py`
- `backend/llm.py`의 legacy 경계와 `tests/adaptive/test_recording_orchestrator.py`
- 시작 시 `git status --short`와 최근 Conventional Commit 형식

## 목표와 계약

기존 `QueryModel.generate_ir(request, ontology, mapping)` 계약을 구현하는 Ollama adapter를 추가한다. 합성 transport를 주입해 네트워크 없이 호출·파싱·실패 분류를 검증할 수 있어야 한다. 기존 legacy `backend/llm.py`의 SQL 생성기와 프롬프트를 재사용하거나 변경하지 않는다.

- adapter 설정은 base URL, 모델 tag, timeout을 명시적으로 받는다. `.env` 읽기는 호출자 책임으로 두고 모델명을 추측·치환하지 않는다. secret·DSN을 설정이나 오류에 넣지 않는다.
- 한 요청은 현재 로컬 Ollama에서 확인한 `/api/generate`에 `stream: false`로 보낸다. 호출당 retry·fallback·동시 실행은 없다.
- 결정적이고 version을 식별할 수 있는 최소 프롬프트를 사용한다. 질문과 runtime ontology의 entity·field·자료형·허용 연산자, Published Mapping에서 사용 가능한 **semantic field 이름**만 포함한다. 물리 table/column, 실제 행, gold IR, 기대 결과, 비밀 설정은 보내지 않는다. 출력 형식은 공통 `QueryIR` JSON으로 지시한다.
- `temperature: 0` 등 실제 전송 설정을 테스트로 고정한다. 프롬프트와 설정의 원문을 로그·예외에 넣지 않는다. 향후 provenance에 필요한 식별자나 hash가 있다면 코드에서 재현 가능하게 하되 이번 단계에서 manifest를 생성하지 않는다.
- HTTP 응답의 envelope는 별도로 검사한다. 성공 envelope의 `response`가 비어 있거나 공백뿐이면 기존 `RecordingProviderError(EMPTY_RESPONSE)`를 발생시킨다.
- 비어 있지 않은 모델 출력이 유효한 JSON이면 Python JSON 값으로 반환한다. 객체, 배열, 문자열, 숫자, boolean, `null`의 타입을 보존한다. 중복 key·비유한 수 등 JSON 의미가 불확실한 출력은 유효한 객체로 조용히 바꾸지 않는다.
- 모델 출력이 SQL이나 설명문처럼 JSON이 아니면 **원래 텍스트를 문자열로 반환**한다. 기존 orchestrator가 이를 성공 JSON 문자열로 보존하고, 후속 `QueryIR` 검증이 `MODEL_INVALID_OUTPUT`으로 판정하게 한다. SQL을 QueryIR로 번역·보정하거나 provider 장애로 위장하지 않는다.
- 네트워크 timeout은 `RecordingProviderError(TIMEOUT)`, 실제 cancellation은 `CANCELLED`, HTTP 오류·연결 실패·잘못된 envelope 등 provider 호출 오류는 `PROVIDER_ERROR`로 변환한다. 취소 신호를 지원하지 않는 transport라면 취소를 임의 추측하지 말고 지원 범위를 명시한다.
- provider 예외 원문, HTTP body, 모델 출력, 질문, 프롬프트, 로컬 경로를 오류 문자열·stdout·기본 로그에 포함하지 않는다. 오류 원인은 고정 code로만 외부에 전달한다.

## 최소 테스트

- 합성 transport가 정확한 URL, 모델 tag, `stream: false`, timeout, 안정적인 프롬프트·설정을 받음
- 정상 JSON 객체와 다른 JSON 타입의 보정 없는 반환
- legacy 형태 SQL 텍스트·Markdown fence·설명문을 그대로 문자열로 반환하고, 실제 harness에서 유효한 QueryIR로 오인하지 않음
- 빈 문자열·공백 응답은 `EMPTY_RESPONSE`; JSON `null`은 성공 값으로 구분
- timeout, 연결 실패, HTTP 오류, malformed envelope, 취소의 명시적 실패 code
- 모델명 자동 fallback과 재시도가 없고 호출당 transport 실행 1회
- 예외·stdout에 질문, 모델 응답, HTTP body, 프롬프트, 로컬 경로가 노출되지 않음
- 기존 `capture_recording()`에 연결했을 때 성공 값과 typed failure가 입력 순서대로 기록되고, 실패 뒤 다음 target을 처리함
- `QueryIR` 구조 검증은 adapter 밖에서 수행됨. JSON 객체여도 잘못된 field·operator는 기존 validator가 거부함
- 관련 테스트를 먼저 실행하고 마지막에 `python -m pytest tests -q`, `python -m compileall -q backend tests`, `git diff --check` 실행. Windows에서는 `.venv/Scripts/python.exe` 사용 가능

## 제외 범위

- 실제 CNU 질문·SQL·DB 행을 모델로 전송
- 실제 모델 응답 recording 저장, private bundle·provenance·수집 CLI 변경
- `run-recorded` 또는 40×3 benchmark 실행과 모델 정확도 수치 발표
- 모델 weight·Modelfile 변경, 재학습, prompt 탐색·성능 최적화
- PostgreSQL adapter, 기존 FastAPI/React 연결, repair loop, 관리자 UI
- 루트 `pytest`의 기존 `backend/test_correction.py` import 문제 해결

## 사람 확인과 완료 보고

합성 transport 테스트에는 새 사람 승인이 필요 없다. 추후 실제 응답을 기록·저장·공유하기 전 E3·L5를 확인하고, 모델 성능 확정 전 L1·L3를 확인한다. 기존 gold 40건의 `NEEDS_HUMAN_REVIEW` 상태를 바꾸지 않는다.

완료 보고에는 변경 파일, 테스트 결과, 실제 Ollama를 호출했는지 여부, 현재 SQL 생성 모델의 한계, 다음 작은 작업만 적어줘. 설계·로드맵 상태가 바뀌면 `docs/schema-adaptive-text-to-sql-plan.md`의 해당 본문·체크박스·변경 이력을 갱신해줘. 과거 형식의 Conventional Commit으로 적절히 커밋하되 push는 하지 마.
