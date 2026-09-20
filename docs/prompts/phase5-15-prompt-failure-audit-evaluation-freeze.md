# 다음 개발 프롬프트: Phase 5-15 v2 실패 감사·비교 계약 고정

Phase 5-14의 `query-ir-v2` 악화 원인을 private 응답 비노출 상태로 점검하고, v1/v2 paired 비교가 서로 다른 fixture·모델·recording을 잘못 묶지 않도록 계약을 보강해줘. **새 prompt 버전이나 live 모델 호출은 만들지 않는다.** 같은 미승인 40건에 반복 적합하지 않도록 다음 독립 합성 평가의 분리 기준을 문서화한다.

## 먼저 확인

- `AGENTS.md`, `docs/next-session-handoff.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5·평가 설계·다음 시작점·ADR-023/024/025
- `docs/phase5-13-finetuned-synthetic-exploratory-results-2026-09-19.md`, `docs/phase5-14-versioned-queryir-prompt-trial-results-2026-09-19.md`, `docs/human-validation-checkpoints.md`의 L1/L3·E3/L5
- `backend/adaptive/evaluation/field_failure_analysis.py`, `paired_prompt_analysis.py`, `run_contracts.py`, `capture_targets.py`, `runner.py`와 관련 테스트; `backend/adaptive/harness/ollama.py`, `recorded.py`, `recording_bundle_storage.py`
- 시작 시 `git status --short`, 최근 Conventional Commit. Phase 5-13/14 private bundle·report가 없으면 공개 결과만으로 세부 원인을 추측하지 말고 미검증으로 표시한다.

## 목표

1. 기존 v1/v2 private bundle·상세 report를 **읽기 전용**으로 strict load한다. `UNKNOWN_FIELD`가 v1 21건에서 v2 72건으로 늘어난 이유를 고정된 범주·집계로 재확인한다. 예컨대 완전한 entity 접두사, 누락/변형된 접두사, ontology 밖 field, 물리 column 형태, 그 외를 서로 구분한다. 원 field 이름·모델 응답·질문·값은 stdout·공개 문서·기본 로그에 쓰지 않는다. 증거가 없는 인과 해석은 하지 않는다.
2. `paired_prompt_summary()`와 실패 분류 함수가 report·recording·target의 **같은 benchmark, seed, ontology, QueryIR 계약, fixture variant/mapping context, model tag, 질문 fingerprint, request ID 집합**을 비교하는지 점검한다. 부족한 검사가 있으면 기존 fingerprint와 strict-loaded bundle의 provenance를 재사용해 작은 입력 검증을 추가한다. report의 `model_label`만으로 model tag를 증명하지 않는다. 단순히 두 결과가 120건이라는 이유만으로 비교를 허용하지 않는다. 원문을 담지 않는 고정 error code로 거부한다. 공개 API 변경은 최소화한다.
3. v1 기본 prompt와 v2의 bytes/hash/전송 설정, 기존 private recording·report를 수정하지 않는다. `query-ir-v3` 생성, prompt 탐색, 응답 보정, 모델 weight·Modelfile 변경, 실제 Ollama 호출은 하지 않는다.
4. 별도 문서에 다음 평가용 **독립 합성 질문 세트**의 설계 기준을 고정한다. 기존 40건의 질문 문장·paraphrase family를 재사용하지 않고, 작성·검토·동결 순서, seed/ontology/fixture fingerprint, 모델·prompt version 고정, 사람 gold 검토 상태, 개발 세트와 독립 세트의 결과를 구분하는 방법을 정의한다. 같은 합성 스키마를 쓴다면 이는 질문 holdout이지 unseen school이 아님을 명시한다. 이번 단계에서 새 세트를 모델에 실행하거나 최종 성능을 발표하지 않는다.

## 최소 합성 테스트

- 정상 v1/v2 paired 비교와 기존 공개 집계의 회귀
- 다른 benchmark/seed, fixture metadata, mapping·question fingerprint, model tag, 중복·누락 request ID, variant context가 어긋난 합성 report·recording은 비교 전 거부
- v2 실패 분류에서 범주 합계·중복·혼합·판별 불가 건수가 일관되고 원문이 출력되지 않음
- private 파일 수정 0회, 네트워크·Ollama 호출 0회, SQL·행·응답·질문·경로 비노출
- 관련 테스트를 먼저 실행하고 마지막에 `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m compileall -q backend tests`, `git diff --check` 실행

## 경계·완료 보고

- 이 결과는 v2 실패 원인과 비교 무결성 점검이다. 같은 40건의 잠정 점수를 확정 성능으로 바꾸지 않는다. 기존 fine-tuned·동적 prompt 평가 체크박스는 사람 gold 검토 전 완료 처리하지 않는다.
- 현재 로컬에는 fine-tuned tag만 확인됐다. base instruct tag를 추측·다운로드·대체하지 않는다. E3/L5·L1/L3 팀 결정을 지금 요청하지 않고 gold 40건의 `NEEDS_HUMAN_REVIEW`를 유지한다.
- 실제 완료 범위만 `docs/schema-adaptive-text-to-sql-plan.md` 본문·체크박스·변경 이력, 필요 시 ADR, `docs/next-session-handoff.md`에 반영한다. 변경 파일·테스트·실제 모델 호출 여부·안전한 진단 집계·독립 평가 준비 상태·다음 작은 작업을 보고해줘. 공개 가능한 코드·문서만 Conventional Commit으로 커밋하고 push는 하지 마.
