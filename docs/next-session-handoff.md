# 새 Codex 대화용 짧은 인수인계

현재 기준: 2026-09-20, Phase 6-03 합성 사용자 확인 경계까지 구현·검증 완료. 최신 상태는 항상 Git과 코드로 다시 확인한다. 설계와 로드맵의 원본은 [`schema-adaptive-text-to-sql-plan.md`](schema-adaptive-text-to-sql-plan.md), 독립 평가 세트 설계 기준은 [`independent-evaluation-set-design.md`](independent-evaluation-set-design.md), 안전한 잠정 비교는 [`phase5-14-versioned-queryir-prompt-trial-results-2026-09-19.md`](phase5-14-versioned-queryir-prompt-trial-results-2026-09-19.md)다.

## 프로젝트와 현재 경계

**현재 개발 방침:** 팀의 사람 판단(E3/L5, L1/L3)은 지금 요청하거나 완료 처리하지 않는다. 합성 입력의 로컬 구현·테스트·private 검증을 계속한다. 실제 CNU 자료의 추가 사용·공유, 실제 데이터 배포·외부 시연, 모델 성능 수치 확정에 도달할 때 해당 관문을 적용한다. 개발 중 같은 결정을 반복해서 묻지 않는다.

- 기존 React/FastAPI + Ollama SQL 생성 + PostgreSQL 서비스는 legacy 기준선으로 보존한다. 새 경로는 `QueryIR`과 runtime ontology, Published Mapping, 읽기 전용 실행 하네스를 사용한다.
- Phase 5-07: Ollama `QueryModel` adapter와 합성 transport 테스트 완료. Phase 5-08: 로컬 `text2sql_qwen:latest`에 합성 질문 2건만 비저장 호출했고 둘 다 JSON object/현재 QueryIR 검증을 통과했다. 모델 정확도 증거는 아니다.
- Phase 5-09: 합성 provider의 capture→private bundle→strict load, 저장 전 preflight, synthetic-only CLI, Ollama 선언형 provenance hash 완료.
- Phase 5-10: `.schema-adaptive`의 versioned private target 파일을 읽는 별도 `backend.adaptive.harness.live_capture` CLI 구현. 명시적 `--live`와 전체 사전 검증, 선언 provenance, 기존 private bundle을 연결했다. 합성 transport 테스트만 실행했다.
- Phase 5-11: `--check`와 `--live`가 동일한 preflight 결과를 사용한다. `--check`는 호출·저장 0회이고 합성 테스트로 경계를 검증했다. E3·L5 미기입 양식은 `PENDING_HUMAN_REVIEW`다.
- Phase 5-12: 완전 합성 질문 2건을 로컬 Ollama에 `--live` 1회로 전송하고 응답 2건을 Git 비추적 private bundle에 저장했다. strict load·`RecordedModel` 재생과 JSON object·QueryIR 검증을 통과했다.
- Phase 5-13: 합성 benchmark v1 40×3 target을 결정적으로 생성해 정확한 로컬 fine-tuned tag로 120건을 한 번씩 수집했다. 잠정 집계는 구조 exact 51, 실행 성공 99, semantic 일치 90이다. Gold 40건은 모두 `NEEDS_HUMAN_REVIEW`이므로 확정 성능 아님.
- Phase 5-14: v1 `UNKNOWN_FIELD` 21건을 비노출 분류한 뒤 `query-ir-v2`를 같은 합성 40×3 세트에서 수집·비교했다. v2는 구조 exact 0, 실행 성공 42, semantic 일치 36, `UNKNOWN_FIELD` 72로 악화. v1을 기본값으로 유지한다.
- Phase 5-15: paired 비교 무결성 계약, 합성 테스트, 읽기 전용 strict-load 재검증(v2 `UNKNOWN_FIELD` 72건 범주 재집계) 완료. 미승인 합성 개발 세트의 진단 집계이며 확정 성능 아님. 독립 합성 평가 세트 설계 기준 문서 작성. gold 40건 `NEEDS_HUMAN_REVIEW` 유지. 새 prompt 탐색·live 호출·확정 성능 발표 없음.
- Phase 5-16: 독립 합성 평가 세트 30건 초안(P2-041~070) 작성·자동 검증 완료, 사람 검토·동결 전. 정식 30~50건 조건 통과, 3개 fixture에서 gold IR 90회 실행이 기대 결과와 일치(자기 일관성, 모델 점수 아님). 개발 세트와 격리 검증 완료. 기존 40건과 새 30건 모두 `NEEDS_HUMAN_REVIEW` 유지. Ollama 호출·성능 평가·승인 없음.
- Phase 5-17: 독립 세트 문항 정리(P2-057·P2-059, 격리 유지)·단독 커버리지(30건·15개 field·결과 0/1/여러 건·JOIN 0~3단계)·FakeModel oracle smoke 90건 전건 일치(모델 점수 아님) 완료. 제품 코드 변경 없음. 새 30건은 사람 검토·동결 전 question holdout 초안 유지. Ollama 호출·성능 평가·승인 없음.
- Phase 6-01: 합성 입력의 제한된 QueryIR repair 경계(`BoundedRepairCoordinator`, 최초 1회 + repair 1회) 구현·합성 검증 완료. 재시도 허용 code/stage 고정, provider에는 고정 code만 전달, 후보 전체 안전 경로 재통과. request·ontology·mapping은 깊은 복사 스냅샷으로 고정하고, 각 모델은 격리 래퍼를 거쳐 harness 검증 컨텍스트와 분리된 복사본만 받아 모델 변이가 검증·컴파일·실행 컨텍스트에 영향을 주지 않는다. 사용자 확인·관리자 검토 큐는 미구현. 성능·정확도 수치 없음. 새 30건과 기존 40건 `NEEDS_HUMAN_REVIEW` 유지.
- Phase 6-02: 합성 `RepairSummary` 전용 결정적 critique 분류(`critique-disposition-v1`) 구현·합성 검증 완료. 성공은 `EXECUTED_UNVERIFIED`+`repair_attempted`·`recovered_after_repair` 구분, 실패는 고정 (code, stage) 6개 disposition 분류, 위조·미지·모순은 `CRITIQUE_INVALID_SUMMARY`로 거부. repair 없으면 최종=초기, repair 표시는 Phase 6-01 재시도 허용 쌍에서만 수락. 원문·질문·SQL·행·경로 비노출, 호출 0회. 전체 loop·모호 질문 확인·관리자 검토 큐는 미구현. 성능·정확도 수치 없음.
- Phase 6-03: 합성 모호성 신호·사용자 확인 경계(`clarification-gate-v1`) 구현·합성 검증 완료. caller 제공 명시적 신호만 pending에 묶고(2개 이상 서로 다른 option ID·semantic 식별자), 신호 없으면 pending·차단 없음. `resolve`는 caller 원본 신호를 재요구해 정렬 option ID·semantic_ref 전체 일치를 확인(일관성 확인이며 암호학적 진위 증명 아님). 정확한 한 선택만 `CONFIRMED`, 명시적 취소는 별도 `CANCELLED`. pending·결과에는 원시 식별자를 저장하지 않고 domain-separated fingerprint만 저장. 전 공개 경계는 dump-then-validate 재검증으로 위조 인스턴스를 고정 code로 거부. 원문·물리·SQL·행·경로·secret 비노출, 모델·DB·네트워크 호출 0회. 자동 감지·QueryIR 보정·하네스 자동 실행·UI·관리자 큐는 미구현. `모호한 질문 감지 및 사용자 확인` 전체 항목은 미완료 유지. 성능·정확도 수치 없음. 실제 CNU 자료·private bundle 미사용.
- 현재 로컬에는 fine-tuned tag만 확인되어 base instruct 평가는 정확한 tag가 별도로 준비될 때 진행한다. E3·L5 팀 결정 대기와 합성 개발은 병행한다.
- `docs/desktop-test-handoff.md`는 초기 데스크톱 이전 시점의 기록이다.

## 다음 작은 작업

1. 새 30건 초안의 사람 검토(L1·E2)·동결
2. Phase 6 전체 loop·관리자 검토 큐 설계(명시적 확인 경계는 합성 완료, 자동 감지·실제 상호작용은 미구현)
3. base instruct 모델 tag가 준비되면 base instruct 평가
4. E3/L5·L1/L3 팀 결정이 필요한 실제 사용·공유·성능 확정 관문 도달 시 적용

## 새 대화의 최소 시작 순서

1. 저장소 루트에서 `AGENTS.md`와 이 문서, 계획 문서의 **Phase 5·6 현재 체크포인트·ADR-023/024/025/027/028/029/030·다음 시작점**, `docs/human-validation-checkpoints.md`의 E3/L1/L3/L5를 읽는다.
2. `git status --short`, 최근 commit, 관련 코드·테스트를 확인한다. 문서는 방향을 알려주지만 구현 상태와 테스트 통과를 대신하지 않는다.
3. 현재 작업에 필요한 파일만 더 읽고 해당 범위의 테스트부터 실행한다. 이전 대화 전체나 저장소 전체를 다시 요약할 필요는 없다.

실제 질문·응답, DB 행·dump, 모델 weight, 비밀값, private recording은 Git이나 공개 문서에 복사하지 않는다.
