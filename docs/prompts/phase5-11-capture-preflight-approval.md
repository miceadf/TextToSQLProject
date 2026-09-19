# 다음 개발 프롬프트: Phase 5-11 live capture 사전 점검·승인 자료

Phase 5-10의 live capture 경계에 **호출·저장 없는 사전 점검**을 추가하고 E3·L5 사람 검토에 필요한 자료를 준비해줘. 이번 단계는 합성 target과 합성 transport로만 검증한다. 실제 Ollama 호출, 실제 질문·응답 recording, 40×3 평가는 실행하지 마.

## 먼저 확인

- `AGENTS.md`, `docs/next-session-handoff.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5·ADR-023/024/025·다음 시작점
- `docs/human-validation-checkpoints.md`의 E3·L1·L3·L5, `docs/ollama-adapter-live-smoke-results-2026-09-19.md`
- `backend/adaptive/harness/live_capture.py`, `private_capture.py`, `recording_bundle_storage.py`, `recording_orchestrator.py`, `ollama.py`와 관련 테스트
- 시작 시 `git status --short`, 최근 Conventional Commit. 기존 `docs/desktop-test-handoff.md`의 구형 상태 설명보다 현재 계획·코드를 우선한다.

## 목표

1. CLI에 명시적 사전 점검 모드를 추가한다(예: `--check`). `--check`와 `--live`는 서로 배타적이어야 한다. `--check`는 versioned private target 파일, 모델 설정·선언 provenance, model run ID, output 경로·overwrite 정책을 live와 **같은 검증 함수**로 점검한다. 검증 로직을 복제하지 않는다. 기본 실행도 계속 provider 호출·파일 생성 0회다.
2. 사전 점검은 Ollama HTTP 요청, provider `generate_ir()`, bundle 파일 생성·수정, 실제 응답 수집을 절대 하지 않는다. 경로·입력 파일을 검증할 때 불필요한 내용을 출력하거나 새로운 공개 artifact를 만들지 않는다. 출력은 고정 상태/code, target 건수, 모델 tag·provenance의 안전한 식별값 정도로 제한한다. 질문·응답·프롬프트·물리 mapping·HTTP body·DSN·절대 경로를 stdout·기본 로그·통제 예외에 넣지 않는다.
3. live 실행도 동일한 preflight 결과를 사용하게 하되 Phase 5-10의 target당 단일 호출·typed failure·private atomic storage·기본 overwrite 금지 계약을 보존한다. 기본 urllib transport의 능동 취소나 독립적인 모델 출처 검증을 새로 주장하지 않는다. `--check` 성공을 실제 데이터 사용 승인으로 표시하지 않는다.
4. `docs/live-capture-human-review.md`에 E3·L5 검토용 **미기입 결정 양식**을 작성한다. 데이터 출처·사용 목적, 승인 주체, 전송할 질문 범위, 모델·저장 위치 접근 권한, recording·로그·백업·반출·보존 기간·삭제 절차, 허용/보류 결정과 근거를 사람이 채울 수 있게 한다. 현재 상태를 `PENDING_HUMAN_REVIEW`로 표시하고, 실제 질문·응답·경로·비밀값을 문서에 복사하지 않는다. `--live` flag나 합성 smoke를 승인 증거로 쓰지 않는다.

## 합성 테스트

- `--check` 성공·실패 모두 HTTP 0회, provider 0회, 새 출력 파일 0개; 기존 bundle/manifest 불변
- live와 check가 동일한 batch·provenance·path preflight를 사용하고, 잘못된 target·중복 ID·DRAFT mapping·모델 설정·tracked/nonprivate output을 호출 전에 거부
- `--check`와 `--live` 동시 지정 거부; 명시적 모드 없이 실행하면 호출·저장 없음
- 기존 live 합성 transport의 단일 호출·recording 순서·typed failure·strict load 회귀 통과
- 성공·오류 출력, 로그, 문서에 합성 비밀 문자열·절대 경로 비노출
- 관련 테스트를 먼저 실행하고 마지막에 `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m compileall -q backend tests`, `git diff --check` 실행

## 경계와 완료 보고

- 이번 단계에서 실제 `--live` 실행, 실제 CNU 질문·SQL·DB 행 전송, 실제 모델 응답 저장·공유를 하지 않는다. 승인 양식은 AI가 대신 승인하지 않는다. 실제 응답 수집 전 E3·L5의 사람이 범위를 확인하고 문서화해야 한다.
- `run-recorded`, 40×3 benchmark, PostgreSQL, FastAPI/React, 모델 weight·Modelfile·legacy `backend/llm.py` 변경은 제외한다. 성능 확정 전 L1·L3를 확인하고 gold 40건의 `NEEDS_HUMAN_REVIEW`를 유지한다.
- 실제 완료 범위만 계획 문서의 본문·체크박스·변경 이력과 `docs/next-session-handoff.md`에 반영한다. 변경 파일·테스트·실제 Ollama 호출 여부·승인 대기 항목·다음 작은 작업을 보고해줘. 과거 형식의 Conventional Commit으로 커밋하되 push는 하지 마.
