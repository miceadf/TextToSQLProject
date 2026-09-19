# 새 Codex 대화용 짧은 인수인계

현재 기준: 2026-09-19, Phase 5-13 합성 탐색 평가 완료. 최신 상태는 항상 Git과 코드로 다시 확인한다. 설계와 로드맵의 원본은 [`schema-adaptive-text-to-sql-plan.md`](schema-adaptive-text-to-sql-plan.md), 안전한 잠정 결과는 [`phase5-13-finetuned-synthetic-exploratory-results-2026-09-19.md`](phase5-13-finetuned-synthetic-exploratory-results-2026-09-19.md)다.

## 프로젝트와 현재 경계

**현재 개발 방침:** 팀의 사람 판단(E3/L5, L1/L3)은 지금 요청하거나 완료 처리하지 않는다. 합성 입력의 로컬 구현·테스트·private 검증을 계속한다. 실제 CNU 자료의 추가 사용·공유, 실제 데이터 배포·외부 시연, 모델 성능 수치 확정에 도달할 때 해당 관문을 적용한다. 개발 중 같은 결정을 반복해서 묻지 않는다.

- 기존 React/FastAPI + Ollama SQL 생성 + PostgreSQL 서비스는 legacy 기준선으로 보존한다. 새 경로는 `QueryIR`과 runtime ontology, Published Mapping, 읽기 전용 실행 하네스를 사용한다.
- Phase 5-07: Ollama `QueryModel` adapter와 합성 transport 테스트 완료. Phase 5-08: 로컬 `text2sql_qwen:latest`에 합성 질문 2건만 비저장 호출했고 둘 다 JSON object/현재 QueryIR 검증을 통과했다. 모델 정확도 증거는 아니다.
- Phase 5-09: 합성 provider의 capture→private bundle→strict load, 저장 전 preflight, synthetic-only CLI, Ollama 선언형 provenance hash 완료.
- Phase 5-10: `.schema-adaptive`의 versioned private target 파일을 읽는 별도 `backend.adaptive.harness.live_capture` CLI 구현. 명시적 `--live`와 전체 사전 검증, 선언 provenance, 기존 private bundle을 연결했다. 합성 transport 테스트만 실행했다. **실제 Ollama 응답 recording은 아직 없다.**
- Phase 5-11: `--check`와 `--live`가 동일한 preflight 결과를 사용한다. `--check`는 호출·저장 0회이고 합성 테스트로 경계를 검증했다. E3·L5 미기입 양식은 `PENDING_HUMAN_REVIEW`다. **실제 Ollama 응답 recording은 아직 없다.**
- Phase 5-12: 완전 합성 질문 2건을 로컬 Ollama에 `--live` 1회로 전송하고 응답 2건을 Git 비추적 private bundle에 저장했다. strict load·`RecordedModel` 재생과 JSON object·QueryIR 검증을 통과했다. 실제 CNU 응답 recording과 모델 성능 평가는 없다.
- Phase 5-13: 합성 benchmark v1 40×3 target을 결정적으로 생성해 정확한 로컬 fine-tuned tag로 120건을 한 번씩 수집했다. strict load와 `run-recorded` 120 attempt 오프라인 재생 완료. 잠정 집계는 구조 exact 51, 실행 성공 99, semantic 일치 90이다. Gold 40건은 모두 `NEEDS_HUMAN_REVIEW`이므로 확정 성능 아님. Private target·bundle·상세 report는 Git 비추적 상태다.
- 다음 작업은 잠정 실패 유형의 private 응답 비노출 분석과 QueryIR field 호환성 점검이다. E3·L5 팀 결정 대기와 합성 개발은 병행한다. 실제 CNU 자료 사용·공유 및 실제 데이터 배포·외부 시연에는 해당 결정을 먼저 확인한다. 모델 성능 확정 전 L1·L3 확인이 필요하다.
- `docs/desktop-test-handoff.md`는 초기 데스크톱 이전 시점의 기록이다. 그 안의 “adapter 없음” 및 Phase 5-06 상태는 현재 상태가 아니다.

## 새 대화의 최소 시작 순서

1. 저장소 루트에서 `AGENTS.md`와 이 문서, 계획 문서의 **Phase 5 현재 체크포인트·ADR-023/024/025·다음 시작점**, `docs/human-validation-checkpoints.md`의 E3/L1/L3/L5를 읽는다.
2. `git status --short`, 최근 commit, 관련 코드·테스트를 확인한다. 문서는 방향을 알려주지만 구현 상태와 테스트 통과를 대신하지 않는다.
3. 현재 작업에 필요한 파일만 더 읽고 해당 범위의 테스트부터 실행한다. 이전 대화 전체나 저장소 전체를 다시 요약할 필요는 없다.

실제 질문·응답, DB 행·dump, 모델 weight, 비밀값, private recording은 Git이나 공개 문서에 복사하지 않는다.
