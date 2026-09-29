# Phase 7-17 지침: 기준 table 보존 LEFT JOIN과 legacy 기본 table 매핑

작성일: 2026-09-29. 사용자 결정(개발 순서 1번, B안).

## 1. 목적

지금 compiler는 FK 경로를 INNER JOIN으로만 잇는다. 정규화된 실제 스키마에서는 "시간표가 없는 강좌"처럼 선택적 관계가 흔하다. INNER JOIN만 쓰면 이런 행이 오류 없이 결과에서 빠진다.

legacy DB는 view(`v_course_info`)가 이 문제를 LEFT JOIN으로 숨기고 있다(시간표 없는 개설강좌 632개). 7-17은 view 없이 기본 table에 직접 매핑해도 같은 답이 나오게 한다.

- 이후 AI mapping 비교 실험에서 mapping 오류와 JOIN 한계가 섞이지 않게 한다.
- 이 단계는 관계 해석 기능만 추가한다. 모델·prompt·챗봇 정책은 바꾸지 않는다.

## 2. 핵심 결정

### 2.1 결과 행의 기준 table(anchor)을 profile이 선언한다

- private profile `postgres-runtime-profile-v3`는 v2 key에 `anchor_table`을 더한다. 값은 문자열이며 필수다.
- v1·v2 형식과 동작은 바꾸지 않는다. v2 profile에 `anchor_table`이 있으면 거부한다(엄격 key 집합).
- `anchor_table`의 의미: 결과 한 행은 이 table의 한 행(및 그에 딸린 자식 행)이다. legacy에서는 개설강좌 table이다.

### 2.2 anchor가 있으면 모든 JOIN이 anchor에서 시작하는 LEFT JOIN이다

- 계획: anchor를 required table에 항상 포함하고, JOIN 계획의 시작점으로 고정한다. 나머지는 기존 최소 FK 경로 탐색 그대로다.
- SQL: 모든 단계를 `LEFT JOIN`으로 쓴다.
  - 부모 방향(자식→부모 FK)에서는 FK 무결성이 있으면 INNER와 결과가 같다.
  - 자식 방향(부모→자식)에서는 자식이 없는 anchor 행이 NULL과 함께 남는다.
  - 즉 "anchor 행은 모두 남고, 관련 값은 있으면 붙는다"가 된다.
- WHERE 조건은 그대로다. LEFT JOIN된 column에 조건이 걸리면 NULL 행은 빠진다. 이는 view에서 같은 조건을 쓸 때와 같은 의미다.
- anchor가 없는 profile(SQLite 경로, 기존 PostgreSQL profile)은 INNER JOIN 그대로다.

### 2.3 AST policy는 모양을 정확히 확인한다

- anchor 모드:
  - FROM은 anchor table이어야 한다.
  - 모든 JOIN은 수식어 없는 `LEFT JOIN`이어야 한다.
  - `INNER`, `LEFT OUTER`, `RIGHT`, `FULL`, `CROSS`, `NATURAL`, `USING`은 거부한다.
- 비 anchor 모드: 지금처럼 기본 JOIN 또는 `INNER JOIN`만 허용한다. `LEFT`는 거부한다.
- policy는 compiler와 독립적으로 JOIN 계획을 다시 세운다. anchor도 같은 profile 값에서 받는다.

### 2.4 시작 시 경로 모호성을 막는다

- anchor profile은 시작 검증에서 두 가지를 확인한다.
  - anchor table이 catalog에 있고, 매핑된 table 중 하나여야 한다.
  - 매핑된 table의 공집합이 아닌 모든 부분집합(최대 8개 table)에 대해 anchor 고정 JOIN 계획이 하나로 정해져야 한다.
- 하나라도 AMBIGUOUS이거나 연결되지 않으면 context를 만들지 않는다(fail closed).
- legacy DB에는 같은 table 쌍을 잇는 다른 경로(예: 필수과목 table)가 있다. anchor를 고정하면 경로가 하나로 정해져야 한다.

### 2.5 legacy 기본 table profile

- `legacy-base`(label `Legacy DB tables (QueryIR)`, DSN 변수 `SCHEMA_ADAPTIVE_POSTGRES_DSN_LEGACY_BASE`)를 추가한다.
- anchor는 개설강좌 table이다.
- view profile과 같은 12개 field를 매핑한다.
  - 과목 field는 과목 table, 학과명은 학과 table, 교원·분반·정원·신청은 개설강좌 table, 요일·시간은 시간표 table이다.
  - 변환 4개와 미지원 3개도 view profile과 같다.
- 기본 table에는 학과 코드 column이 있다. 하지만 이번 단계는 view와 같은 조건에서 JOIN 차이만 보려는 것이므로 `Department.code`는 미지원으로 유지한다. 이 판단은 결과 문서에 적는다.
- `legacy_view_profile.py`는 spec 구조로 일반화한다.
  - CLI에 `--target view|base`를 둔다. 기본값은 `view`다.
  - 기존 view 동작, 오류 code, 파일 경로는 바꾸지 않는다.
  - 등록 전 각 매핑 column의 table 존재와 선언 타입을 확인한다.

### 2.6 dev launcher와 비교 도구

- `dev:all` launcher:
  - 두 profile이 각각 있으면 legacy DSN을 두 DSN 변수에 in-process로 넣는다.
  - 상태는 profile별로 한 줄에 표시한다.
  - 한 profile이 잘못되어도 다른 profile과 Ollama는 계속 쓴다.
- 비교 도구에 `--queryir-database legacy-view|legacy-base`를 추가한다(기본 `legacy-view`). 실행 기록에 대상 DB를 남긴다.

## 3. 검증

1. 단위 테스트:
   - planner anchor 고정: 모호성 해소, 알 수 없는 anchor.
   - compiler LEFT JOIN 출력.
   - policy 허용·거부 모양 전체.
   - v3 profile 엄격 load, v2에 추가 key가 있으면 거부.
   - 시작 시 모호성 거부.
   - base spec 등록·smoke, launcher 두 profile 상태, 비교 도구 옵션.
2. 실제 legacy DB 동등성:
   - base profile로 컴파일한 "카드 전체 field, 조건 없음" 관계를 조회한다. view profile의 같은 관계와 행 multiset이 같아야 한다(6,846행).
   - 조건은 두 경로에서 같은 column 값과 같은 변환에 대한 행 단위 함수다. 따라서 전체 관계가 같으면 어떤 조건에서도 결과가 같다.
   - 이 검증 script는 scratchpad에 두고, 결과만 문서에 기록한다.
3. 같은 30문항 live 비교를 `legacy-base`로 다시 실행한다.
   - 모델 출력이 7-16과 같은지 확인한다.
   - 판정 분포가 view와 같은지 확인한다.
   - LIMIT 200에 걸리는 문항은 ORDER BY가 없어서 부분집합이 달라질 수 있다. 이 차이는 따로 표시한다.
4. 전체 pytest, compileall, `git diff --check`를 실행한다.

## 4. 범위 밖

- 학과 코드 추가 매핑과 ontology 확장.
- anchor 자동 추론. 이번에는 profile에 명시한다.
- nullable FK의 부모 방향 INNER 선택.
- 다중 anchor와 self join.
- SQLite 경로의 LEFT JOIN.
- 결과 정렬(ORDER BY).
- 모델·prompt 변경.

## 5. 지켜야 할 제약

- private profile과 비교 원자료는 Git 비추적 경로에만 둔다.
- DSN·SQL·물리 이름을 브라우저나 stdout에 내보내지 않는다.
- 모델 호출은 1회이며 raw SQL fallback은 없다.
- 실행은 read-only이고 timeout과 행 상한 200을 유지한다.
- legacy 시스템은 바꾸지 않는다.
- 사람 관문(E3, L1–L5)은 승인으로 표시하지 않는다.
- push하지 않는다.
