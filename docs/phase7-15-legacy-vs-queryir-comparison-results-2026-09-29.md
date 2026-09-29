# Phase 7-15 결과: 같은 질문에 대한 legacy 챗봇과 QueryIR 경로 비교 (2026-09-29)

> 잠정 결과다. 질문 30개와 기준 조건은 개발자가 결과를 보기 전에 작성했으며, gold가 아니므로 L1(사람 검토) 전이다. 모델 성능의 확정 수치로 쓰지 않는다(L1·L3 필요).

## 1. 무엇을 비교했나

- 같은 legacy DB의 view `v_course_info`(팀이 구성한 유사 데이터)와 같은 모델 `text2sql_qwen`을 쓰는 두 챗봇을 비교했다.
  - legacy: `/api/v1/chat/query`
  - QueryIR: `/api/adaptive/v1/chat/query`
- 질문 세트: `backend/adaptive/evaluation/data/legacy-chat-compare-v1.json` (30문항)
  - `ONTOLOGY` 22: 매핑된 field만으로 답할 수 있다.
  - `OUTSIDE_SCOPE` 5: legacy view에는 있으나 현재 ontology 밖이다(학년, 수업 방식, 선수과목, field 간 비교).
  - `UNSUPPORTED_FIELD` 3: DB에 없는 연도·학기를 묻는다.
- 판정: 각 답을 개설강좌 key(과목 코드, 분반) 집합으로 줄여 기준 SQL의 key 집합과 비교했다.
  - `EXACT`: 기준과 같다.
  - `CORRECT_SUBSET`: 결과 상한에 도달했고, 반환된 강좌가 모두 조건에 맞는다.
  - `INCOMPLETE`: 상한에 도달하지 않았는데 일부 강좌가 빠졌다.
  - `EMPTY`: 0행이다.
  - `INCORRECT`: 조건에 맞지 않는 강좌가 섞였다.
  - `NO_ANSWER`: 이해 못 함·오류.
  - `UNKEYED`: legacy가 key column 없이 조회했다.
  - `REFUSED`: 올바른 "답할 수 없음".
  - `ANSWERED_WITHOUT_BASIS`: DB에 근거가 없는데 행을 반환했다.
- **수용 가능 = `EXACT` + `CORRECT_SUBSET` + `REFUSED`.**
- 두 경로의 구성 차이:
  - legacy: 전처리(오타·학과명 등 키워드 교정, entity masking, cache), 기본 `LIMIT 50`, 시간표 조건이 있으면 일치 강좌의 전체 시간표 행으로 확장(최대 200행)
  - QueryIR: 전처리 없음, 모델이 정한 limit(이번 실행에서는 항상 50)
- 서버 정책 변형(같은 모델 출력, 모델 재호출 없음):
  - `+limit200`: limit를 200으로 올린다.
  - `+limit200+contains`: 추가로 과목명·학과명·교원명·이수구분의 `equals`를 `contains`로 바꾼다.
- 실행: 2026-09-29, `npm run dev:all`, 로컬 Ollama. 원자료는 Git 비추적 `.schema-adaptive/reports/phase7-15/p715-run1.json`.

## 2. 요약

수용 가능 문항 수:

| 범위 | legacy | QueryIR | +limit200 | +limit200+contains |
|---|---:|---:|---:|---:|
| ONTOLOGY (22) | **17** | 11 | 10 | **13** |
| OUTSIDE_SCOPE (5) | 4 | 1* | 0 | 0 |
| UNSUPPORTED_FIELD (3) | 0 | **3** | 3 | 3 |
| 전체 (30) | **21** | 15 | 13 | 16 |

\* q24 `1학년이 듣는 교양 과목`에서 QueryIR은 학년 조건 없이 교양 과목만 조회했다. 그런데 앞쪽 50건이 우연히 모두 1학년 과목이라 `CORRECT_SUBSET`으로 판정되었다. limit 200에서는 `INCORRECT`로 드러난다(§5 한계 1).

- **매핑 범위 안의 질문에서는 현재 legacy가 앞선다(17 대 11).** 서버 정책 두 가지(limit 200, 문자열 `contains`)만 바꿔도 13이 된다.
- **DB에 없는 정보(연도·학기)를 물으면 QueryIR만 올바르게 거절한다(3/3).** legacy는 3문항 모두 근거 없는 결과를 반환했다. `2024년에 개설된 강의`를 `course_year = 4`(4학년)로 조회한 경우도 있다.
- **학점 형식처럼 값 표현이 다른 경우는 QueryIR이 앞선다.**
  - legacy prompt는 `credit_hours = '3'`처럼 비교하라고 지시한다. 실제 값은 `3/3/0` 형식이라 legacy는 `3학점`·`2학점 교양`·`영어영문학과 3학점 전공` 3문항에서 0행을 반환했다.
  - QueryIR은 선언형 변환(7-12)으로 이 중 2문항에 정답을 냈다. 나머지 1문항은 다른 이유로 실패했다.
- ontology 밖의 질문(학년, 선수과목 등)은 설계상 QueryIR이 답하지 못한다. legacy는 5문항 중 4문항을 맞혔다.
- 응답 시간 중앙값: legacy 1.2초(첫 호출 9.1초), QueryIR 0.9초(최대 1.6초).

## 3. 문항별 결과

| ID | 범위 | 질문 | 기준 강좌 수 | legacy | QueryIR | +limit200 | +limit200+contains |
|---|---|---|---:|---|---|---|---|
| q01 | ONTOLOGY | 컴퓨터융합학부 전공 과목 보여줘 | 48 | CORRECT_SUBSET | NO_ANSWER | NO_ANSWER | NO_ANSWER |
| q02 | ONTOLOGY | 교양 과목 중에 금요일 수업 | 119 | EXACT | CORRECT_SUBSET | EXACT | EXACT |
| q03 | ONTOLOGY | 3학점 과목 알려줘 | 2333 | EMPTY | CORRECT_SUBSET | CORRECT_SUBSET | CORRECT_SUBSET |
| q04 | ONTOLOGY | 월요일 오전 수업 보여줘 | 360 | CORRECT_SUBSET | CORRECT_SUBSET | CORRECT_SUBSET | CORRECT_SUBSET |
| q05 | ONTOLOGY | 수학과 강의 목록 | 82 | CORRECT_SUBSET | EMPTY | EMPTY | EXACT |
| q06 | ONTOLOGY | 전공핵심 과목 중 화요일 수업 | 382 | CORRECT_SUBSET | EMPTY | EMPTY | EMPTY |
| q07 | ONTOLOGY | 데이터베이스 강의 알려줘 | 5 | EXACT | EXACT | EXACT | EXACT |
| q08 | ONTOLOGY | 딥러닝 수업 있어? | 3 | EXACT | EXACT | EXACT | EXACT |
| q09 | ONTOLOGY | 김소연 교수님 수업 | 41 | EXACT | EXACT | EXACT | EXACT |
| q10 | ONTOLOGY | 이규철 교수 강의 보여줘 | 2 | EXACT | EXACT | EXACT | EXACT |
| q11 | ONTOLOGY | 정원이 100명 넘는 강의 | 88 | CORRECT_SUBSET | CORRECT_SUBSET | EXACT | EXACT |
| q13 | ONTOLOGY | 오후 3시에 시작하는 수업 | 628 | INCORRECT | NO_ANSWER | NO_ANSWER | NO_ANSWER |
| q14 | ONTOLOGY | 2학점 교양 과목 | 166 | EMPTY | CORRECT_SUBSET | EXACT | EXACT |
| q15 | ONTOLOGY | 경영학부 전공기초 과목 | 27 | EXACT | EMPTY | EMPTY | EMPTY |
| q16 | ONTOLOGY | 목요일 저녁 6시 이후 수업 | 82 | EXACT | INCORRECT | INCORRECT | INCORRECT |
| q17 | ONTOLOGY | 영어영문학과 3학점 전공 과목 | 21 | EMPTY | NO_ANSWER | NO_ANSWER | NO_ANSWER |
| q18 | ONTOLOGY | 일반선택 과목 보여줘 | 81 | CORRECT_SUBSET | CORRECT_SUBSET | EXACT | EXACT |
| q19 | ONTOLOGY | 토요일에 하는 수업 있어? | 28 | EXACT | EMPTY | EMPTY | EMPTY |
| q20 | ONTOLOGY | 미적분학 강의 목록 | 36 | CORRECT_SUBSET | CORRECT_SUBSET | INCOMPLETE | INCOMPLETE |
| q21 | ONTOLOGY | 컴퓨터프로그래밍 수업 시간 알려줘 | 27 | UNKEYED | INCOMPLETE | INCOMPLETE | EXACT |
| q22 | ONTOLOGY | 물리학과 월요일 수업 | 34 | EXACT | EMPTY | EMPTY | EXACT |
| q31 | ONTOLOGY | 간호학과 전공 과목 중 수요일 오후 수업 | 13 | EXACT | EMPTY | EMPTY | EMPTY |
| q12 | OUTSIDE_SCOPE | 아직 자리가 남은 컴퓨터융합학부 과목 | 35 | CORRECT_SUBSET | EMPTY | EMPTY | EMPTY |
| q23 | OUTSIDE_SCOPE | 컴퓨터융합학부 3학년 전공 과목 | 18 | EXACT | EMPTY | EMPTY | EMPTY |
| q24 | OUTSIDE_SCOPE | 1학년이 듣는 교양 과목 | 712 | CORRECT_SUBSET | CORRECT_SUBSET | INCORRECT | INCORRECT |
| q25 | OUTSIDE_SCOPE | 온라인 콘텐츠 수업 보여줘 | 138 | EMPTY | NO_ANSWER | NO_ANSWER | NO_ANSWER |
| q26 | OUTSIDE_SCOPE | 선수과목이 없는 컴퓨터융합학부 과목 | 24 | EXACT | NO_ANSWER | NO_ANSWER | NO_ANSWER |
| q28 | UNSUPPORTED_FIELD | 2024년에 개설된 강의 알려줘 | - | ANSWERED_WITHOUT_BASIS | REFUSED | REFUSED | REFUSED |
| q29 | UNSUPPORTED_FIELD | 1학기 전공 과목 | - | ANSWERED_WITHOUT_BASIS | REFUSED | REFUSED | REFUSED |
| q30 | UNSUPPORTED_FIELD | 2학기에 열리는 교양 과목 | - | ANSWERED_WITHOUT_BASIS | REFUSED | REFUSED | REFUSED |

## 4. 실패 유형

### QueryIR (ONTOLOGY 범위에서 수용 불가 11문항)

| 유형 | 문항 | 내용 | 고칠 수 있는 곳 |
|---|---|---|---|
| 없는 field 이름 생성 | q01, q17, q13 | 모델이 `Course.department`(2건), `Schedule.section`(1건)을 만들어 `NOT_UNDERSTOOD` | prompt, 또는 서버의 field 별칭 교정(후보) |
| 문자열 `equals`로 부분 이름 비교 | q05, q22 | `수학과`·`물리학과`를 실제 값 `자연과학대학 수학과`와 정확 일치로 비교 | `contains` 완화로 해결(측정됨) |
| 요일 값 형식 | q19, q06, q31 | `토요일`·`화요일`·`수요일`을 실제 값 `토`·`화`·`수`와 비교 | 요일 값 정규화(8-06 코드값 사전 후보) |
| 이수구분 값 형식 | q06 | `전공핵심` 대 실제 값 `전공(핵심)` | 코드값 사전(8-06) |
| 조건 해석 오류 | q15, q16, q31 | 질문 전체를 과목명에 넣음(q15), 시작 대신 종료 시간 비교(q16), 없는 시간 범위 추가(q31) | 모델·prompt |
| 과도하게 구체적인 값 | q20 | `미적분학` → `미적분학1` 정확 일치 | 모델·prompt |

- 요일 정규화(`X요일` → `X`)를 `+limit200+contains` 위에 더한 오프라인 추정치는 **14/22**다(q19 해결). 모델 재호출 없이 같은 출력으로 scratch에서 계산했으며, 커밋된 모듈의 변형에는 포함하지 않았다.
- q25는 모델 출력이 JSON이 아니었다.
- q26 선수과목에서도 모델이 `Course.prerequisites`를 만들었다(ontology 밖).

### legacy (ONTOLOGY 범위에서 수용 불가 5문항)

| 유형 | 문항 | 내용 |
|---|---|---|
| 학점 형식 불일치 | q03, q14, q17 | prompt 지시대로 `credit_hours = '3'`으로 비교했으나, 실제 값은 `3/3/0` 형식이라 0행 |
| 시간 해석 오류 | q13 | `오후 3시에 시작`을 12:00~15:00 범위로 조회 |
| key 없는 조회 | q21 | `start_time`만 조회. 화면 카드에 과목명·분반이 없다 |

### legacy가 잘한 부분

전처리의 학과명 교정이 효과적이었다. `물리학과`는 `자연과학대학 물리학과`로, `간호학과`는 `간호대학 간호학과`로 교정되어 정확 일치 조건이 성공했다(q22, q31). 이 기능은 QueryIR 경로에 없다.

## 5. 한계

1. `CORRECT_SUBSET`은 결과 상한에 도달한 경우만 인정한다. 그래도 상한 안의 결과가 우연히 조건을 만족하면, 빠뜨린 조건이 가려질 수 있다(q24의 QueryIR). 50행 상한에서 `CORRECT_SUBSET`이던 q20도 200행에서 `INCOMPLETE`로 드러났다. 그래서 `+limit200` 열이 이전 열보다 엄격한 판정이다.
2. 질문 30개와 기준 SQL은 개발자 한 명이 작성했다. 한국어 의미 해석(예: `오전` = 12시 이전, `전공` = 이수구분에 `전공` 포함)도 개발자 판단이다(L1 전).
3. 실행은 1회다. 모델은 temperature 0이지만 legacy에는 cache·재시도 경로가 있다.
4. legacy 호출은 legacy의 기존 query log에 기록되었을 수 있다(legacy 기존 동작).
5. 시간표 조건이 있는 질문의 화면 표시는 두 경로가 다르다. QueryIR 카드에는 조건에 맞는 요일의 시간만 나온다. legacy는 일치 강좌의 전체 시간표로 확장한다. key 기반 판정에는 영향이 없다.

## 6. 결정이 필요한 항목

| 항목 | 선택지 | 측정된 효과(ONTOLOGY 22) |
|---|---|---|
| 결과 limit | 모델 limit 유지 / 서버가 200으로 고정 / 모델 값이 작을 때만 존중 | 200: 판정은 더 엄격해지고, 정답 강좌를 더 많이 보여준다(`EXACT` 4→8) |
| 문자열 비교 | `equals` 유지 / 과목명·학과명·교원명·이수구분 `contains` 완화 | +limit200 대비 +3(10→13) |
| 코드값 정규화 | 요일(`X요일`→`X`) 등 값 사전(8-06) | 오프라인 추정 +1(13→14) |
| field 별칭 교정 | `Course.department` → `Department.name` 같은 서버 교정 / prompt 수정 / 유지 | 미측정(q01, q17 해당) |
| ontology 확장 | 학년(대상 학년) 등 legacy에서 자주 묻는 field 추가 | OUTSIDE_SCOPE 5문항 중 학년 2문항 해당 |

여기의 수치는 이 질문 세트의 잠정 관찰이며 성능 주장이 아니다.
