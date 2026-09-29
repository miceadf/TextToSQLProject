# Phase 8-06 지침: 코드값·값 사전(value dictionary)

작성일: 2026-09-30. 사용자 승인 개발 순서 2번.

## 1. 목적

같은 의미의 값이 DB마다 다르게 저장된다.

- 형식 차이: legacy DB의 이수구분은 `전공(핵심)`인데 사용자와 모델은 `전공핵심`이라고 쓴다.
- 코드 차이: 다른 학교는 이수구분을 `01`·`02` 같은 코드로 저장할 수 있다. 이런 코드는 사용자가 알 수 없다.

7-12·7-16의 닫힌 목록 변환은 **형식이 규칙적인 값**(학점 `3/3/0`, 시간, 요일)만 다룬다. 이수구분처럼 **값 목록 자체가 DB마다 다른 경우**는 규칙으로 풀 수 없다. 사람이 검토한 대응표(값 사전)가 필요하다.

이 단계는 이후 합성 변형 benchmark와 AI mapping 제안이 "무엇을 제안해야 하는가"의 형식을 정한다. AI가 제안하더라도 결과물은 이 값 사전 형식이고, 게시 전에 사람이 검토한다.

## 2. 핵심 결정

### 2.1 값 사전은 field별로 선언하는 닫힌 대응표다

`ValueDictionary`(검증 모델, 추가 key 금지):

- `entries`: 1~64개. 각 항목의 구성은 다음과 같다.
  - `stored`: DB에 저장된 실제 값.
  - `label`: 사용자에게 보일 이름.
  - `aliases`: 사용자가 쓸 수 있는 다른 표기, 최대 16개.
- `unknown`: 사전에 없는 값을 어떻게 처리할지 정한다.
  - `pass`: 그대로 쓴다. 이름형처럼 목록 밖 검색도 의미가 있는 경우다.
  - `reject`: "이해 못 함"으로 거부한다. 코드 column처럼 목록 밖 값이 무의미한 경우다.
- `reviewer`, `reason`: 누가 왜 선언했는지 남긴다.

조회 key는 NFKC 정규화, 소문자화, 공백 제거를 거친 문자열이다. `stored`, `label`, `aliases`가 모두 key가 된다.

- 한 key는 정확히 한 `stored`로만 이어져야 한다. 충돌하면 사전 자체를 거부한다.
- `stored` 값은 서로 달라야 한다.

### 2.2 적용 위치

- **filter 값**: compiler와 AST policy가 같은 함수 `bind_filter_value`로 값을 정한다.
  - filter 값 전체가 사전 key와 일치하면 `stored`로 바꾼다.
  - 일치하지 않으면 `unknown` 설정에 따라 그대로 쓰거나 거부한다.
  - 연산자(`equals`, `not_equals`, `contains`)는 바꾸지 않는다. `contains`는 바뀐 값을 escape해서 감싼다.
  - 부분 문자열 치환은 하지 않는다. 값 전체가 key일 때만 바꾼다.
- **결과 값**: runtime이 결과 행의 `stored`를 `label`로 바꾼다. 코드 값이 그대로 사용자에게 보이지 않게 하기 위해서다.
- **챗봇 이해 조건 표시**: 사전으로 바뀐 값은 `label`로 보여준다.

### 2.3 선언 위치와 검증

- private PostgreSQL profile `postgres-runtime-profile-v4`를 추가한다.
  - v2 key + `anchor_table`(문자열 또는 null) + `value_dictionaries`(field → 사전)로 구성한다.
  - v1·v2·v3은 그대로 읽는다.
- 시작 검증:
  - 사전 field는 매핑된 field여야 한다.
  - ontology 타입이 string이어야 한다.
  - 값 변환(transform)과 같은 field에 둘 수 없다.
  - 매핑 column의 선언 타입이 문자열 계열이어야 한다.
  - 시작 검증은 DB 값을 읽지 않는다(sample-less 유지).
- 등록 CLI(`legacy_view_profile --write --live`)는 각 `stored` 값이 실제 column에 있는지 read-only로 확인한다. 없으면 `VALUE_MISSING`으로 등록하지 않는다. 출력은 고정 code뿐이다.

### 2.4 legacy 적용

- 두 legacy profile(`legacy-view`, `legacy-base`)에 `Course.category` 사전을 선언한다(`unknown: pass`).
  - 실제 값 9개(`전공(핵심)`, `전공(기초)`, `전공(심화)`, `교양(소양)`, `교양(균형)`, `교양(소양필수)`, `교양(기초)`, `일반선택`, `교직`)를 등록한다.
  - label은 값 그대로다.
  - 괄호 없는 표기(`전공핵심` 등)와 앞뒤를 바꾼 표기(`핵심전공` 등)를 aliases로 둔다.
- 두 profile을 v4로 다시 쓴다(`--replace`). fingerprint는 바뀌지 않는다.

## 3. 검증

1. 단위 테스트:
   - 사전 모델 검증(key 충돌, 중복 stored, 크기).
   - 조회 규칙(공백·전각 정규화).
   - `pass`와 `reject` 동작.
   - compiler와 policy의 bind 일치, policy가 치환 전 값을 거부하는지.
   - 결과 decode, 챗봇 표시와 `reject` 시 `NOT_UNDERSTOOD`.
   - v4 엄격 load.
   - 시작 검증 거부 조건.
   - 등록 시 `VALUE_MISSING`.
   - 코드값 합성 예(`01` → `전공`)의 입력·출력 양방향.
2. 실제 legacy DB:
   - 두 profile을 다시 등록하고 smoke를 실행한다.
   - 같은 30문항 live 비교를 `legacy-base`로 실행한다.
   - 기대: q06 개선, 다른 문항은 변화 없음.
3. 전체 pytest, compileall, `git diff --check`를 실행한다.

## 4. 범위 밖

- FK 없는 관계 선언(관계 교정). 합성 변형 benchmark에서 필요해지면 별도 단계로 한다.
- 관리자 화면에서 값 사전 편집(8-07).
- SQLite 경로와 게시 mapping 계약(8-01~8-05)에 값 사전을 넣는 일.
- 하나의 표기가 여러 저장 값을 뜻하는 경우(1:N). 예: `전공` → 세 값. 이 경우는 지금처럼 `contains`로 처리한다.
- 모델·prompt 변경.
- 학년 등 ontology 확장.

## 5. 지켜야 할 제약

- private profile은 Git 비추적 경로에만 둔다.
- DSN·SQL·물리 이름은 출력하지 않는다.
- 모델 호출은 1회이며 raw SQL fallback은 없다.
- 실행은 read-only이고 행 상한 200을 유지한다.
- legacy 시스템은 바꾸지 않는다.
- 사람 관문은 승인으로 표시하지 않는다. 이 사전은 팀이 구성한 legacy DB에 대한 개발자 선언이며 L2 검토가 아니다.
- push하지 않는다.
