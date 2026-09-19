# 다음 개발 프롬프트: Phase 5-08 Ollama adapter 합성 live smoke

`OllamaQueryModel`을 **로컬 Ollama와 소수의 합성 입력으로만** 호출해 Phase 5-07의 실제 전송·응답 분류 경계를 확인해줘. 구현이 필요한 경우에는 명시적으로 실행해야 하는 작은 smoke 진입점과 그 합성 테스트만 추가해줘. 실제 응답 recording·저장 CLI, DB 실행, 40×3 평가는 하지 마.

## 먼저 확인

- `AGENTS.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5·다음 시작점·변경 이력
- `docs/desktop-test-handoff.md`, `docs/desktop-smoke-results-2026-09-19.md`, `docs/human-validation-checkpoints.md`의 E3·L1·L3·L5
- `backend/adaptive/harness/ollama.py`, `contracts.py`, `validator.py`, `engine.py`, `tests/adaptive/test_ollama_query_model.py`
- 시작 시 `git status --short`, 최근 Conventional Commit, 로컬 `ollama list`와 `/api/tags`에서 실제 사용 가능한 **정확한 모델 tag**. 없거나 서버가 응답하지 않으면 다른 모델로 대체하지 말고 환경 미준비로 보고해줘.

## 작은 구현·실행 범위

1. 현재 adapter의 기본 urllib transport가 로컬 `/api/generate`에서 동작하는지 확인한다. 모델 tag, base URL, timeout은 호출자가 명시하며, 모델명 추측·fallback·retry는 하지 않는다. `backend/llm.py`와 legacy 프롬프트·API는 변경하지 않는다.
2. 필요하다면 수동 실행 전용 smoke 진입점을 추가한다. **명시적 `--live` 없이는 HTTP 호출하지 않고**, 합성 질문 2~3개와 고정된 합성 runtime `Ontology`·`PUBLISHED MappingProfile`만 사용한다. 물리 mapping은 합성 이름을 쓰되 adapter 전송에는 semantic 이름만 들어가야 한다. 실제 DB 연결, SQL 실행, gold IR·기대 결과 사용은 금지한다.
3. 호출당 adapter 실행은 한 번만 한다. 원 응답을 파일·recording·report·로그·stdout에 저장하지 않는다. 각 합성 case의 비식별 ID, 모델 tag·가능하면 digest, prompt version, 설정의 안전한 식별 정보, JSON 파싱 여부·JSON 타입, `QueryIR` 구조 검증 여부, semantic field/operator 검증 결과 코드, provider 실패 code, 대략적인 경과 시간만 요약한다. 질문·응답·프롬프트·HTTP body·로컬 경로·DSN은 출력하지 않는다. 모델 출력이 SQL이나 Markdown이면 원문을 보이지 않고 `NON_JSON_TEXT`로만 분류한다.
4. `QueryIR` 검증은 adapter 밖에서 한다. 모델 출력이 JSON 객체라도 잘못된 구조·field·operator를 성공으로 세지 않는다. SQL 텍스트를 QueryIR로 번역하거나 프롬프트를 즉흥적으로 바꾸지 않는다. 기존 fine-tuned SQL 생성 모델이 QueryIR을 못 만들 수 있음을 실패가 아닌 관찰로 기록한다.
5. 순수 합성 transport 테스트에서 opt-in 경계, 단일 호출, 출력 비노출, JSON/비 JSON/typed failure의 분류를 확인한다. 네트워크 오류나 모델 부재는 fixed code만 표시하고 실제 live smoke를 성공으로 표시하지 않는다. timeout 이후 강제 취소를 구현한 것으로 주장하지 않는다.
6. **실제 live 호출이 가능할 때만** 합성 질문으로 smoke를 실행한다. 결과는 안전한 요약만 새 문서에 남긴다. 실행 불가하면 원인 코드·미검증 상태를 남기고 멈춘다. 모델 weight, Modelfile, 데이터셋, legacy 시스템은 변경하지 않는다.

## 제외 범위와 사람 확인

- 실제 CNU 질문·SQL·DB 행·식별값을 모델로 보내지 않는다.
- `capture_recording()`, private bundle, provenance manifest, 수집·저장 CLI, `run-recorded`, 40×3 benchmark, PostgreSQL adapter, FastAPI/React 연결은 건드리지 않는다.
- 이번 **합성 입력의 비저장 live smoke**는 E3·L5의 실제 데이터·응답 recording 승인을 대신하지 않는다. 이후 실제 응답을 기록·저장·공유하기 전 E3·L5를 확인하고, 모델 성능 수치를 확정하기 전 L1·L3를 확인한다. gold 40건의 `NEEDS_HUMAN_REVIEW`는 유지한다.
- 모델 정확도, 학교 간 일반화, PostgreSQL adaptive 통합 성공 수치를 발표하지 않는다.

## 검증·완료 보고

- 관련 합성 테스트를 먼저 실행한다. 마지막에 `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m compileall -q backend tests`, `git diff --check`를 실행한다. 루트 `pytest`의 기존 `backend/test_correction.py` import 문제는 범위 밖이다.
- `docs/schema-adaptive-text-to-sql-plan.md`에서 실제 완료한 범위만 체크하고 다음 시작점·변경 이력을 갱신한다. live smoke 결과 문서에는 환경·실행 여부·안전한 형식 분류·실패 코드·미검증 사항만 기록한다.
- 완료 보고에 변경 파일, 테스트, 실제 Ollama 호출 여부, SQL 생성 모델의 QueryIR 한계, 다음 작은 작업을 적는다. 과거 형식의 Conventional Commit으로 커밋하되 push는 하지 않는다.
