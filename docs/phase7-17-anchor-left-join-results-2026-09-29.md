# Phase 7-17 결과: 기준 table 보존 LEFT JOIN과 legacy 기본 table 매핑 (2026-09-29)

> 기능 검증 결과다. 챗봇 비교 수치는 7-15·7-16과 같은 개발자 작성 30문항·기준이며, L1 전 잠정치다.

## 1. 한 일

- private PostgreSQL profile에 `postgres-runtime-profile-v3`(v2 + 필수 `anchor_table`)를 추가했다. 결과 한 행이 어느 table의 한 행인지를 선언한다.
- anchor가 있으면 JOIN 계획은 항상 anchor에서 시작하고, compiler는 모든 단계를 `LEFT JOIN`으로 쓴다. anchor 행은 관련 행이 없어도 남는다.
- AST policy는 anchor 모드에서 수식어 없는 `LEFT JOIN`만 허용한다. `INNER`, `LEFT OUTER`, `RIGHT`, `FULL`, `NATURAL`, anchor가 아닌 FROM은 거부한다. anchor가 없는 기존 경로는 지금처럼 기본·`INNER JOIN`만 허용하며 `LEFT`는 거부한다.
- 시작 검증은 매핑된 table의 모든 조합에서 anchor 고정 JOIN 경로가 하나로 정해지는지 확인한다. 아니면 DB를 등록하지 않는다.
  - legacy DB에서 anchor 없이 과목+학과를 물으면 경로가 두 개(개설강좌 경유, 필수과목 table 경유)로 모호하다. anchor를 개설강좌 table로 두면 하나로 정해진다.
- `legacy-base` profile(`Legacy DB tables (QueryIR)`)을 등록했다.
  - 기본 table 4개에 view profile과 같은 12개 field, 변환 4개, 미지원 3개를 매핑했다.
  - 학과 코드 column은 기본 table에 있지만, JOIN 차이만 보려고 이번에는 미지원으로 유지했다.
- `dev:all` launcher는 두 legacy profile을 각각 켜고, 하나가 실패해도 나머지를 유지한다.
- 비교 도구에 `--queryir-database`를 추가했다.

## 2. 동등성 검증(실제 legacy DB)

두 validated context로 같은 QueryIR을 컴파일·policy 검증하고, 검증 목적으로만 행 상한을 풀어 전체 결과를 비교했다(scratch script, 런타임 경로 아님).

| 확인 | view | 기본 table + LEFT JOIN | 결과 |
|---|---:|---:|---|
| 카드 field 전체, 조건 없음(행 multiset) | 6,846 | 6,846 | 같음 |
| 7-16 답변 22문항의 실행 조건 재실행(상한 없음) | – | – | 22/22 같음 |
| 시간표 없는 행 | 632 | 632 | 같음 |

비교용 반례: 같은 기본 table을 anchor 없이 INNER JOIN으로 읽으면 다음과 같다.

- 전체가 6,214행이다. 시간표 없는 632행이 빠진다.
- 22문항 중 6문항(q03, q05, q09, q11, q18, q24)의 답이 달라진다.

## 3. 같은 30문항 live 비교(`p717-base-run1`)

- 조건: legacy 챗봇과 QueryIR(`legacy-base`), 7-16과 같은 질문 세트.
- 모델 출력: 30/30 문항이 7-16(view)과 같았다.
- legacy SQL: 30/30 같았다.
- 판정: 30/30 같았다.
- 결과 key 집합: 30/30 같았다.
- ONTOLOGY 수용 가능 문항: QueryIR 14/22, legacy 17/22(7-16과 같음).
- 미지원 field 거절: 3/3.
- 응답 시간 중앙값: QueryIR 0.63초(view 0.66초), legacy 0.86초.

view 없이 기본 table만으로 view 경로와 같은 답을 낸다. 모델 prompt는 매핑된 field 이름에만 의존하므로 물리 구조가 달라도 바뀌지 않았다.

## 4. 한계

- anchor는 profile에 명시한다. 자동 추론은 하지 않는다.
- anchor 모드에서는 부모 방향 JOIN도 LEFT다. FK 무결성이 깨진 행(부모 없는 자식)은 NULL 값으로 남는다. legacy DB에는 그런 행이 0개였다.
- 결과 정렬(ORDER BY)이 없으므로, 200행 상한에 걸리는 질문은 실행 계획에 따라 부분집합이 달라질 수 있다. 이번 실행에서는 달라지지 않았다.
- SQLite 경로와 anchor 없는 PostgreSQL profile은 INNER JOIN 그대로다.
- 비교 도구는 기준 SQL을 선택한 QueryIR 대상과 같은 DSN 변수로 실행한다(리뷰 지적 반영).
  - legacy 챗봇이 쓰는 DB는 legacy backend의 자체 설정이므로 도구가 확인할 수 없다.
  - 이번 실행에서는 세 곳 모두 같은 `DATABASE_URL`을 썼다.
- 원자료는 `.schema-adaptive/reports/phase7-15/p717-base-run1.json`(Git 비추적)에 있다.
