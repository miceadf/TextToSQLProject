# Phase 6-15 구현 지침: 합성 prototype browser smoke와 팀 공유 handoff

## 목표

Phase 6-14의 `/adaptive-prototype` 화면과 Phase 6-13 loopback ASGI backend를 실제로 함께 실행할 수 있는 팀 공유 단위로 정리한다. 이 단계는 합성 fixture 전용 내부 prototype의 재현성과 경계를 닫는 작업이다. legacy 시스템 교체, 실제 서비스 연결, 외부 시연 승인을 뜻하지 않는다.

## 작업 범위

1. 현재 구현과 실행 script를 필요한 범위만 확인한다.
2. `docs/synthetic-clarification-team-handoff.md`를 새로 작성한다.
   - 사전 조건
   - 단일 실행 명령과 접속 URL
   - 종료 방법
   - 브라우저 smoke 시나리오 표
   - 합성 전용·loopback-only·무인증·비영속 session 등 경계와 알려진 한계
   - 실제 browser 관찰 전 상태는 `PENDING_BROWSER_SMOKE`로 적는다.
3. 기존 `docs/synthetic-clarification-prototype.md`에서 팀 handoff 문서를 연결한다.
4. 팀원이 재현하는 데 막히는 명백한 script 또는 문서 결함만 최소 수정한다. 새 제품 기능이나 운영 연결은 추가하지 않는다.

## 브라우저 smoke 시나리오

실제 통과 여부는 Codex가 browser를 직접 조작한 뒤 기록한다. OpenCode는 통과했다고 추정하지 않는다.

- `강의 목록 보여줘` 시작 시 두 option 표시
- `강의명 기준` 선택 후 합성 결과 표 표시
- 새 흐름에서 `교수자명 기준` 선택 후 합성 결과 표 표시
- 명시적 취소가 `CANCELLED`로 종료
- trigger가 없는 질문이 `NOT_REQUIRED`로 종료
- backend 연결 실패가 안전한 일반 오류로 표시되고 token, SQL, 경로, 원시 응답을 노출하지 않음
- terminal 상태에서 UI가 같은 session 재제출을 허용하지 않음; backend token at-most-once 계약은 기존 자동 테스트 증거를 함께 명시
- `/` legacy 경로가 기존 화면을 유지

## 금지 범위

- legacy app 동작·API·인증 변경 금지
- 실제 CNU 데이터, `.env`, PostgreSQL, Ollama, private recording 접근 금지
- 외부 bind, 배포, 계정·권한·rate limit 구현 금지
- 계획 문서, ADR, 체크리스트, 변경 이력, `docs/next-session-handoff.md` 수정 금지
- browser를 실제로 조작했다고 주장 금지
- 전체 pytest, compileall, commit, push 금지

## 검증

변경과 직접 관련된 최소 검증만 실행한다.

- adaptive prototype Node 테스트
- 변경한 frontend 파일에 대한 ESLint(해당할 때만)
- frontend production build
- synthetic prototype backend 관련 pytest

전체 로그나 긴 diff는 출력하지 않는다. 마지막 응답은 다음만 한국어로 간결히 보고한다.

- 변경 파일
- 공유 계약과 안전 경계
- 실패 원인(있을 때)
- 테스트 요약
- 실제 browser smoke에 남긴 항목과 위험
