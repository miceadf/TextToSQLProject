# Phase 6-16 구현 지침: 합성 활성 DB 선택과 메인 목록·질의 동기화

## 목표

메인 수강신청 화면에서 사용 DB를 선택하고, 선택한 합성 DB가 강의 목록과 `/adaptive-prototype` clarification 질의에 동일하게 적용되는 하나의 기능 흐름을 완성한다.

이번 단계는 **미리 등록된 합성 DB 선택**까지만 구현한다. 파일 업로드·임의 경로·PostgreSQL 등록·실제 CNU 데이터·Ollama live 호출·인증·전역 current DB·관리자 mapping publish UI는 구현하지 않는다.

## 필수 계약

1. 기존 legacy `/api/v1/courses`와 기존 로그인·수강신청 화면 동작을 제거하거나 교체하지 않는다. 기본 선택은 legacy 기준선이다.
2. 합성 prototype backend는 최소 두 개의 서버 소유 등록 DB를 제공한다.
   - 동일 `DEFAULT_SEED`를 사용한 `flat` schema
   - 동일 `DEFAULT_SEED`를 사용한 `legacy` schema
   - 서로 다른 물리 테이블·컬럼과 각 DB의 Published Mapping을 사용한다.
3. backend에 strict JSON 응답의 합성 DB 목록 endpoint와 canonical course catalog endpoint를 추가한다.
   - caller가 파일 경로·mapping·SQL·물리 식별자를 주입할 수 없어야 한다.
   - catalog는 seed를 그대로 반환하지 말고, 선택 DB를 기존 read-only executor와 Published Mapping/안전한 compiler 경로로 조회한 결과를 canonical course DTO로 변환한다.
   - unknown DB, mapping/schema drift, 실행 실패는 고정 code의 redacted 오류로 닫는다.
   - 두 schema의 canonical course 결과가 같음을 테스트한다.
4. 기존 clarification start는 request의 `database_id`를 서버 registry에서 해석한다. session token은 시작 DB·context fingerprint에 계속 묶여야 하며 submit에서 다른 DB로 바꿀 API를 만들지 않는다.
5. frontend에 strict parser/client를 둔다. 응답의 version, 허용 key, DB ID 형식, canonical course 필드·타입·row 수를 runtime 검증한다. raw server body나 내부 식별자·경로·SQL을 UI 오류에 반사하지 않는다.
6. 메인 화면 header에 현재 DB와 `DB 변경` 버튼을 추가한다.
   - modal/list에서 legacy 기준선 또는 등록 합성 DB를 선택하고 명시적으로 적용한다.
   - legacy 선택은 기존 `coursesApi.getCourses()`를 그대로 쓴다.
   - 합성 선택은 adaptive catalog endpoint를 쓰며 검색 keyword도 선택 DB 요청에 포함한다.
   - loading/error/retry를 표시하고 실패를 빈 목록으로 위장하지 않는다.
   - DB 변경 시 선택 강의·경고·hover 등 이전 DB에 종속된 UI 상태를 초기화한다.
7. 활성 선택은 서버 전역 mutable state가 아니라 browser `sessionStorage`에 저장한다. 값은 registry 응답으로 재검증하고, 손상·미등록 값은 legacy로 fail closed한다. localStorage·cookie·backend global current DB를 쓰지 않는다.
8. `/adaptive-prototype` 진입 링크 또는 버튼은 선택한 합성 DB ID를 명시적으로 전달한다. adaptive page는 같은 검증된 선택을 표시하고 `startClarification()`에 전달한다. legacy가 선택된 경우 합성 질의 지원 없음이 분명해야 한다.
9. 기존 fixed `DATABASE_ID` 의존을 제거하되 기존 Phase 6-12~15 응답·token·redaction 계약은 유지한다.
10. prototype app 종료 시 두 임시 DB 모두 정리한다. import side effect, 외부 network, `.env`, PostgreSQL, Ollama 접근을 추가하지 않는다.

## 구현 품질

- registry/context는 방어적 복사 또는 immutable view로 노출한다.
- DB ID와 label은 제한된 길이·문자 집합으로 검증한다.
- SQL identifier는 caller 입력으로 조립하지 않는다.
- canonical DTO 변환은 한 곳에 모으고 frontend legacy `Course` 타입으로 매핑 가능한 명확한 계약을 둔다.
- React async 요청은 abort/stale response 방지를 유지한다.
- 새 코드가 기존 legacy API client의 auth/base URL 계약을 오염시키지 않게 별도 adaptive client를 쓴다.

## 테스트 범위

구현 중 관련 테스트만 실행한다.

- backend: registry 목록, 두 DB catalog 동등성, keyword, unknown DB, drift/failure redaction, clarification 두 DB 분기, cleanup
- frontend: strict parser/client, sessionStorage fail-closed, DB 적용·상태 초기화에 필요한 순수 state/helper, dynamic clarification database ID
- 기존 Phase 6-13~15 backend/frontend 관련 회귀
- 변경 frontend 파일 ESLint와 frontend build

전체 pytest, 전체 compileall, 전체 `git diff --check`, 문서 계획/ADR/handoff 수정, commit, push는 하지 않는다. 마지막에는 긴 diff나 전체 로그 없이 변경 파일, 핵심 계약, 실패 원인, 실행한 테스트와 결과, 남은 위험만 한국어로 요약한다.
