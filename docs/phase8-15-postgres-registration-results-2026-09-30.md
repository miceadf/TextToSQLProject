# Phase 8-15 결과: 화면에서 PostgreSQL 등록 (2026-09-30)

## 1. 무엇이 되나

"DB 변경" 창의 **PostgreSQL 등록** 영역에서 등록부터 사용까지 한 번에 진행된다.
1. 서버에 설정된 연결과 schema를 고른다.
2. ID와 이름을 넣고 등록하면 SQLite와 같은 검토 화면이 뜬다.
3. 검토 후 게시하면 서버를 재시작하지 않아도 DB 목록과 챗봇(QueryIR, 값 힌트, SQL 표시)에 바로 추가된다.

**연결 목록**
- 연결은 `backend/.env`에서 온다.
  - `DATABASE_URL`은 "기본 PostgreSQL 서버"가 된다.
  - `PG_SOURCE_<이름>=<DSN>`을 넣으면 연결이 추가된다.
- 브라우저에는 연결 ID와 이름만 간다. 저장 파일에는 DSN 대신 환경변수 이름만 남는다.

**등록**
- 읽기 전용으로 구조를 읽는다.
- 규칙 제안에 더해, 모델이 켜져 있으면 8-12 AI 제안을 만든다. 모델에 줄 저장 값 예시는 메모리에서만 쓴다.

**검토**
- 동작은 승인, 다른 column 지정, 새로 추가한 **"이 DB에 없음"(IGNORE)**이다.
- PostgreSQL은 없는 정보를 빼고 게시할 수 있다(미지원 field).
- SQLite는 지금처럼 field 15개가 모두 있어야 게시된다.

**게시할 때 서버가 정하는 것** (고정 규칙)
- 변환: field·column type과 저장 값 예시가 맞는 변환이 하나일 때만 붙인다.
  - `3/3/0` 형식 → `SLASH_FIRST_NUMERIC`
  - `time` column → `TIME_TO_HHMM`
  - 한 글자 요일 → `KOREAN_WEEKDAY`
- 기준 table: 분반이 있는 table이다.
- 검증: 실제 연결로 기존 runtime 검증을 통과한 profile만 쓴다. 파일은 서버 재시작 때 다시 읽는 형식으로 저장한다.

**재시작**
- dev launcher가 게시된 등록을 찾아 연결 DSN을 넣고 다시 붙인다.
- 연결 설정이 사라졌으면 해당 DB는 `SOURCE_MISSING`으로 꺼진다.

## 2. 확인

- **실제 PostgreSQL smoke** (합성 DB에 임시 schema를 만들고, 끝나면 schema와 private 파일 삭제):

  | 변형 | 제안 | 게시 | 자동 결정 | 챗봇 |
  |---|---|---|---|---|
  | `legacy-like` | AI | 성공(학기 1개 무시) | 학점 `SLASH_FIRST_NUMERIC`, 시간 `TIME_TO_HHMM`, 기준 `cnu_courses` | "3학점 과목" 9건, SQL 확인 |
  | `credits-slash` | AI | 성공(15개 모두) | 학점 `SLASH_FIRST_NUMERIC`, 요일 `KOREAN_WEEKDAY`, 기준 `course_offerings` | "3학점 과목" 9건 |

- **브라우저** (새 코드를 8124·5174 포트에 따로 띄움. 사용자 서버는 건드리지 않음):
  - "DB 변경" 창에 연결 "기본 PostgreSQL 서버 (DATABASE_URL)"과 schema 목록이 나왔다.
  - 레거시 `public` schema를 등록하니 table 8개·column 38개, 제안은 "AI 제안"으로 나왔고 검토 시작 버튼까지 확인했다. 확인 뒤 등록 파일은 지웠다.
  - 확인 중 "DB 변경" 창에 높이 제한·스크롤이 없어 아래 입력칸을 누를 수 없는 문제를 찾아 고쳤다.

## 3. 리뷰에서 고친 점

`gpt-6-luna` high review-only 지적 4건을 재현해 고쳤다.
1. 게시한 DB가 "합성 질의(clarification)" 경로에서는 보이지 않았다. 살아 있는 집합으로 바꾸기 전에 loader를 만든 탓이다.
2. 실행 중 게시로 DB 수 상한(고정 + PostgreSQL 8개)을 넘길 수 있었다. 이제 409로 거부한다.
3. profile 두 파일 중 하나만 바뀐 채 실패할 수 있었다. 이제 함께 바뀌거나 이전 상태로 돌아간다.
4. DB 목록을 읽는 도중 게시가 일어날 수 있었다. 이제 한 번 복사한 목록으로 응답한다.

## 4. 한계와 다음 단계 (8-07)

- 값 사전(코드표·약어)은 아직 입력할 수 없다. 영어 요일 등 코드값 column은 게시돼도 조건이 맞지 않는다(smoke에서 "월요일 수업" 0건).
- 버전 되돌리기, test query 미리보기, 회귀 비교는 PostgreSQL 등록에 아직 없다.
- view는 등록하지 않는다(table만).
- 자동 승인이 아니므로 검토자가 이름 함정(예: `course_year`)을 걸러야 한다.
