# Phase 7-14 구현 지침: ChatPopup의 DB별 경로 선택과 dev:all 연결

## 배경

7-13에서 prototype 서버에 QueryIR 챗봇 endpoint(`POST /api/adaptive/v1/chat/query`)를 만들었다. 7-14는 팀원이 기존 화면에서 이 경로를 바로 써 볼 수 있게 두 가지를 연결한다.

- `DB 변경`에서 고른 DB에 따라 AI Course Assistant가 알맞은 경로로 질문한다.
- `npm run dev:all` 하나로 legacy DB와 로컬 Ollama를 쓰는 QueryIR 경로까지 켜진다.

## 범위

### 1. frontend 경로 선택

- `src/adaptive-prototype/chatQuery.ts`
  - `chatTargetFor(activeDbId, registry)`는 DB 종류에 따라 경로를 정한다.

    | 선택한 DB | 경로 |
    |---|---|
    | Legacy 기준선 | `legacy`(기존 `/api/v1/chat/query` 그대로) |
    | registry `schema === 'postgres'` | `queryir` |
    | 그 밖의 합성·private SQLite | `unsupported` |

    `unsupported`는 legacy DB로 질의하지 않고 안내만 한다. 이전에는 DB를 바꿔도 legacy SQL이 legacy DB로 나갔다.
  - `adaptive-chat-query-v1` strict parser: 카드 key 허용 목록, 행 수·limit 일관성, understood filter 형식을 검사한다. SQL·물리 이름·추가 key는 거부한다.
  - client는 `{database_id, query}`만 보낸다. 질문 길이·제어 문자는 서버와 같은 규칙으로 검사한다.
  - 오류는 고정 한국어 문구로 보여준다. Ollama 미설정, timeout, DB 사용 불가, 형식 오류를 구분한다.
  - 카드 key를 기존 카드 렌더러가 읽는 legacy 행 key로 바꾼다. 예: `course_name` → `subject_name`, `credits` → `credit_hours`.
- `ChatPopup`
  - `chatTarget` prop을 받고, header에 현재 경로(`Legacy 기준선 · 기존 SQL 경로` / `… · QueryIR 경로` / `… · 챗봇 미지원`)를 표시한다.
  - QueryIR 경로의 응답 처리
    - 결과는 기존 카드와 시간표에 그대로 넣는다.
    - SQL 대신 "이해한 조건 (QueryIR)"을 보여준다.
    - `UNSUPPORTED`·`NOT_UNDERSTOOD`는 안내 문구로 보여준다.
    - 수강 이력 제외 옵션은 비활성화한다.
  - 대상 DB가 바뀌면 진행 중인 요청을 취소하고 이전 결과를 지운다. 대화가 시작된 뒤에만 변경 알림을 남긴다.
  - 늦게 도착한 이전 DB의 응답은 두 경로 모두에서 버린다.
- `MainPage`: 부분 mapping PostgreSQL DB는 catalog가 `SYNTHETIC_CATALOG_UNSUPPORTED`(409)로 답한다. 이때만 목록 대신 "AI Course Assistant로 질문해 조회" 안내를 보인다. 그 밖의 catalog 오류는 기존처럼 오류와 재시도 버튼을 보인다.

### 2. catalog 응답 구분(`synthetic_registry.py`)

- `unsupported_fields()`가 있는 PostgreSQL context의 catalog는 DB 조회 없이 409 `SYNTHETIC_CATALOG_UNSUPPORTED`를 돌려준다.
- 완전 mapping PostgreSQL과 SQLite의 응답은 변경하지 않는다.

### 3. `dev:all` 연결(`backend/adaptive/api/dev_launcher.py`)

- `backend/.env`에서 `DATABASE_URL`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL` 세 key만 읽는다. legacy backend와 같은 DB·같은 모델을 쓰게 하기 위해서다.
- private legacy-view profile이 있고 DSN 환경변수가 비어 있을 때만, DSN을 프로세스 안에서 `SCHEMA_ADAPTIVE_POSTGRES_DSN_LEGACY_VIEW`로 넘긴다. 출력·파일 기록은 하지 않는다.
- Ollama는 loopback URL일 때만 `alpaca-raw-no-system-v1` 형식으로 켠다.
- 각 부분은 독립적으로 강등된다. profile 오류, DB 연결·drift 실패, 모델 구성 실패 중 하나가 생겨도 나머지는 유지된다.
- 출력은 고정 상태 한 줄 하나다. 예: `adaptive dev launcher: legacy-view=enabled, ollama=enabled`.
- `package.json`
  - `dev:adaptive-prototype:legacy` script를 추가한다.
  - `dev:all`이 이 script를 쓰게 바꾼다.
  - 기존 `dev:adaptive-prototype`·`dev:adaptive-full`은 그대로 둔다.

## 제외

- legacy 챗봇과의 정량 비교(7-15)와 그 결과에 따른 문자열 `equals` 완화·limit 정책 결정.
- 부분 mapping DB의 목록 화면(catalog DTO 확장).
- `/adaptive-prototype` clarification 화면의 QueryIR 챗봇 연결.
- legacy backend 변경.

## 보안 계약(유지)

- browser에는 SQL·물리 이름·DSN을 보내지 않는다. 모델 호출은 질문당 1회이고 raw SQL fallback은 없다.
- DSN은 server-owned다. launcher는 legacy backend가 이미 쓰는 `.env` 값을 같은 프로세스 경계 안에서만 넘기고, 출력하지 않는다.
- QueryIR 경로의 질문과 인증 token은 legacy endpoint로 보내지 않는다.

## 테스트

- launcher
  - `.env`에서 세 key만 읽는지 확인한다.
  - 부분별 활성·비활성 사유를 확인한다(profile 없음, DSN 없음, 비loopback·잘못된 모델).
  - DB 실패에도 Ollama가, 모델 실패에도 legacy-view가 유지되는지 확인한다.
  - 출력에 값이 없고, 포트가 범위 안인지 확인한다.
- catalog: 부분 profile은 409 `SYNTHETIC_CATALOG_UNSUPPORTED`다.
- chat 서버: 1,000자를 넘는 결과 문자열은 닫힌다. frontend와 같은 제한이다.
- frontend
  - 경로 선택, parser 수용·거부, client 요청 body·오류 문구를 확인한다.
  - 카드 key 변환과 답변 설명 문구를 확인한다.
  - 소스 연결을 확인한다: layout effect 무효화, 두 경로의 응답 대상 확인, 409 전용 안내.
- browser(수동): `dev:all`에서 다음을 확인한다.
  - 합성 flat → 챗봇 미지원 표시.
  - Legacy DB (QueryIR) → 목록 안내와 질문 결과 카드·이해한 조건·미지원 안내.
  - Legacy 기준선 → 기존 SQL 경로와 SQL 패널.
