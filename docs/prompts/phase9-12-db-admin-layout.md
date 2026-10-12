# Phase 9-12 지침: "DB 변경" 관리 화면을 보기 쉽게 다시 배치

## 배경 (2026-10-12 사용자 요청)

- 메인 화면 "DB 변경" 버튼이 여는 창(`src/pages/MainPage.tsx` 약 613행, `max-w-sm` 세로로 긴 dialog)에 DB 고르기, SQLite 등록, PostgreSQL 등록(`PostgresRegistrationPanel`), mapping 검토(`MappingReviewPanel`)와 그 안의 값 사전(`ValueDictionaryPanel`, `CodeTableImport`), 확장 항목(`ExtensionFieldPanel`), 시험 질의(`TestQueryPanel`), 버전 이력이 한 줄로 쌓여 있다. 챗봇처럼 좁고 길어서 무엇이 어디 있는지 알아보기 어렵다.
- 사용자 결정: 지금은 **가시성**이 목표다. 기존 스타일과 맞추는 것은 다음 단계에서 한다. 기능·API·검증 규칙은 바꾸지 않는다(배치·제목·안내 문구만).

## 바꿀 것

1. **창 크기와 구조**
   - dialog를 넓고 높게: 화면의 약 90%(예: `max-w-6xl w-[95vw] h-[90vh]`), 안쪽만 스크롤.
   - 위쪽에 탭(또는 왼쪽 메뉴) 셋: **사용할 DB** / **DB 등록** / **검토·게시**. 지금 어느 탭인지 분명히 보인다.
   - 화면 폭이 좁으면(약 768px 미만) 세로로 쌓인다.
2. **사용할 DB 탭**: 지금의 DB 목록 라디오, 취소·적용. 각 DB에 상태(게시됨 vN)를 배지로.
3. **DB 등록 탭**
   - 등록 양식(연결, Schema, Database ID, Label, 등록)을 한 줄 또는 2열로.
   - "등록된 PostgreSQL DB"를 표로: ID | 이름 | 연결/Schema | 상태 배지(검토 대기 / 게시됨 vN) | "검토 열기" 버튼. "검토 열기"는 검토·게시 탭으로 그 DB를 연다.
   - SQLite 등록은 범위 밖(ADR-089)이므로 이 탭 맨 아래 접힌 영역("기타: SQLite 파일 등록")으로 옮긴다. 기능은 그대로 둔다.
4. **검토·게시 탭** (DB 하나를 연 상태)
   - 맨 위: DB 이름·상태·현재 버전, 그리고 단계 표시 `① 항목 연결 확인 → ② 값 사전(선택) → ③ 확장 항목(선택) → ④ 게시`.
   - 본문 2열:
     - 왼쪽(넓게): **항목 연결 표**. 열: 공통 항목(한국어 이름과 key) | 연결된 칸(`table.column` 또는 "이 DB에 없음") | 상태 배지(확정/대기/없음) | 동작(후보 승인, 다른 칸으로 변경, 이 DB에 없음). 지금 `MappingReviewPanel`의 항목별 UI를 표 행으로 옮긴다.
     - 오른쪽: 값 사전, 확장 항목, 시험 질의, 버전 이력을 접고 펼 수 있는 상자(또는 하위 탭)로. 각 상자 제목 아래 한 줄 설명(무엇을 하는 곳인지, 꼭 해야 하는지).
   - 게시 버튼과 게시 막힘 이유 목록(9-07 `reasons`)은 아래쪽에 크게, 막힌 항목마다 한 줄.
5. **문구**: 처음 쓰는 사람이 읽을 수 있게 짧은 한국어로. 내부 용어는 괄호로만(예: "칸(column)", "공통 항목(field)"). 기존 오류·안내 문구의 뜻은 바꾸지 않는다.

## 바꾸지 않을 것

- 서버 API, 요청·응답 parser(`mappingReview.ts` 등 `*.ts` 계약), 검증·게시 규칙, 챗봇 화면, 레거시 기준선, adaptive-prototype 페이지.
- 기존 frontend 테스트가 확인하는 동작. 문구를 바꿔 테스트가 깨지면 테스트의 의도(동작)를 유지하는 범위에서만 기대 문구를 고친다.

## 확인

- `npx tsc -b`, `npm run lint`, `npx vite build`, 그리고 `tests/frontend/`의 관련 테스트(`phase6-19-mapping-review`, `phase8-24-dictionary-import`, `phase9-07-publish-block-reasons`, `phase9-11-published-review-contract` 등)를 `node --test`로.
- 서버를 띄우지 않는다(사용자 서버 8000·8123·5173도 건드리지 않는다). 화면 확인은 Claude가 따로 한다.
- 커밋하지 않는다. `backend/.env` 값 출력 없음.
- 문서: `docs/schema-adaptive-text-to-sql-plan.md` 로드맵에 9-12, 변경 이력에 2026-10-12 한 줄(사용자 요청, 배치만 바꿈, 스타일 맞추기는 다음 단계).
- 끝나면 바꾼 파일, 실행한 확인과 결과, 새 배치를 5줄 이내로 요약한다.
