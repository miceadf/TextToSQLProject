# Phase 9-22 지침: 관리 창 스타일 정리와 검토자 기억

## 배경 (2026-10-13 사용자 요청)

1. 값 사전의 [가져오기]·[사전 적용해 게시]가 눌리지 않았다. 원인: 검토자 이름이 비어 있었다. 9-14 이후 검토자는 "검토·게시" 탭 맨 위 한 곳(`MappingReviewPanel`의 `reviewer` state)에서만 입력하는데, 새로고침하면 비고, 값 사전 상자 안에는 "먼저 검토자 이름을 넣어 주세요" 문구만 있어 어디에 넣는지 모른다.
2. "DB 변경" 관리 창(9-12에서 배치만 바꾸고 스타일 통일은 미룸)의 버튼 대부분이 외곽선·배경이 없어 글자와 구분되지 않는다. 이제 스타일을 정리한다.

## 고칠 것 (화면만, API·동작·검증 규칙 변경 없음)

### A. 검토자
- 검토자 이름을 브라우저에 기억한다(`localStorage`, 키 하나, 앱 공통). 읽기·쓰기는 모두 try/catch로 감싸고 실패해도 화면이 정상 동작해야 한다(사생활 창·차단 환경). 처음 열 때 기억된 값으로 채운다.
- 검토자가 비어 있을 때 하위 상자(값 사전·확장 항목·시험 질의·게시)의 안내 문구를 "맨 위 ‘검토자’ 칸에 이름을 넣어 주세요"로 바꾸고, 문구를 누르면 그 입력칸으로 스크롤·focus한다.
- 비활성 버튼에는 왜 비활성인지 `title`(마우스 올림 설명)을 단다(검토자 없음 / 저장하지 않은 편집 있음 / 출처 미선택 등 실제 조건대로).

### B. 스타일 통일 (메인 화면의 기존 Tailwind 스타일에 맞춘다)
- 공용 class 상수 파일(예: `src/adaptive-prototype/adminStyles.ts`)을 만들고 관리 창 안의 모든 컴포넌트에서 쓴다: `MainPage.tsx`의 DB 창, `PostgresRegistrationPanel`, `MappingReviewPanel`, `MappingReviewRow`, `ValueDictionaryPanel`, `CodeTableImport`, `ExtensionFieldPanel`, `TestQueryPanel`, `SchemaComparePanel`, SQLite 등록 영역.
  - 기본 버튼(주요 동작: 등록, 검토 시작, 게시, 사전 적용해 게시, 저장): 메인 화면 "적용" 버튼과 같은 파란 채움(`bg-blue-600 text-white hover:bg-blue-700 rounded-lg h-9 px-3 text-sm font-semibold`).
  - 보조 버튼(검토 열기, 다른 칸으로, 다시 정하기, 가져오기, 추가, 새로고침, 취소 등): 메인 "취소"와 같은 흰 바탕+외곽선(`border border-slate-200 bg-white text-slate-700 hover:bg-slate-50`).
  - 위험 버튼(삭제, 이 DB에 없음): 빨간 외곽선(`border-red-200 text-red-700 hover:bg-red-50`), 확인 단계의 최종 삭제는 빨간 채움.
  - 비활성: 흐리게(`disabled:opacity-50 disabled:cursor-not-allowed`), 눌림 표시 없음.
  - 입력·선택(input/select/textarea): 메인 검색창 계열(`h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm focus:border-blue-400 focus:ring-4 focus:ring-blue-100`).
  - 상자(section/fieldset): 흰 바탕, `rounded-xl border border-slate-200 p-4`, 제목 `text-sm font-semibold text-slate-900`, 설명 `text-xs text-slate-500`.
  - 상태 배지: 확정=초록, 대기=노랑, 없음=회색, 게시됨=파랑, 검토 대기=노랑 계열의 연한 배경 배지.
  - 안내·오류·성공 메시지: 기존 amber/red/emerald 연한 상자 스타일로 통일.
- 표(등록 목록, 항목 연결 표): 머리글 연한 회색 배경, 줄 구분선, 버튼은 한 줄로 정렬(좁으면 줄바꿈).
- 글자·문구는 바꾸지 않는다(A의 안내 문구 제외).

## 확인

- 관련 frontend 테스트(동작 의도가 같으면 class 문자열 기대만 고친다), `npx tsc -b`, 관련 파일 eslint.
- 서버를 띄우지 않는다. 화면 확인은 Claude가 한다.

## 지킬 것

- 서버·사용자 데이터 안 건드림, live 모델 호출 없음, 커밋 없음, `backend/.env` 값 출력 없음. UTF-8로 쓴다.
- 문서: `docs/schema-adaptive-text-to-sql-plan.md` 로드맵 9-22, 변경 이력 2026-10-13 한 줄(사용자 요청: 스타일 통일·검토자 기억).
- 끝나면 바꾼 파일과 확인 결과를 짧게 적는다.
