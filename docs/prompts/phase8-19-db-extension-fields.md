# Phase 8-19 지침: DB별 확장 항목 (8-11 축소판)

작성일: 2026-10-01. 사용자 결정: 진행(8-18 → 8-19 → 8-20 순서).

## 1. 목적

- 강의실처럼 어떤 학교에서는 중요하지만 공통 ontology에 넣을 정도는 아닌 정보를, 관리자가 그 DB에만 추가한다.
- 코드는 고치지 않고, 관리자가 화면에서 데이터로 선언한다.
- 공통 항목과 똑같이 QueryIR 검증, compiler, SQL policy, read-only executor를 거친다.

## 2. 확장 항목 선언

- 대상: 화면 등록 PostgreSQL DB.
- 선언하는 값:
  - **label:** 화면과 모델에 보이는 이름. 예: 강의실. 1~20자.
  - **key:** 영문 소문자로 시작하고 영문 소문자·숫자·밑줄만 쓴다. 최대 31자.
  - **entity:** 공통 entity 5개 중 하나. join 경로 판단에 쓴다.
  - **table, column:** 등록 catalog에 실제로 있는 것.
  - **data_type:** `string` | `integer` | `number`.
- 항목 이름은 `<Entity>.ext_<key>`이다(예: `Schedule.ext_classroom`). 공통 항목과 겹치지 않는다.
- 검사:
  - column 타입과 data_type이 맞아야 한다(문자 ↔ string, 정수 ↔ integer, 수치 ↔ number).
  - 한 column은 한 항목에만 연결한다. 검토에서 승인된 column이나 다른 확장 항목과 겹치면 막는다.
  - DB당 최대 10개.
- 저장 위치: 등록 폴더의 `extensions.json`(변경 가능). 게시해야 챗봇에 반영된다. 값 사전과 같은 방식이다.

## 3. 게시와 runtime

- 게시하면 확장 항목이 게시 mapping의 field로 들어간다. profile은 v5이고 `extension_fields`(label, data_type)를 담는다.
- runtime 검사:
  - 이름 형식과 entity를 확인한다.
  - mapping에 있는지 확인한다.
  - column 타입이 맞는지 확인한다.
  - 변환·값 사전·unsupported 목록과 겹치지 않는지 확인한다.
- 공통 항목 검사(`mapped | unsupported == 버전의 항목`)에서는 확장 항목을 뺀다.

## 4. 챗봇

- 그 DB의 runtime ontology = 공통 항목 + 확장 항목.
  - 연산자: string은 equals·contains, 숫자는 equals와 크기 비교.
- 모델 prompt의 field 목록에서 확장 항목에만 label을 붙인다. 확장 항목이 없는 DB의 prompt는 바뀌지 않는다.
- 문자열 확장 항목의 equals는 이름형 항목처럼 contains로 완화한다.
- 결과 카드:
  - DTO key는 `ext_<key>`이다.
  - 응답에 `extensions: [{key, label}]`를 추가한다.
  - frontend는 이 목록에 있는 key만 받고, 카드 상세에 label로 보인다.

## 5. 화면

- PostgreSQL 등록 검토 아래에 "확장 항목" 패널을 둔다.
  - 목록과 삭제.
  - 추가 양식: label, key, entity, table, column, data_type.
- 게시는 기존 게시 버튼과 같다. 패널에 "게시해야 챗봇에 반영" 안내를 둔다.

## 6. 검증

- 8-17 가상 학교 DB의 시간표에 강의실 column을 추가한다. 질문과 정답을 실행 전에 고정한다.
- 레거시 DB(`public`)를 화면 경로로 등록하고 강의실을 확장 항목으로 추가해 확인한다(읽기 전용).

## 7. 제약

- 확장 항목에는 변환과 값 사전을 붙이지 않는다(이번 범위).
- 강의 목록 계약은 그대로 둔다.
- push하지 않는다.
