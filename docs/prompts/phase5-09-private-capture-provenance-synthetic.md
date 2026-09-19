# 다음 개발 프롬프트: Phase 5-09 private capture·provenance 합성 연결

기존 `capture_recording()`과 `write_recording_bundle()`을 작은 수집 경계로 연결해줘. **이번 단계는 합성 provider와 합성 target으로만** capture→private bundle 저장→strict load를 검증한다. Ollama provenance를 실제 adapter 설정에서 재현 가능하게 계산하는 계약을 준비하되, 로컬 Ollama를 호출하거나 실제 모델 응답을 기록·저장하지 마.

## 먼저 확인

- `AGENTS.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5·ADR-023/024·다음 시작점
- `docs/human-validation-checkpoints.md`의 E3·L1·L3·L5와 `docs/ollama-adapter-live-smoke-results-2026-09-19.md`
- `backend/adaptive/harness/ollama.py`, `recording_orchestrator.py`, `recording_capture.py`, `recorded.py`, `recording_bundle_contracts.py`, `recording_bundle_storage.py`
- 기존 recording·bundle·Ollama 테스트, 시작 시 `git status --short`, 최근 Conventional Commit 형식

## 목표와 경계

1. 재사용 가능한 작은 함수 또는 coordinator를 만든다. 명시적 `model_run_id`, 주입된 `QueryModel`, 검증된 `CaptureTarget` 목록, `RecordingProvenance`, repository root, private output 경로를 받아 `capture_recording()` 결과를 기존 `write_recording_bundle()`에 전달한다. batch·provenance·저장 target의 입력 오류는 provider 호출·파일 생성 전에 차단한다. 기존 storage 경로 검사에 작은 공개 preflight가 필요하면 같은 validator를 공유하고 검사 로직을 복제하지 않는다. 성공·typed 실패를 기존 순서와 JSON 타입 그대로 보존한다.
2. 로컬에서 수동으로 실행할 진입점이 필요하면 **합성 fixture 전용**으로 만든다. 명시적 `--synthetic` 없이는 provider를 호출하거나 파일을 쓰지 않는다. 이 단계에는 `--live`, 실제 질문 파일 import, gold 40건 자동 수집, `.env` 자동 로드 기능을 넣지 않는다. synthetic bundle은 기존 storage가 허용하는 `.schema-adaptive` private 경로에만 쓰고, 기본 overwrite는 금지한다.
3. Ollama provenance helper는 `ProviderKind.OLLAMA`와 기존 `RecordingProvenance`를 사용한다. provider version, 정확한 모델 tag, 선택적 모델 digest는 호출자가 명시한다. `verification_status`는 항상 `DECLARED_UNVERIFIED`다. `/api/tags` digest를 독립적으로 증명된 weight hash라고 주장하지 않는다. tag fallback, digest 추측, 빈 값을 임의 버전으로 채우는 동작은 금지한다.
4. `prompt_template_sha256`은 **질문·runtime ontology·mapping을 채우기 전의 versioned template**에서, `generation_config_sha256`은 **실제 전송 설정**에서 결정적으로 계산한다. 현재 adapter의 prompt version·template·`stream: false`·`temperature: 0`과 하나의 코드 출처를 공유하도록 필요한 최소 리팩터링만 한다. hash 대상, UTF-8 인코딩, canonical JSON 규칙, timeout과 모델 tag를 어느 필드에 넣는지 문서화하고 테스트로 고정한다. 프롬프트·설정 원문, 질문, 응답, 물리 mapping, DSN을 manifest·stdout·기본 로그·오류 문자열에 넣지 않는다.
5. 기존 `recording-bundle-v1` 저장·strict load 계약을 재사용한다. storage 경로 방어·원자 게시·overwrite 정책을 새 코드에서 우회하거나 중복 구현하지 않는다. `run-recorded`와 평가 보고서를 자동 실행하지 않는다. 합성 bundle도 논문 성능 자료로 표시하지 않는다.

## 최소 합성 테스트

- 주입 provider의 성공 JSON 값과 `TIMEOUT`·`EMPTY_RESPONSE`·`CANCELLED`·`PROVIDER_ERROR`가 입력 순서대로 기록되고 strict load 후 `RecordedModel`에서 재생됨
- 한 target당 provider 1회; 실패 후 다음 target 처리; preflight 실패 시 provider 0회·출력 파일 0개
- 합성 진입점의 opt-in 경계와 Ollama HTTP 호출 0회. 테스트에서 네트워크 함수를 실패하도록 막아도 통과
- private 경로 밖, tracked target, 기존 bundle 덮어쓰기, 잘못된 provenance는 기존 storage 오류로 거부되고 이전 manifest가 보존됨
- 같은 template·전송 설정은 같은 SHA-256, version·template·설정 변경은 다른 SHA-256. 요청별 질문이나 mapping이 달라도 template hash는 동일
- manifest의 `DECLARED_UNVERIFIED`, record count, content hash가 strict loader에서 확인됨
- 질문·모델 응답·HTTP body·프롬프트·설정 원문·로컬 경로가 manifest·stdout·기본 로그·통제 오류에 노출되지 않음
- 관련 테스트를 먼저 실행하고 마지막에 `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m compileall -q backend tests`, `git diff --check` 실행

## 제외 범위와 사람 확인

- 실제 Ollama 호출, 실제 응답 저장·공유, CNU 질문·DB 행·SQL, 40×3 평가, PostgreSQL 통합
- 모델 weight·Modelfile 변경, prompt 탐색, QueryIR 자동 보정, legacy `backend/llm.py` 변경
- `run-recorded` 변경, bundle schema migration, FastAPI/React 연결, 루트 `pytest`의 기존 `backend/test_correction.py` import 문제 해결
- 합성 recording 테스트에는 새 사람 승인이 필요 없다. **실제 응답을 기록·저장·공유하기 전 E3·L5**의 데이터·보안 범위를 확인한다. **모델 성능 확정 전 L1·L3**를 확인하고 gold 40건의 `NEEDS_HUMAN_REVIEW`를 유지한다. synthetic provenance나 2건 live smoke를 승인·정확도 증거로 사용하지 않는다.

## 완료 보고

설계·로드맵 상태가 바뀌면 `docs/schema-adaptive-text-to-sql-plan.md`의 본문·체크박스·변경 이력을 갱신한다. 변경 파일, 테스트, 실제 Ollama 호출 여부, private 저장 경계, 승인 전 남은 작업만 보고해줘. 과거 형식의 Conventional Commit으로 적절히 커밋하되 push는 하지 마.
