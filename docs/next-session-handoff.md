# 새 Codex 대화용 짧은 인수인계

현재 기준: 2026-09-19, Phase 5-10 완료. 최신 상태는 항상 Git과 코드로 다시 확인한다. 설계와 로드맵의 원본은 [`schema-adaptive-text-to-sql-plan.md`](schema-adaptive-text-to-sql-plan.md), 완료 범위는 [`prompts/phase5-10-live-capture-boundary.md`](prompts/phase5-10-live-capture-boundary.md)다.

## 프로젝트와 현재 경계

- 기존 React/FastAPI + Ollama SQL 생성 + PostgreSQL 서비스는 legacy 기준선으로 보존한다. 새 경로는 `QueryIR`과 runtime ontology, Published Mapping, 읽기 전용 실행 하네스를 사용한다.
- Phase 5-07: Ollama `QueryModel` adapter와 합성 transport 테스트 완료. Phase 5-08: 로컬 `text2sql_qwen:latest`에 합성 질문 2건만 비저장 호출했고 둘 다 JSON object/현재 QueryIR 검증을 통과했다. 모델 정확도 증거는 아니다.
- Phase 5-09: 합성 provider의 capture→private bundle→strict load, 저장 전 preflight, synthetic-only CLI, Ollama 선언형 provenance hash 완료.
- Phase 5-10: `.schema-adaptive`의 versioned private target 파일을 읽는 별도 `backend.adaptive.harness.live_capture` CLI 구현. 명시적 `--live`와 전체 사전 검증, 선언 provenance, 기존 private bundle을 연결했다. 합성 transport 테스트만 실행했다. **실제 Ollama 응답 recording은 아직 없다.**
- 다음 작업은 실제 live 실행 전 E3·L5의 데이터 사용·접근·보관·반출·삭제·recording 노출 경로를 사람과 문서로 확인하는 것이다. 모델 성능 확정 전 L1·L3 확인이 필요하고, gold 40건은 모두 `NEEDS_HUMAN_REVIEW`다.
- `docs/desktop-test-handoff.md`는 초기 데스크톱 이전 시점의 기록이다. 그 안의 “adapter 없음” 및 Phase 5-06 상태는 현재 상태가 아니다.

## 새 대화의 최소 시작 순서

1. 저장소 루트에서 `AGENTS.md`와 이 문서, 계획 문서의 **Phase 5 현재 체크포인트·ADR-023/024/025·다음 시작점**, `docs/human-validation-checkpoints.md`의 E3/L1/L3/L5를 읽는다.
2. `git status --short`, 최근 commit, 관련 코드·테스트를 확인한다. 문서는 방향을 알려주지만 구현 상태와 테스트 통과를 대신하지 않는다.
3. 현재 작업에 필요한 파일만 더 읽고 해당 범위의 테스트부터 실행한다. 이전 대화 전체나 저장소 전체를 다시 요약할 필요는 없다.

실제 질문·응답, DB 행·dump, 모델 weight, 비밀값, private recording은 Git이나 공개 문서에 복사하지 않는다.
