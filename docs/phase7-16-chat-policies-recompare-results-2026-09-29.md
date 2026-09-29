# Phase 7-16 결과: 챗봇 서버 정책 3종 적용 뒤 재비교 (2026-09-29)

> 잠정 결과다. 질문 30개와 기준은 7-15와 같다(개발자 작성, L1 전). 모델 성능의 확정 수치로 쓰지 않는다.

## 1. 적용한 정책(사용자 결정)

| 정책 | 위치 | 내용 |
|---|---|---|
| 결과 limit 200 고정 | 챗봇 endpoint | 모델 limit(항상 prompt 예시값 50) 대신 서버 상한 200을 쓴다 |
| 이름형 문자열 `contains` | 챗봇 endpoint | 과목명·학과명·교원명·이수구분의 `equals`를 `contains`로 바꾼다. 이해한 조건에 `포함`으로 표시된다 |
| 요일 값 정규화 | legacy-view profile의 선언형 변환 `KOREAN_WEEKDAY` | `토요일`·`Sat`·`saturday` → `토`. 그 밖의 값은 거부한다 |

리뷰 지적으로 함께 고친 점: PostgreSQL `contains`는 값 속의 `%`·`_`·`\`를 escape한다. 예전에는 `전공_핵심`이 `전공X핵심`에도 일치했다. 실제 PostgreSQL에서 동작을 확인했다.

## 2. 요약(수용 가능 문항 수)

| 범위 | legacy | QueryIR 7-15 | QueryIR 7-16 |
|---|---:|---:|---:|
| ONTOLOGY (22) | **17** | 11 | **14** |
| OUTSIDE_SCOPE (5) | 4 | 1* | 0 |
| UNSUPPORTED_FIELD (3) | 0 | 3 | **3** |
| 전체 (30) | **21** | 15 | 17 |

\* 7-15의 q24는 50행 상한 안에서 우연히 맞은 경우였다. limit 200에서는 `INCORRECT`로 드러난다.

- 두 실행 사이 모델 출력과 legacy SQL은 문항별로 모두 같았다(temperature 0). 차이는 전부 서버 정책의 효과다.
- ONTOLOGY 범위 11 → 14. 7-15 오프라인 추정치와 같다.
- 개선된 문항:
  - q05 `수학과`, q22 `물리학과`: contains 완화
  - q19 `토요일`: 요일 정규화
  - q21 `컴퓨터프로그래밍`: contains 완화
  - q02·q11·q14·q18: 상한 때문에 잘리던 답이 limit 200에서 전체 정답(`EXACT`)이 되었다.
- 판정이 엄격해진 문항: q20(`미적분학1` 정확 지정)과 q24(학년 조건 누락)는 50행 상한에서 가려져 있던 오류가 드러났다.
- 응답 시간 중앙값: legacy 0.9초, QueryIR 0.7초.

## 3. 문항별 결과

| ID | 범위 | 질문 | 기준 | legacy | QueryIR 7-15 | QueryIR 7-16 |
|---|---|---|---:|---|---|---|
| q01 | ONTOLOGY | 컴퓨터융합학부 전공 과목 보여줘 | 48 | CORRECT_SUBSET | NO_ANSWER | NO_ANSWER |
| q02 | ONTOLOGY | 교양 과목 중에 금요일 수업 | 119 | EXACT | CORRECT_SUBSET | EXACT |
| q03 | ONTOLOGY | 3학점 과목 알려줘 | 2333 | EMPTY | CORRECT_SUBSET | CORRECT_SUBSET |
| q04 | ONTOLOGY | 월요일 오전 수업 보여줘 | 360 | CORRECT_SUBSET | CORRECT_SUBSET | CORRECT_SUBSET |
| q05 | ONTOLOGY | 수학과 강의 목록 | 82 | CORRECT_SUBSET | EMPTY | **EXACT** |
| q06 | ONTOLOGY | 전공핵심 과목 중 화요일 수업 | 382 | CORRECT_SUBSET | EMPTY | EMPTY |
| q07 | ONTOLOGY | 데이터베이스 강의 알려줘 | 5 | EXACT | EXACT | EXACT |
| q08 | ONTOLOGY | 딥러닝 수업 있어? | 3 | EXACT | EXACT | EXACT |
| q09 | ONTOLOGY | 김소연 교수님 수업 | 41 | EXACT | EXACT | EXACT |
| q10 | ONTOLOGY | 이규철 교수 강의 보여줘 | 2 | EXACT | EXACT | EXACT |
| q11 | ONTOLOGY | 정원이 100명 넘는 강의 | 88 | CORRECT_SUBSET | CORRECT_SUBSET | EXACT |
| q13 | ONTOLOGY | 오후 3시에 시작하는 수업 | 628 | INCORRECT | NO_ANSWER | NO_ANSWER |
| q14 | ONTOLOGY | 2학점 교양 과목 | 166 | EMPTY | CORRECT_SUBSET | EXACT |
| q15 | ONTOLOGY | 경영학부 전공기초 과목 | 27 | EXACT | EMPTY | EMPTY |
| q16 | ONTOLOGY | 목요일 저녁 6시 이후 수업 | 82 | EXACT | INCORRECT | INCORRECT |
| q17 | ONTOLOGY | 영어영문학과 3학점 전공 과목 | 21 | EMPTY | NO_ANSWER | NO_ANSWER |
| q18 | ONTOLOGY | 일반선택 과목 보여줘 | 81 | CORRECT_SUBSET | CORRECT_SUBSET | EXACT |
| q19 | ONTOLOGY | 토요일에 하는 수업 있어? | 28 | EXACT | EMPTY | **EXACT** |
| q20 | ONTOLOGY | 미적분학 강의 목록 | 36 | CORRECT_SUBSET | CORRECT_SUBSET | INCOMPLETE |
| q21 | ONTOLOGY | 컴퓨터프로그래밍 수업 시간 알려줘 | 27 | UNKEYED | INCOMPLETE | **EXACT** |
| q22 | ONTOLOGY | 물리학과 월요일 수업 | 34 | EXACT | EMPTY | **EXACT** |
| q31 | ONTOLOGY | 간호학과 전공 과목 중 수요일 오후 수업 | 13 | EXACT | EMPTY | INCOMPLETE |
| q12 | OUTSIDE_SCOPE | 아직 자리가 남은 컴퓨터융합학부 과목 | 35 | CORRECT_SUBSET | EMPTY | EMPTY |
| q23 | OUTSIDE_SCOPE | 컴퓨터융합학부 3학년 전공 과목 | 18 | EXACT | EMPTY | EMPTY |
| q24 | OUTSIDE_SCOPE | 1학년이 듣는 교양 과목 | 712 | CORRECT_SUBSET | CORRECT_SUBSET | INCORRECT |
| q25 | OUTSIDE_SCOPE | 온라인 콘텐츠 수업 보여줘 | 138 | EMPTY | NO_ANSWER | NO_ANSWER |
| q26 | OUTSIDE_SCOPE | 선수과목이 없는 컴퓨터융합학부 과목 | 24 | EXACT | NO_ANSWER | NO_ANSWER |
| q28 | UNSUPPORTED_FIELD | 2024년에 개설된 강의 알려줘 | - | ANSWERED_WITHOUT_BASIS | REFUSED | REFUSED |
| q29 | UNSUPPORTED_FIELD | 1학기 전공 과목 | - | ANSWERED_WITHOUT_BASIS | REFUSED | REFUSED |
| q30 | UNSUPPORTED_FIELD | 2학기에 열리는 교양 과목 | - | ANSWERED_WITHOUT_BASIS | REFUSED | REFUSED |

## 4. ONTOLOGY 범위 22문항 대조

| 구분 | 문항 수 | 문항 |
|---|---:|---|
| 둘 다 수용 가능 | 11 | q02, q04, q05, q07, q08, q09, q10, q11, q18, q19, q22 |
| QueryIR만 수용 가능 | 3 | q03·q14(학점 형식), q21(legacy가 key 없이 조회) |
| legacy만 수용 가능 | 6 | q01, q06, q15, q16, q20, q31 |
| 둘 다 불가 | 2 | q13, q17 |

legacy만 맞힌 6문항의 원인:

| 원인 | 문항 | 다음 후보 |
|---|---|---|
| 없는 field 이름 | q01 (`Course.department`) | field 별칭 교정 또는 prompt |
| 이수구분 값 형식 | q06 (`전공핵심` 대 `전공(핵심)`) | 이수구분 값 사전(8-06) |
| 조건 해석 | q15 (질문 전체를 과목명에), q16 (종료 시간으로 비교), q31 (없는 시간 범위 추가) | 모델·prompt |
| 과도한 값 지정 | q20 (`미적분학1`) | 모델·prompt |

## 5. 남은 결정 항목

| 항목 | 예상 효과(이 질문 세트) |
|---|---|
| field 별칭 교정(`Course.department` → 학과명 등) | q01, q17 후보 |
| 이수구분 값 사전(`전공핵심` → `전공(핵심)` 등) | q06 후보 |
| 학년 등 ontology 확장 | OUTSIDE_SCOPE 학년 2문항(q23, q24) |
| prompt 개선(조건 해석, 시간 field 구분) | q15, q16, q20, q31 후보 |
| 개수 요청 존중("3개만") | 현재 질문 세트에는 해당 없음 |

## 6. 한계

7-15 결과 문서 §5와 같다. 개발자 작성 문항·기준이고 실행은 1회다. 상한 안의 우연한 일치는 부분집합으로 보일 수 있다. 원자료는 `.schema-adaptive/reports/phase7-15/p716-run1.json`에 있다.
