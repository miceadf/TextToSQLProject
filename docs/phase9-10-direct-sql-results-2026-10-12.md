# Phase 9-10 결과: 등록 PostgreSQL 직접 SQL과 QueryIR 비교 (2026-10-12)

지침: [phase9-10d-results-doc.md](prompts/phase9-10d-results-doc.md) · 설계: 계획 문서 ADR-100 · 앞 단계: [9-08](phase9-08-chat-model-choice-results-2026-10-12.md)

> 아래 수치와 실패 분류는 Claude가 기존 측정 기록과 실패 문항 재생성으로 확인해 제공한 것이다. 이번 작업은 문서 기록만 하며 새 측정·모델 호출·서버 조작·테스트·커밋은 하지 않았다.
>
> 개발 질문 56개는 QueryIR 규칙을 맞춘 질문이라 QueryIR에 유리하다. L1/L3 전 잠정치이며 적응성 근거가 아니다. 판정 근거는 에이전트가 미리 보지 않은 팀원 DB와 고정 질문의 첫 실행이다. 어떤 사람 관문도 승인하지 않았다.

## 1. 측정 조건

- 2026-10-12 사용자 승인으로 한 번 측정했다. 두 방식 모두 로컬 `qwen2.5-coder:7b-instruct`, temperature 0, 질문당 모델 호출 한 번이다. OpenAI 측정은 사용자 결정으로 제외했다.
- 테스트 서버는 8124였고 측정 뒤 종료했다. 사용자 서버는 건드리지 않았다.
- 직접 SQL(`direct-sql-pg-v1`)은 등록만 한 DB를 쓴다. 관리자 mapping·변환·값 사전은 주지 않았다. 등록 catalog, column별 예시 최대 10개/40자, 실제 값 힌트를 쓴다.
- QueryIR은 기존 profile 또는 게시 mapping·값 사전·compiler 경로를 쓴다. 두 방식은 온보딩 비용이 다른 조건이다.

| 질문 세트 | 직접 SQL 등록 | QueryIR 조건 |
| --- | --- | --- |
| 레거시 30 | public schema를 `p910-legacy`로 새로 등록(table 8·column 38·FK 12, `student`·`enrollment` 포함), 읽기만 | 9-08과 같은 `legacy-view` profile. 기준 SQL은 `p910-legacy` 연결로 실행(CLI에 `--reference-database` 추가) |
| `other_school` 12 | `p910-other`(table 6·column 30·FK 6) | 다시 돌리지 않고 9-08 결과 10/12 재사용. 같은 모델·질문이며 9-08 이후 QueryIR runtime 변경 없음. 9-08 기록에는 응답 시간이 없음 |
| 리허설 14 | 기존 `rehearsal-school` | 게시된 `rehearsal-school` |

새 등록 `p910-legacy`·`p910-other`는 `PENDING_REVIEW`로 남겼고 게시·관리자 작업은 하지 않았다.

두 방식은 같은 채점기를 쓴다. 거절은 `blind_eval`과 같은 규칙(`UNSUPPORTED`, `NOT_UNDERSTOOD`)으로 채점했다. 레거시 QueryIR 26은 9-08의 23(정확 18 + 상한 부분 집합 5)에 거절 정답 3을 더한 것과 같다. 9-08의 23에서 정확도가 3문항 개선되었다는 뜻은 아니다.

## 2. 결과

| 질문 세트 | 직접 SQL 맞음 | QueryIR 맞음 |
| --- | ---: | ---: |
| 레거시 30 | 10 | 26 |
| `other_school` 12 | 5 | 10 (9-08) |
| 리허설 14 | 2 | 6 |
| 합 56 | 17 | 42 |

| 지표 | 직접 SQL (레거시 / other / 리허설) | QueryIR (레거시 / 리허설) |
| --- | --- | --- |
| 조용한 오답 | 2 / 1 / 10 | 2 / 3 (other 9-08: 틀린 2문항) |
| 실행 실패 | 15 / 0 / 0 (모호한 column 등 11, `course_code` 별칭 없음 4) | 0 / 0 |
| 정책 거절·생성 실패 | 0 | 0 |
| 거절이 맞음 / 답할 수 있는데 거절 | 0/3, 0/6, 1/2 | 3/2, 1/5 |
| 상한(200행) 도달 | 0 | 5 / 0 |
| 지연 중앙값 / p90 (ms) | 674/1104, 514/880, 508/844 | 1469/2030, 1231/1494 |

QueryIR `other_school`의 지연은 9-08 기록에 없어 비교하지 않는다. QueryIR 지연은 HTTP 왕복·JSON 출력·compile을 포함한다.

## 3. 직접 SQL 실패 원인

직접 SQL 오답은 39건(56 − 17)이다. 아래 분류는 실패 문항만 같은 조건으로 다시 생성해 SQL을 확인한 것이다. temperature 0이지만 재생성이 완전히 같지는 않았다(`other_school` q12는 기록은 거절, 재생성은 SQL). 그래서 분류별 개수는 대략적인 값이며 원래 측정의 채점 결과를 대체하지 않는다.

1. **SQL 작성 실수 약 11건.** JOIN 뒤 table 없이 쓴 `subject_code`가 모호한 경우 9건(레거시 q01·q14·q15·q17·q18·q23·q24·q29·q30, 조건은 대부분 맞음), 없는 column 2건(q06 `c.category`, q20 `cnu_courses.subject_name`).
2. **지시 미준수 4건.** 과목 코드를 `AS course_code`로 내지 않음(레거시 q02·q04·q13·q16, 조건은 그럴듯함).
3. **과목 코드 식별 오인 약 9건.** 리허설에서 `lecture_id`(정수 PK)를 과목 코드로 냄(r02~r07·r09·r10·r12). 조건은 대부분 맞음(예: r09 `cap >= 60`, r10 `grade_year = 1`). 이 DB의 과목 코드는 분반이 붙은 `lec_no`에서 나오며, QueryIR은 관리자 mapping·변환이 이를 알려 준다. “과목 코드가 무엇인지”는 DB별 지식이다.
4. **값 위치·형식 못 찾음 약 5건.** 과목명 질문. 레거시 q07(데이터베이스)·q08(딥러닝)·q21(컴퓨터프로그래밍)은 거절, other q08·리허설 r08은 `과목명 = '데이터베이스'` 정확 일치. 과목명 column은 서로 다른 값이 500개를 넘거나 값 힌트에 잡히지 않는다(값 힌트는 값 500개 이하 글자 column만 색인).
5. **약어·코드 의미(값 사전 없음) 6건.** other q01(소웨)·q02(전전)·q03(기공)·q06(2학기 = `hakgi_cd` 20), 리허설 r01(소웨)·r14(전전). 설계상 직접 SQL에는 관리자 값 사전을 주지 않았다.
6. **의미 오해 약 3~4건.** 레거시 q03(학점 `3/0/0` 정확 일치), q28(대상 학년 `course_year`를 개설 연도로 씀), other q12(1학년 대상을 학기 코드로).

## 4. 해석 (잠정, 개발용)

- 등록만 한 직접 SQL + 7B는 17/56, QueryIR은 42/56이다. 이 질문은 QueryIR 규칙을 맞춘 개발 질문이며 L1/L3 전 잠정치다. 적응성 판정은 팀원 DB 첫 실행으로 한다.
- 직접 SQL 실패의 절반 이상(위 1·2·3, 약 24건)은 SQL 작성 습관과 과목 코드 식별이다. 4는 사용자가 제안한 “DB 값 전체 검색” 아이디어가 겨냥하는 경우다. 5는 값 사전 같은 DB별 지식이 필요하다.
- 응답 시간을 기록한 레거시·리허설에서 직접 SQL은 약 2배 빠르다. QueryIR 지연은 HTTP 왕복·JSON 출력·compile을 포함하므로 모델 생성 시간만의 비교는 아니다.
- 이 실패 사례로 직접 SQL을 고친 뒤 같은 56개로 다시 재면 그 수치는 개발 질문에 맞춘 것이다. 개선 여부는 팀원 DB로 판단한다.
- 제품 구조 변경은 사용자 결정 사항이다. 이번 결과로 어떤 결정도 내리지 않았다. Legacy 기준선은 유지한다.

## 5. 측정 기록

원자료는 Git 밖의 private 경로에 있다. 이 문서에는 제공된 요약과 실패 분류만 기록한다.

| 경로 | 방식·질문 세트 |
| --- | --- |
| `.schema-adaptive/direct-sql-pg/run-001` | 직접 SQL·레거시 |
| `.schema-adaptive/direct-sql-pg/run-002` | 직접 SQL·`other_school` |
| `.schema-adaptive/direct-sql-pg/run-003` | QueryIR·레거시 |
| `.schema-adaptive/blind-evals/rehearsal-v1/run-007` | 직접 SQL·리허설 |
| `.schema-adaptive/blind-evals/rehearsal-v1/run-008` | QueryIR·리허설 |

`other_school` QueryIR은 [9-08 결과 문서](phase9-08-chat-model-choice-results-2026-10-12.md) §3.3의 10/12를 재사용한다.
