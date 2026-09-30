# Phase 8-14 지침: 챗봇 실행 SQL 표시(개발 옵션)

작성일: 2026-09-30.

사용자 요청: 레거시 챗봇처럼 QueryIR 경로도 실제 SQL을 보여 달라. 그래야 잘 작동하는지 끝까지 확인할 수 있다.

## 1. 결정

- 지금까지는 "브라우저에 SQL·물리 이름을 보내지 않는다"는 원칙이었다. 이번에 사용자 결정으로 **서버 개발 옵션**을 켰을 때만 성공한 챗봇 답에 실행 SQL을 붙인다.
- 기본값:
  - `synthetic_prototype`는 끔. `--show-sql`로 켠다.
  - `npm run dev:all` launcher는 개발용이므로 켬. `backend/.env`의 `SHOW_SQL=0`으로 끈다. 상태 줄에 `chat_sql=shown|hidden`을 표시한다.

## 2. 무엇을 보여 주나

- executor에 실제로 들어간 compile 결과다. 모델 출력이 아니라, 검증·policy를 통과해 실행된 것이다.
- 읽기 쉽게 bound 값을 SQL literal로 채워 넣은 **보기용 문장**이다.
  - 문자열은 `'...'`로 감싸고 작은따옴표는 두 번 쓴다.
  - 숫자와 `TRUE`/`FALSE`는 그대로 쓴다. `%%`는 `%`로 바꾼다.
- 실제 DB에는 여전히 문장과 값을 따로 보낸다(parameter binding). 화면 표시만 합친 것이다.
- `UNSUPPORTED`·`NOT_UNDERSTOOD`·오류 응답에는 SQL을 넣지 않는다.
- 보기용 문장을 만들다 실패하면 SQL만 빼고 답은 그대로 준다. 길이는 최대 20,000자다.

## 3. 화면

- 기존 레거시용 SQL 패널을 같이 쓴다.
- QueryIR 경로에서는 제목을 "Executed SQL (QueryIR · 값은 보기용으로 채워 넣음)"으로 표시한다.
- frontend parser는 성공 응답에서만 선택 key `sql`(비어 있지 않은 문자열, 20,000자 이하)을 받는다.

## 4. 테스트

- 보기용 문장: literal 규칙, 매개변수 개수 불일치는 거부.
- 옵션을 껐을 때는 응답이 이전과 같다. 켰을 때는 `sql` 외에는 같다.
- 실행하지 않은 답에는 SQL이 없다.
- launcher 기본값과 `SHOW_SQL=0`, CLI `--show-sql`.
- frontend parser 허용·거부.

## 5. 영향

- 옵션을 켜면 브라우저에 물리 table·column 이름과 값이 보인다.
- 외부 시연이나 실제 자료 사용 전에는 끄거나 L5/E3 판단이 필요하다(기록만 하고 막지 않는다).
