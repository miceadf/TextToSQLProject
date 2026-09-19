# 다음 개발 프롬프트: Phase 5-10 live capture 입력·실행 경계

실제 Ollama recording을 위한 **입력 검증과 명시적 실행 경계**를 구현해줘. 이번 단계에서는 합성 입력·합성 transport로만 검증하고, 실제 Ollama 호출이나 실제 응답 저장은 실행하지 마. E3·L5 확인 전에는 운영 데이터 수집을 시작하지 않는다.

## 먼저 확인

- `AGENTS.md`, `docs/next-session-handoff.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5·ADR-023/024·다음 시작점
- `docs/human-validation-checkpoints.md`의 E3·L1·L3·L5와 `docs/ollama-adapter-live-smoke-results-2026-09-19.md`
- `backend/adaptive/harness/ollama.py`, `private_capture.py`, `synthetic_capture_cli.py`, `recording_orchestrator.py`, `recording_bundle_storage.py`, `recording_bundle_contracts.py`, `contracts.py`
- 관련 테스트, 시작 시 `git status --short`와 최근 Conventional Commit. `docs/desktop-test-handoff.md`는 초기 데스크톱 계획의 과거 기록이므로 현재 구현 상태는 계획 문서와 코드로 확인한다.

## 구현 범위

1. 수동 실행용 live capture 진입점을 작게 추가한다. **명시적 `--live`가 없으면 provider 호출과 bundle 쓰기가 0회**여야 한다. 입력은 호출자가 명시한 private target 파일, 정확한 model tag, base URL, timeout, Ollama version, 선택적 digest, model run ID, repository root, private output 경로만 받는다. `.env` 자동 읽기, 모델 추측·fallback·retry, gold 40건 자동 import는 금지한다.
2. private target 파일의 형식과 버전을 명시하고, 기존 `HarnessRequest`·`Ontology`·`PUBLISHED MappingProfile`·`CaptureTarget` 계약으로 엄격히 검증한다. 전체 batch의 형식, 중복 ID, database/mapping 일치, 비어 있는 입력, provenance, 저장 경로와 overwrite 정책을 **첫 provider 호출·파일 생성 전에** 검사한다. 질문·ontology·mapping 원문과 로컬 경로는 오류 문자열·stdout·기본 로그에 넣지 않는다. 입력 파일은 실행용 private 자료이며 Git에 추가하지 않는다.
3. 승인된 live 실행 경로에서는 기존 `OllamaQueryModel`, `declared_provenance()`, `capture_private_bundle()` 및 `recording-bundle-v1` storage를 그대로 연결한다. provider 호출은 target당 1회이며 typed 실패 뒤 다음 target을 처리한다. 성공·실패와 JSON 타입·순서는 기존 recording 계약을 따른다. 별도 SQL 생성·QueryIR 보정·DB 연결·평가 실행은 넣지 않는다.
4. CLI의 성공·실패 출력은 고정 상태/code와 안전한 건수·선언형 provenance 식별 정보만 허용한다. 질문, 모델 응답, HTTP body, 프롬프트, 물리 mapping, DSN, 절대 경로, private manifest 내용을 출력하지 않는다. 실제 recording은 기존 `.schema-adaptive` private 저장 경로에만 원자 게시하고 기본 덮어쓰기를 금지한다. 실행 flag 자체를 E3·L5의 사람 승인으로 간주하지 않는다.
5. 기존 synthetic-only CLI의 의미와 legacy `backend/llm.py`는 보존한다. 별도 진입점 또는 명확히 분리된 명령을 사용해 실수로 synthetic과 live가 섞이지 않게 한다.

## 합성 검증

- opt-in 누락, 잘못된 입력·중복 ID·비공개 경로 위반·기존 bundle·잘못된 provenance: provider 0회, 새 출력 0개, 기존 manifest 보존
- 버전 있는 private target 파일의 strict parsing; 유효한 synthetic target의 순서·필드 보존
- 주입한 합성 transport로 정확한 model tag·URL·timeout·`stream: false`·설정, target당 호출 1회, fallback/retry 0회 확인
- JSON 객체·다른 JSON 타입·비 JSON 텍스트·`TIMEOUT`·`EMPTY_RESPONSE`·`CANCELLED`·`PROVIDER_ERROR`의 capture→strict load 결과 확인
- 오류·stdout·기본 로그·manifest metadata에서 질문·응답·프롬프트·HTTP body·물리 mapping·로컬 경로 비노출 확인. recording 본문 자체는 private artifact이므로 이 비노출 검사의 대상과 구분한다.
- 관련 테스트를 먼저 실행하고 마지막에 `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m compileall -q backend tests`, `git diff --check` 실행

## 실행 금지·사람 확인

- **이번 단계에서 실제 `--live` 실행 금지.** 합성 transport 테스트만 한다. 실제 CNU 질문·SQL·DB 행·식별값을 Ollama에 보내거나 실제 모델 응답을 recording으로 저장·공유하지 않는다.
- 실제 응답 수집 전 E3·L5에서 데이터 사용 목적, 접근 권한, 보관·반출·삭제와 recording 노출 경로를 사람과 확인한다. 확인 결과가 문서화되지 않았다면 live capture는 후속 작업으로 남긴다.
- `run-recorded`, 40×3 benchmark, PostgreSQL adapter, FastAPI/React 연결, 모델 weight·Modelfile·legacy 프롬프트 변경은 제외한다. 성능 확정 전 L1·L3를 확인하고 gold 40건의 `NEEDS_HUMAN_REVIEW`를 유지한다.

## 완료 보고

실제 구현 범위만 `docs/schema-adaptive-text-to-sql-plan.md`의 본문·체크박스·변경 이력에 반영한다. 변경 파일, 테스트 결과, 실제 Ollama 호출 여부(이번 단계는 0회), E3·L5 전에 남은 작업, 다음 작은 작업을 보고해줘. 과거 형식의 Conventional Commit으로 커밋하되 push는 하지 마.
