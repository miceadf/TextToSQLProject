# 다음 개발 프롬프트: Phase 5-12 합성 입력의 첫 private live capture

Phase 5-11의 `--check`와 `--live` 경계를 **로컬 Ollama + 합성 질문 2건**으로 작게 검증해줘. 질문·ontology·mapping은 전부 합성으로 만들고, 모델 응답은 기존 private bundle에만 저장한다. E3·L5의 팀 결정은 실제 CNU 자료 사용·공유·배포·외부 시연 전에 남겨둔다. 합성 개발을 그 결정 때문에 중단하지 마.

## 먼저 확인

- `AGENTS.md`, `docs/next-session-handoff.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5·ADR-023/024/025·다음 시작점
- `docs/human-validation-checkpoints.md`의 E3·L1·L3·L5와 `docs/live-capture-human-review.md`
- `backend/adaptive/harness/live_capture.py`, `ollama.py`, `private_capture.py`, `recording_bundle_storage.py`, `recorded.py`, `validator.py`와 관련 테스트
- 시작 시 `git status --short`, 최근 Conventional Commit, `ollama list`와 `/api/tags`의 정확한 model tag·서버 상태. tag가 없거나 서버가 응답하지 않으면 다른 모델로 대체하지 말고 환경 미준비로 보고한다.

## 실행 범위

1. `.schema-adaptive` 아래 Git 비추적 `capture-targets-v1` 파일에 **완전 합성 질문 2건**과 고정 합성 runtime ontology·`PUBLISHED` mapping을 준비한다. 물리 table/column 이름도 합성으로 한다. 실제 CNU 질문·dataset·SQL·DB 행·식별값, gold IR·기대 결과를 쓰지 않는다.
2. 정확한 model tag, base URL, timeout, Ollama version, 선택적 digest, model run ID, 새 private output 경로를 명시한다. `--check`를 먼저 실행하고 성공 상태·target 건수·선언 provenance fingerprint만 확인한다. 실패하거나 기존 bundle이 있으면 중단한다. fallback·retry·overwrite는 하지 않는다.
3. `--live`를 한 번 실행한다. target당 adapter 호출은 한 번으로 제한한다. 원 응답은 stdout·기본 로그·공개 문서에 복제하지 않고 기존 private bundle에만 보관한다. 실제 호출이 불가능하면 고정 실패 code와 미검증 상태만 보고한다.
4. strict loader로 manifest·hash·건수·`DECLARED_UNVERIFIED` provenance를 확인하고 `RecordedModel`에서 입력 순서, JSON 타입 또는 typed failure를 재생한다. QueryIR 구조·semantic field/operator 검증은 adapter 밖에서 한다. SQL·Markdown·설명문을 QueryIR로 번역·보정하지 않는다.
5. 비식별 case ID별 성공/실패 code, JSON 여부·타입, QueryIR 검증 code, 안전한 경과 시간 정도만 결과 문서에 남긴다. 질문·응답·프롬프트·HTTP body·물리 mapping·절대 경로·DSN은 출력·커밋하지 않는다. private target과 bundle은 Git 비추적 상태를 확인한다.

## 검증·완료 보고

- 관련 테스트를 먼저 실행한다. 코드가 바뀌면 마지막에 `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m compileall -q backend tests`, `git diff --check`를 실행한다. 문서만 바뀌어도 `git diff --check`와 Git 비추적 상태를 확인한다.
- 합성 2건의 결과를 모델 정확도, 학교 간 일반화, 40×3 benchmark 성공으로 해석하지 않는다. `run-recorded` 평가, PostgreSQL·실제 DB 연결, 모델 weight·Modelfile·legacy `backend/llm.py` 변경은 제외한다.
- E3·L5는 실제 CNU 자료 사용·공유·배포·외부 시연 전에 팀이 결정한다. 이번 합성 로컬 수집은 그 결정을 대신하지 않는다. 성능 확정 전 L1·L3를 확인하고 gold 40건의 `NEEDS_HUMAN_REVIEW`를 유지한다.
- 실제 완료 범위만 `docs/schema-adaptive-text-to-sql-plan.md`와 `docs/next-session-handoff.md`에 반영한다. 변경 파일, 테스트, 실제 Ollama 호출 여부, 안전한 형식 분류, 남은 한계를 보고해줘. 공개 가능한 코드·문서만 과거 형식의 Conventional Commit으로 커밋하고 push는 하지 마.
