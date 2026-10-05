# Phase 8-21 결과 (3): 교시 시간표 형식 B (2026-10-06)

지침: [phase8-21-postgres-format-variety.md](prompts/phase8-21-postgres-format-variety.md). 앞의 결과: [D 넓은 table](phase8-21-wide-table-results-2026-10-02.md), [A 공통코드](phase8-21-code-table-results-2026-10-05.md).

## 1. 만든 DB

- 합성 DB의 `period_school` schema. `other_school`과 내용은 같고, 시간표만 시각 대신 교시 번호(정수)로 저장한다.
  - 교시는 09:00부터 30분 단위다(1교시 09:00, 2교시 09:30, …).
  - `gyosi_from`은 수업이 시작하는 교시다. 원래 시작 시각과 모두 일치한다(불일치 0건).
  - `gyosi_to`는 수업이 걸치는 마지막 교시다. 원래 종료 시각을 30분 단위로 올린 것이라 종료 시각은 원본과 다르다. 고정 질문 중 종료 시각을 쓰는 것은 없다.
- 고정 질문: q01~q12와 정답을 그대로 썼다. 첫 실행 전에 b01 "오후 3시 이후에 시작하는 수업 보여줘"(시작 ≥ 15:00)를 더 고정했다.
- 다시 만드는 SQL(합성 DB에서만 실행):

```sql
CREATE SCHEMA period_school;
-- isu_gubun, hakgwa, gyosu, gyogwamok, gangjwa: other_school 그대로 복사(CREATE TABLE AS SELECT *)하고 같은 PK·FK를 건다.
CREATE TABLE period_school.siganpyo AS SELECT sp_id, ks_id, yoil,
  ((CAST(SUBSTRING(si_st FROM 1 FOR 2) AS integer) * 60 + CAST(SUBSTRING(si_st FROM 3 FOR 2) AS integer) - 540) / 30 + 1)::integer AS gyosi_from,
  ((CAST(SUBSTRING(si_ed FROM 1 FOR 2) AS integer) * 60 + CAST(SUBSTRING(si_ed FROM 3 FOR 2) AS integer) - 540 + 29) / 30)::integer AS gyosi_to
FROM other_school.siganpyo;
ALTER TABLE period_school.siganpyo ADD PRIMARY KEY (sp_id), ADD FOREIGN KEY (ks_id) REFERENCES period_school.gangjwa (ks_id);
```

## 2. 첫 실행 (코드 수정 전)

- 시작·종료 시간을 정수 교시 column에 연결해도 **게시가 막히지 않았다**. 변환이 없는데도 통과했다.
- b01 "오후 3시 이후"는 **HTTP 500**이었다. 정수 column을 '15:00'과 비교해 SQL이 실패했다.
- 시간과 무관한 질문(q01·q05·q06·q12)은 맞았다.

## 3. 고친 것

- **교시표 = 값 사전.** 시간 항목의 값 사전에서 이름이 모두 `HH:MM`이면 교시표로 본다(1 → 09:00).
  - 저장 위치, 게시, 관리자 화면(값 사전 패널), 결과 카드의 이름 표시는 기존 값 사전 기능을 그대로 쓴다. 카드에는 교시 번호 대신 시각이 보인다.
- **시간 비교 → 교시 목록.** "시작 시간 < 12:00"은 `column IN (12시 전에 시작하는 교시들)`이 된다.
  - compiler와 SQL policy가 같은 함수(`time_label_values`)로 목록을 정한다. policy는 그 조건에만 정확한 `IN` 모양을 허용한다.
- **조건에 맞는 교시가 없으면** SQL 없이 0건으로 답하고 이유를 보인다. 예: "밤 10시 이후" → "종료 시간에서 '22:00:00' 값을 찾지 못했습니다"(모델이 종료 시간으로 이해함).
- **게시 검사**
  - 시간 항목이 정수 column에 연결되었는데 교시표가 없으면, 게시를 막고 그 항목을 알려 준다.
  - runtime도 같은 경우를 거부한다.
- **관리자 입력**
  - 교시표 항목은 "DB에 있는 값" 검사 대신 형식 검사를 한다. 교시는 숫자, 이름은 `HH:MM`이고 별칭은 없다.
  - 아직 쓰지 않는 교시도 넣을 수 있다. 학교 시간표 전체를 한 번에 넣는 것이 자연스럽고, 표에 빠진 교시는 조건에 걸리지 않기 때문이다.
- 값 읽기(값 사전·힌트용)는 column을 글자로 읽는다. 그래야 정수 교시 값도 목록에 보인다.

## 4. 고친 뒤 결과

- 교시표(시작·종료 각 18개)를 넣고 게시: 고정 질문 **11/13**. 틀린 2개는 모두 모델이 조건을 다르게 이해한 것이다.
  - q11 "금요일 오전 수업"은 모델이 시간 조건을 뺐다(8-20·D에서도 본 흔들림).
  - b01 "오후 3시 이후에 시작하는 수업"은 모델이 종료 시간으로 이해했다.
- 교시 계산 자체는 정확했다.
  - "금요일 12시 이전에 시작하는 수업" → 7개(정답).
  - "15시 이후 시작 수업" → 모델이 `>`로 이해해 15:00 시작은 뺀 12건이었다.
- 레거시: 컴융 전공 82건, 월요일 오전 200건(상한), 컴융 3학년 전공 33건으로 이전과 같다.

## 5. 리뷰

`gpt-6-luna` high review-only 지적 1건을 재현하고 고쳤다.

- **`01`처럼 0을 붙인 교시 번호가 입력되었다.** 정수 column은 `1`을 돌려주므로 카드에 `09:00` 대신 `1`이 보였을 것이다. 이제 교시 번호는 `1`처럼 0 없는 정수만 받는다. 같은 교시가 `1`과 `01` 두 항목으로 들어가는 것도 막힌다. 회귀 테스트를 추가했다.

## 6. 남은 것

- "1교시 수업"처럼 교시 번호로 묻는 질문은 다루지 않는다. 공통 항목에 교시가 없다.
- 관리자 화면의 값 사전 패널은 값 목록이 오면 그 안에서만 고르게 한다. 교시표 항목은 서버가 목록을 보내지 않아 자유 입력 칸이 나온다. 화면을 직접 열어 확인하지는 않았다(API 테스트로만 확인).
- 형식 C(한 칸에 묶인 시간표)가 남았다.
