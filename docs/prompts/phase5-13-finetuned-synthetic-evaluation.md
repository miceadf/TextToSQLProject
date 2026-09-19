# 다음 개발 프롬프트: Phase 5-13 기존 fine-tuned 모델의 합성 탐색 평가

Phase 5의 첫 미완료 항목인 **기존 fine-tuned 모델 평가**를 시작해줘. 로컬 `text2sql_qwen:latest`를 정확한 tag로 사용하고, 저장소의 **합성 fixture 40건 × 3스키마**에서만 수집→strict load→오프라인 `run-recorded`를 연결한다. Gold 40건은 `NEEDS_HUMAN_REVIEW`이므로 결과는 **탐색용 잠정 수치**이며 확정 성능이나 논문 결과로 발표하지 마. E3/L5·L1/L3의 팀 결정을 지금 요청하지 않는다.

## 먼저 확인

- `AGENTS.md`, `docs/next-session-handoff.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5·ADR-023/024/025·다음 시작점
- `docs/human-validation-checkpoints.md`의 E3/L5·L1/L3, `docs/phase5-12-synthetic-live-capture-results-2026-09-19.md`
- `backend/adaptive/evaluation/{contracts.py,loader.py,runner.py,run_report.py,recorded_cli.py,fixture_mapping.py}`, `backend/adaptive/harness/{live_capture.py,recorded.py,recording_bundle_storage.py,ollama.py}` 및 관련 테스트
- 시작 시 `git status --short`, 최근 Conventional Commit, `ollama list`·`/api/tags`의 정확한 model tag와 서버 상태. 모델이 없으면 fallback 없이 환경 미준비로 보고한다.

## 구현·실행 범위

1. 기존 benchmark v1의 합성 질문 40건과 flat·normalized·legacy fixture 각각의 runtime ontology·`PUBLISHED` mapping으로 `capture-targets-v1` target 120건을 결정적으로 만든다. request ID는 `run-recorded`와 같은 `deterministic_request_id()`를 재사용한다. target에는 질문과 runtime context만 넣고 **gold IR·기대 결과·실제 DB 행·SQL을 모델 prompt나 capture 파일에 넣지 않는다.** target 파일은 Git 비추적 `.schema-adaptive` 아래에만 둔다.
2. 전체 target의 개수·순서·request ID·database/mapping 일치·fingerprint drift를 합성 FakeModel/transport 테스트로 먼저 검증한다. 별도 생성기가 필요하면 최소 함수/CLI를 추가하고 기존 loader·preflight를 재사용한다. 질문·mapping·gold를 stdout·기본 로그에 출력하지 않는다.
3. 정확한 tag·base URL·timeout·Ollama version·선택적 digest·model run ID·새 private output을 명시하고 `--check`를 먼저 수행한다. 통과할 때만 명시적 `--live`를 한 번 실행한다. target당 호출 1회, retry·fallback·overwrite 없음. 120건은 시간이 걸릴 수 있으므로 실행 전에 전체 소요와 timeout을 확인하되, 일부만 실행하고 40×3 완료로 표시하지 않는다. 서버나 모델이 불가하면 중단 사유와 미실행 상태를 남긴다.
4. private bundle을 strict load해 count·hash·provenance를 확인한 뒤 기존 `run-recorded`에서 오프라인 재생한다. 실행은 합성 SQLite fixture에만 연결한다. 성공·실패·누락을 예정된 120 attempt 분모에서 숨기지 않는다. 기존 runner/report의 계산 계약은 유지하고, 필요하지 않은 benchmark·schema migration은 하지 않는다.
5. 원 응답·질문·프롬프트·SQL·결과 행·물리 mapping·HTTP body·절대 경로를 공개 문서·stdout·기본 로그에 복제하지 않는다. private target, bundle, 상세 report는 Git 비추적 경로에 둔다. 공개 문서에는 환경·실행 여부·시도 건수·안전한 실패 유형과 **잠정/미승인** 표시, 미검증 사항만 남긴다. 모델 결과를 QueryIR로 자동 보정하지 않는다.

## 테스트와 경계

- target 생성 120건과 각 variant의 정확한 request ID·ontology·Published Mapping, gold 비포함, fingerprint drift 차단
- `--check` 실패 시 Ollama 호출·파일 생성 0회; 합성 transport에서 target당 호출 1회, typed failure 뒤 다음 target 처리
- strict load 후 `run-recorded`가 동일 context에서 재생하고 실패·누락을 분모에 남김; report·stdout 비노출
- 관련 테스트를 먼저 실행하고 마지막에 `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m compileall -q backend tests`, `git diff --check` 실행
- 실제 CNU 자료·DB·식별값, PostgreSQL 통합, base instruct 모델 비교, prompt 탐색, LoRA 학습, legacy `backend/llm.py` 변경은 제외한다.
- Gold 40건의 `NEEDS_HUMAN_REVIEW`를 유지한다. 잠정 수치를 최종 점수·논문 주장으로 바꾸기 전 L1·L3를 확인한다. 실제 CNU 자료 사용·공유 또는 실제 데이터 배포·외부 시연 전 E3·L5를 확인한다. 지금은 팀 결정을 요청하지 않는다.

## 완료 보고

`docs/schema-adaptive-text-to-sql-plan.md`의 본문·체크박스·변경 이력과 `docs/next-session-handoff.md`를 **실제 완료 범위만** 갱신한다. 현재 fine-tuned 모델 평가 항목은 검토 전 잠정 실험만 했다면 완료 체크하지 않는다. 변경 파일·테스트·실제 Ollama 호출 수·120 attempt 완료 여부·안전한 잠정 결과의 한계·다음 작은 작업을 보고해줘. 공개 가능한 코드·문서만 과거 형식의 Conventional Commit으로 커밋하고 push는 하지 마.
