# 다음 개발 프롬프트: Phase 5-14 QueryIR field 실패 분석·versioned prompt 1회 비교

Phase 5-13의 합성 40×3 잠정 결과에서 `UNKNOWN_FIELD` 21건을 **private 응답 비노출** 상태로 분류하고, 그 근거로 기존 fine-tuned 모델의 QueryIR 동적 schema prompt를 **한 가지 versioned 변형**과 비교해줘. 같은 40건은 이미 개발에 사용된 평가 세트이므로 새 비교도 탐색용 개발 결과다. 확정 정확도나 일반화 성능으로 발표하지 마.

## 먼저 확인

- `AGENTS.md`, `docs/next-session-handoff.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5·평가 설계·다음 시작점·ADR-023/024/025
- `docs/phase5-13-finetuned-synthetic-exploratory-results-2026-09-19.md`, `docs/human-validation-checkpoints.md`의 E3/L5·L1/L3
- `backend/adaptive/harness/ollama.py`, `live_capture.py`, `recorded.py`, `recording_bundle_storage.py`, `validator.py`; `backend/adaptive/evaluation/capture_targets.py`, `runner.py`, `run_report.py`, `recorded_cli.py`와 관련 테스트
- 시작 시 `git status --short`, 최근 Conventional Commit, `ollama list`·`/api/tags`의 정확한 tag. 현재 fine-tuned tag 외 base instruct 모델이 없으면 대체 모델을 추측·다운로드하지 않는다.

## 범위

1. Phase 5-13의 private recording·상세 report를 **읽기 전용**으로 검사한다. `UNKNOWN_FIELD` 21건의 원인을 원 응답을 공개하지 않고 고정 범주로 집계한다. 예: ontology 밖 semantic field, entity 접두사 불일치, 물리 column 형태, 대소문자/표기 차이, 그 외. 분류 실패·중복·판별 불가도 별도 건수로 남긴다. 공개 문서에는 원 field 이름, 질문, 값, SQL, 모델 출력, private 경로를 쓰지 않는다. 기존 bundle·report를 수정하지 않는다.
2. 이 집계로 **검증 가능한 가설 하나**를 정하고 `query-ir-v1`을 그대로 보존한 채 `query-ir-v2`처럼 식별되는 prompt 변형을 추가한다. 기존 default 동작은 v1로 유지하고 새 버전은 명시적으로 선택할 때만 쓴다. semantic field를 허용 목록에서 정확히 복사하도록 지시하되 물리 table/column, 행, gold IR·기대 결과, 비밀 설정을 prompt에 넣지 않는다. SQL/Markdown을 QueryIR로 자동 번역·보정하지 않는다.
3. prompt version·template hash는 실제 선택한 template에서, generation config hash는 실제 전송 설정에서 계산한다. 같은 version/settings는 동일 hash, v1/v2는 다른 hash여야 한다. 호출당 1회, `stream: false`, `temperature: 0`, timeout·model tag 명시, fallback·retry 없음. 기존 `backend/llm.py` 및 모델 weight·Modelfile은 변경하지 않는다.
4. 합성 transport 테스트와 `--check`를 먼저 통과시킨 뒤, 모델과 서버가 가능하면 **같은 합성 40×3 target**, 같은 정확한 fine-tuned tag, 새 model run ID·private output으로 v2를 한 번 수집한다. strict load→오프라인 `run-recorded`를 수행하고 v1/v2를 같은 case×variant 단위로 대응시켜 구조 exact, 하네스 실행, semantic 일치, `UNKNOWN_FIELD`, provider 실패·누락을 같은 120 attempt 분모로 비교한다. v1 recording과 세트 자체는 수정하지 않는다.
5. 공개 결과는 prompt/version/hash의 안전한 식별 정보, 전체 건수, 실패 범주, paired 변화 요약과 **동일 개발 세트에 맞춘 탐색 결과**라는 한계만 남긴다. 질문·응답·프롬프트 원문·filter 값·물리 mapping·SQL·결과 행·HTTP body·절대 경로를 stdout·기본 로그·공개 문서에 쓰지 않는다. private input·bundle·상세 report는 Git 비추적 경로에만 둔다.

## 최소 검증

- v1 기본 동작·기존 provenance hash 회귀, 명시적 v2 선택·서로 다른 template hash, 전송 설정 불변
- 합성 transport에서 v1/v2 prompt의 semantic field만 전송되고 물리 mapping·gold·비밀 문자열은 전송되지 않음
- private 실패 분류의 합계·중복·판별 불가 처리와 공개 출력 비노출
- `--check` 실패 시 호출·파일 생성 0회; v2 target당 단일 호출과 strict load·동일 ID paired 비교
- 관련 테스트를 먼저 실행하고 마지막에 `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m compileall -q backend tests`, `git diff --check` 실행

## 제외 범위·완료 보고

- 실제 CNU 자료·DB, PostgreSQL, base instruct 모델 설치·비교, 반복 prompt 탐색, LoRA 학습, legacy 경로 변경은 제외한다. 로컬 모델이 없거나 서버가 응답하지 않으면 v2 live 비교를 성공으로 표시하지 말고 합성 테스트 결과와 환경 미준비 code만 보고한다.
- Gold 40건은 `NEEDS_HUMAN_REVIEW`를 유지한다. 이번 v1/v2 결과는 같은 미승인 개발 세트의 잠정 비교이며 Phase 5의 모델 평가 체크박스를 확정 완료로 바꾸지 않는다. E3/L5·L1/L3 팀 결정을 지금 요청하지 않는다.
- 실제 완료 범위만 `docs/schema-adaptive-text-to-sql-plan.md`의 본문·체크박스·변경 이력, 필요한 ADR, `docs/next-session-handoff.md`에 반영한다. 변경 파일·테스트·실제 Ollama 호출 여부·paired 잠정 결과·한계·다음 작은 작업을 보고해줘. 공개 가능한 코드·문서만 Conventional Commit으로 커밋하고 push는 하지 마.
