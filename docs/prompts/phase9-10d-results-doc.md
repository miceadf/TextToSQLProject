# Phase 9-10 결과 기록 지침 (문서만)

코드는 바꾸지 않는다. 아래 측정 결과를 문서로 남긴다. 수치와 분류는 Claude가 측정 기록과 실패 문항 재생성으로 확인한 것이다. 새로 측정하거나 모델을 부르지 않는다. 커밋하지 않는다.

## 만들 파일

`docs/phase9-10-direct-sql-results-2026-10-12.md` (기존 결과 문서 문체를 따른다).

## 측정 조건 (2026-10-12, 사용자 승인, 한 번)

- 모델: 두 방식 모두 로컬 `qwen2.5-coder:7b-instruct`, temperature 0, 질문당 호출 한 번. OpenAI 측정은 사용자 결정으로 제외.
- 테스트 서버 8124(측정 뒤 종료). 사용자 서버는 건드리지 않음.
- 직접 SQL(`direct-sql-pg-v1`): 등록만 한 DB. 레거시는 public schema를 `p910-legacy`로 새로 등록(table 8·column 38·FK 12, `student`·`enrollment` 포함, 읽기만), `other_school`은 `p910-other`(table 6·column 30·FK 6), 리허설은 기존 `rehearsal-school`. 게시·관리자 작업 없음.
- QueryIR: 레거시는 9-08과 같은 `legacy-view` profile(기준 SQL은 `p910-legacy` 연결로 실행, CLI에 `--reference-database` 추가). 리허설은 게시된 `rehearsal-school`. `other_school` QueryIR은 다시 돌리지 않고 9-08 결과(10/12, 같은 모델·질문, 9-08 이후 QueryIR runtime 변경 없음)를 쓴다. 9-08 기록에는 응답 시간이 없다.
- 채점: 두 방식 같은 채점기. 거절은 `blind_eval`과 같은 규칙(`UNSUPPORTED`, `NOT_UNDERSTOOD`). 레거시 QueryIR 26은 9-08의 23(정확 18 + 상한 부분 집합 5)에 거절 정답 3을 더한 것과 같다.
- 기록: `.schema-adaptive/direct-sql-pg/run-001`(직접·레거시), `run-002`(직접·other_school), `run-003`(QueryIR·레거시), `.schema-adaptive/blind-evals/rehearsal-v1/run-007`(직접), `run-008`(QueryIR).

## 결과

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

## 직접 SQL 실패 원인 (약 39건, 실패 문항만 같은 조건으로 다시 생성해 SQL 확인)

temperature 0이지만 재생성이 완전히 같지는 않았다(other_school q12는 기록은 거절, 재생성은 SQL). 그래서 개수는 "약"으로 적는다.

1. SQL 작성 실수 약 11: JOIN 뒤 table 없이 쓴 `subject_code`가 모호함 9(레거시 q01·q14·q15·q17·q18·q23·q24·q29·q30, 조건은 대부분 맞음), 없는 column 2(q06 `c.category`, q20 `cnu_courses.subject_name`).
2. 지시 미준수 4: 과목 코드를 `AS course_code`로 내지 않음(레거시 q02·q04·q13·q16, 조건은 그럴듯함).
3. 과목 코드 식별 오인 약 9: 리허설에서 `lecture_id`(정수 PK)를 과목 코드로 냄(r02~r07·r09·r10·r12). 조건은 대부분 맞음(예: r09 `cap >= 60`, r10 `grade_year = 1`). 이 DB의 과목 코드는 분반이 붙은 `lec_no`에서 나오며, QueryIR은 관리자 mapping·변환이 이를 알려 준다. "과목 코드가 무엇인지"는 DB별 지식이다.
4. 값 위치·형식 못 찾음 약 5: 과목명 질문. 레거시 q07(데이터베이스)·q08(딥러닝)·q21(컴퓨터프로그래밍)은 거절, other q08·리허설 r08은 `과목명 = '데이터베이스'` 정확 일치. 과목명 column은 서로 다른 값이 500개를 넘거나 값 힌트에 잡히지 않는다(값 힌트는 값 500개 이하 글자 column만 색인).
5. 약어·코드 의미(값 사전 없음) 6: other q01(소웨)·q02(전전)·q03(기공)·q06(2학기 = `hakgi_cd` 20), 리허설 r01(소웨)·r14(전전). 설계상 직접 SQL에는 관리자 값 사전을 주지 않았다.
6. 의미 오해 약 3~4: 레거시 q03(학점 `3/0/0` 정확 일치), q28(대상 학년 `course_year`를 개설 연도로 씀), other q12(1학년 대상을 학기 코드로).

## 해석 (잠정, 개발용)

- 이 개발 질문(56개)은 QueryIR 규칙을 맞춘 질문이라 QueryIR에 유리하다. L1/L3 전 잠정치이며 적응성 근거가 아니다. 판정은 팀원 DB 첫 실행이다.
- 등록만 한 직접 SQL + 7B는 17/56, QueryIR은 42/56이다.
- 직접 SQL 실패의 절반 이상(1·2·3, 약 24건)은 SQL 작성 습관과 과목 코드 식별이다. 4는 사용자가 제안한 "DB 값 전체 검색" 아이디어가 겨냥하는 경우다. 5는 값 사전 같은 DB별 지식이 필요하다.
- 직접 SQL은 응답이 약 2배 빠르다(QueryIR은 HTTP 왕복·JSON 출력·compile 포함).
- 이 실패 사례로 직접 SQL을 고친 뒤 같은 56개로 다시 재면 그 수치는 개발 질문에 맞춘 것이다. 개선 여부는 팀원 DB로 판단한다.
- 제품 구조 변경은 사용자 결정 사항이다. 이번 결과로 어떤 결정도 내리지 않았다.

## 함께 고칠 문서

- `docs/schema-adaptive-text-to-sql-plan.md`: ADR-100에 "결과" 항목(위 표 요약과 결과 문서 링크), 로드맵 9-10의 live 측정 항목 완료, 변경 이력에 2026-10-12 9-10 측정 항목, 15.1.1에 `--reference-database` 한 줄.
- `docs/human-validation-checkpoints.md` 9-10 메모에 결과 한 줄과 "개발 질문·L1/L3 전 잠정치, 관문 승인 없음".
- `docs/next-session-handoff.md` 9-10 줄에 결과 한 줄과 남은 등록 `p910-legacy`·`p910-other`(PENDING_REVIEW, 게시 안 함)를 적는다.
- 끝나면 바꾼 파일을 짧게 적는다.
