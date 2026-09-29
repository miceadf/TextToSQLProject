# Phase 7-16 구현 지침: 챗봇 서버 정책 3종 적용과 재비교

## 배경

7-15 비교(개발자 작성 30문항)에서 매핑 범위 22문항 중 수용 가능한 답은 legacy 17, QueryIR 11이었다. 같은 모델 출력에 서버 정책을 바꿔 재실행한 결과는 다음과 같았다.

- limit 200 + 문자열 `contains`: 13
- 요일 정규화 추가(오프라인 추정): 14

사용자는 이 세 가지 적용과 재비교를 결정했다(2026-09-29).

## 범위

### 1. 결과 limit 200 고정(`api/chat_query.py`)

- 챗봇 경로는 모델이 낸 limit 대신 서버 상한 `CHAT_ROW_LIMIT = 200`을 쓴다. executor 최대와 같다.
- 모델의 limit은 이번 실행에서 항상 prompt 예시값 50이었다. 표시 행 수는 화면 규칙이므로 서버가 정한다.
- 한계: "3개만 보여줘" 같은 개수 요청은 반영하지 않는다(문서에 명시).

### 2. 이름형 문자열 field의 `equals` → `contains`(`api/chat_query.py`)

- 대상 field는 `Course.name`, `Department.name`, `Instructor.name`, `Course.category`다. 모델이 `equals`를 쓰면 서버가 `contains`로 바꾼다.
- 코드형 field(`Course.code`, `CourseOffering.section`)와 요일은 바꾸지 않는다.
- 바뀐 연산자는 "이해한 조건"에 그대로 보인다. 예: `학과명에 "수학과" 포함`.
- 바뀐 QueryIR은 기존 QueryIR 검증·compiler·AST policy를 그대로 거친다.

### 3. 요일 값 정규화: profile 선언형 변환 `KOREAN_WEEKDAY`(`mapping/transforms.py`)

- 요일 표기는 DB마다 다를 수 있다(`월`, `MON`, `1` 등). 그래서 챗봇 전역 규칙이 아니라 **profile이 선언하는 닫힌 목록 변환**으로 둔다. ADR-068의 확장이며, 8-06 코드값 교정의 첫 사례다.
- `KOREAN_WEEKDAY`
  - SQL 식: column 그대로(identity).
  - filter 값: `월`·`월요일`·`mon`·`monday`(대소문자 무시) 등을 한 글자 `월`로 바꾼다. 월~일 7개 요일을 모두 다룬다.
  - 그 밖의 값(`주말` 등)은 표현 불가로 거부한다: compiler는 `INVALID_VALUE_TYPE`, 챗봇은 `NOT_UNDERSTOOD`.
  - 허용 field 타입: ontology `string`. 허용 column 타입: 문자열.
  - 허용 연산자: `equals`, `not_equals`, `contains`.
- 변환별 허용 연산자를 함수로 둔다(`transform_operators`). 기존 두 변환의 허용 연산자는 바뀌지 않는다.
- compiler와 policy는 같은 정규화를 적용한다. identity 변환도 AST exact 비교를 통과해야 한다.
- legacy-view profile(`legacy_view_profile.py`)의 선언에 `Schedule.day: KOREAN_WEEKDAY`를 더한다. private profile 파일은 `--write --live --replace`로 갱신한다(fingerprint 불변).

### 4. 재비교

- `legacy_chat_compare --live`를 같은 질문 세트로 다시 실행한다(`p716-run1`).
- 7-15의 `p715-run1`과 문항별로 비교한다. 모델 출력은 temperature 0이다.
- 정책이 서버에 들어갔으므로 `queryir` 열 자체가 정책 적용 결과다. 기존 변형 열(`+limit200`, `+limit200+contains`)은 같은 filter의 재실행이라 거의 같아진다.
- 결과 문서: `docs/phase7-16-chat-policies-recompare-results-2026-09-29.md`.

## 제외

- 이수구분 값 사전(`전공핵심` ↔ `전공(핵심)`), 모델이 만든 field 이름의 별칭 교정, 학년 등 ontology 확장, prompt 변경.
- 개수 요청("3개만")을 존중하는 limit 규칙.

## 보안 계약(유지)

- 모델 호출은 1회이고 raw SQL fallback은 없다. 정책은 QueryIR 수준의 결정적 재작성이며, 결과는 기존 검증·compiler·policy·executor를 그대로 거친다.
- 변환은 닫힌 목록이고, 식은 고정 template에서만 나온다. SQL·물리 이름은 응답에 넣지 않는다.

## 테스트

- 챗봇 서버 정책
  - limit이 200으로 고정되는지 확인한다.
  - 이름형 field `equals`가 `contains`로 바뀌고, 코드형·수치형 field는 유지되는지 확인한다.
  - understood 표시가 실제 실행 조건과 같은지 확인한다.
- `KOREAN_WEEKDAY`
  - 정규화 표와 거부 값을 확인한다.
  - compiler와 policy에 같은 값이 bind되는지 확인한다.
  - 허용 연산자(`contains` 허용)와 기존 변환의 연산자 불변을 확인한다.
  - identity 변환의 AST 검증(다른 column·함수 거부)을 확인한다.
- profile·runtime: `Schedule.day` 변환 선언이 타입 검사를 통과하는지 확인한다.
- live: 재비교를 실행하고 7-15 대비 문항별 변화를 확인한다.
