# Phase 8-21 결과 (4): 한 칸에 묶인 시간표 형식 C (2026-10-06)

지침: [phase8-21-postgres-format-variety.md](prompts/phase8-21-postgres-format-variety.md) 3절 C의 ①(요일만 지원, 시작·종료 시간은 "이 DB에 없음").

## 1. 만든 DB

- 합성 DB의 `packed_school` schema. `other_school`과 내용은 같고, 시간표 table 대신 강좌 table에 `sigan_txt`(예: `월 13:30-14:45, 수 13:30-14:45`)가 있다.
- 고정 기대(실행 전): q01~q10·q12는 답한다. q11 "금요일 오전 수업"과 b01 "오후 3시 이후에 시작하는 수업"은 시작 시간이 이 DB에 없으므로 "정보 없음"으로 거절하는 것이 맞다.
- 다시 만드는 SQL(합성 DB에서만 실행):

```sql
CREATE SCHEMA packed_school;
-- isu_gubun, hakgwa, gyosu, gyogwamok: other_school 그대로 복사하고 같은 PK·FK를 건다.
CREATE TABLE packed_school.gangjwa AS SELECT g.*, (SELECT string_agg(
    (CASE s.yoil WHEN 'MON' THEN '월' WHEN 'TUE' THEN '화' WHEN 'WED' THEN '수' WHEN 'THU' THEN '목' ELSE '금' END)
    || ' ' || SUBSTRING(s.si_st FROM 1 FOR 2) || ':' || SUBSTRING(s.si_st FROM 3 FOR 2)
    || '-' || SUBSTRING(s.si_ed FROM 1 FOR 2) || ':' || SUBSTRING(s.si_ed FROM 3 FOR 2), ', ' ORDER BY s.sp_id)
  FROM other_school.siganpyo s WHERE s.ks_id = g.ks_id)::varchar(100) AS sigan_txt
FROM other_school.gangjwa g;
ALTER TABLE packed_school.gangjwa ADD PRIMARY KEY (ks_id), ADD FOREIGN KEY (gm_cd) REFERENCES packed_school.gyogwamok (gm_cd),
  ADD FOREIGN KEY (gs_no) REFERENCES packed_school.gyosu (gs_no);
```

## 2. 첫 실행 (코드 수정 전)

강의 요일을 `sigan_txt`에 연결하고, 시작·종료 시간은 "이 DB에 없음"으로 검토해 게시했다.

- 12/13. q11·b01은 "시작 시간 정보 없음"으로 바르게 거절했다(8-20 이전부터 있던 unsupported 처리).
- q05 "월요일 수업"이 0건이었다. 강의 요일 = '월요일'을 묶인 글자 전체와 같음으로 비교했기 때문이다. 0건 진단(8-20)이 그 조건을 짚었다.

## 3. 고친 것

- **닫힌 변환 `PACKED_WEEKDAY`:** "월요일"을 "월"로 바꾸고 포함으로 찾는다(`LIKE '%월%'`). 연산자는 포함만 허용한다. 챗봇은 같음을 포함으로 바꾼다(이름형 항목과 같은 방식).
- **서버 규칙으로만 붙는다.** 저장 값이 모두 "요일 + 숫자·시각 기호"(`월 09:00-10:15, 수 …`, `월1-2/수3`) 꼴일 때만 붙는다. 과목명처럼 다른 글자가 섞이면 붙지 않는다. 요일 한 글자만 있는 column은 기존 `KOREAN_WEEKDAY`가 맡는다.
- **게시 때 값 표본 읽기:** 게시 때 변환을 정하려고 값을 50개까지 읽는데, 값 종류가 그보다 많으면 아무 변환도 붙지 않았다(묶인 시간표는 80종). 이제 값이 많으면 50개 표본을 읽는다. 표본으로는 `PACKED_WEEKDAY`만 고를 수 있다. 이 변환은 `LIKE`로만 찾아서, 표본 밖의 이상한 값이 있어도 SQL이 실패하지 않는다. 다른 변환(학점 숫자 변환 등)은 지금처럼 전체 값이 필요하다.

## 4. 고친 뒤 결과

- `packed_school` **13/13**(q11·b01은 기대대로 거절).
- 레거시: 컴융 전공 82건, 월요일 오전 200건(상한)으로 이전과 같다.

## 5. 리뷰

`gpt-6-luna` high review-only 지적 1건을 재현하고 고쳤다.

- **표본이 모든 변환 결정에 쓰였다.** 값이 50종 넘는 학점 column에서 표본 50개가 `3/3/0` 꼴이면 `SLASH_FIRST_NUMERIC`이 붙을 수 있었다. 그러면 표본 밖의 이상한 값(`audit` 등)에서 숫자 변환이 실패한다. 이전에는 이런 column에 변환이 붙지 않았다.
- 고친 내용: 표본 값에는 표시(`SampledValues`)를 붙이고, 표본으로는 `PACKED_WEEKDAY`만 고르게 했다. 회귀 테스트를 추가했다. 고친 뒤 `packed_school`을 다시 등록해 13/13을 확인했다.

## 6. 남은 것

- 묶인 시간표에서 시작·종료 시간까지 꺼내는 문자열 해석 변환(지침의 ②)은 하지 않았다. 필요하면 사용자 결정으로 진행한다.
- "금요일 오전 수업"은 요일 부분까지 함께 거절된다(시작 시간이 없으므로). 요일만으로 답하지 않는 것이 맞다고 보았다.
