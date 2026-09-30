# Phase 8-08 지침: 합성 변형 benchmark와 규칙 기반 mapping 기준선

작성일: 2026-09-30. 사용자 승인 개발 순서 3번. 사용자 승인: 로컬 PostgreSQL에 전용 DB `schema_adaptive_synthetic` 생성(2026-09-30).

## 1. 목적

AI mapping 제안(다음 단계)을 공정하게 평가할 고정 benchmark를 먼저 만든다. 같은 의미의 수강 데이터를 여러 방식으로 저장한 합성 PostgreSQL 변형을 두고, mapping 제안이 얼마나 맞는지 측정한다. 이번 단계는 benchmark와 현재 규칙 기반 aligner의 기준 점수까지다.

- benchmark는 AI 구현 전에 고정한다. AI 결과를 보고 benchmark를 고치지 않는다.
- 규칙 기반 기준선은 **현재 aligner 그대로** 측정한다. benchmark에 맞춘 새 규칙은 만들지 않는다.

## 2. 구성

### 2.1 seed

- `variant-benchmark-seed-v1`의 구성:
  - 개설강좌 12개, 학과 4개, 교원 6명, 과목 9개.
  - 연도 2개, 학기 2개, 이수구분 3개(`전공`, `교양`, `일반선택`), 요일 6개(한 글자 `월`~`토`).
- 표준 표현은 runtime 출력 형식과 같다: 학점은 숫자, 시간은 `HH:MM`, 요일은 한 글자.
- 기존 fixture seed(`MON` 등)와 40문항 세트는 바꾸지 않는다.

### 2.2 변형(12개, 축별)

| 축 | 변형 | 차이 |
|---|---|---|
| 구조 | `base` | 정규화 5 table, 영어 이름, 표준 값. 대조군 |
| 구조 | `flat` | 한 table |
| 구조 | `optional-schedule` | 개설강좌 2개에 시간표가 없음. 기준 table(anchor) 필요 |
| 형식 | `credits-slash` | 학점이 `3/3/0` 문자열 → `SLASH_FIRST_NUMERIC` |
| 형식 | `time-type` | 시간이 PostgreSQL `time` → `TIME_TO_HHMM` |
| 코드값 | `category-code` | 이수구분이 `M`·`G`·`E` → 값 사전 |
| 코드값 | `weekday-english` | 요일이 `MON`… → 값 사전 |
| 코드값 | `semester-code` | 학기가 `10`·`20` → 값 사전 |
| 의미 | `abbreviated` | 약어 이름(`tb_sbjt.sbjt_nm`, `yoil` 등) |
| 의미 | `grade-trap` | `course_year`는 학년(1~4)이고 연도 column이 없다 → 연도 미지원 |
| 의미 | `capacity-trap` | `capacity`는 강의실 정원이고 수강 정원은 `enroll_limit` |
| 복합 | `legacy-like` | 학점 문자열, `time`, 영어 요일, 시간표 없는 강좌, 학년 column |

### 2.3 정답(gold)

변형 spec의 column 주석에서 자동으로 만든다. field마다 다음을 둔다.

- 위치: table·column, 또는 미지원. 다른 table의 같은 값을 가리키는 FK column(예: 개설강좌의 과목 코드)도 정답으로 인정한다.
- 값 변환: 허용 집합. 한 글자 요일 column은 변환 없음과 `KOREAN_WEEKDAY`를 모두 인정한다.
- 값 사전: 저장 값 → 표준 값.
- 변형 단위 기준 table: 개설강좌 table. 한 table 변형은 그 table 또는 없음.

## 3. 채점

### 3.1 mapping 답안

답안(`VariantMappingAnswer`)은 AI 제안의 출력 형식이기도 하다.

- field별 위치, 값 변환, 값 사전.
- 기준 table.

### 3.2 field 단위 점수(DB 불필요)

- column, 변환, 값 사전, 세 가지 모두의 정답 수를 셌다(변형당 15 field).
- 값 사전은 두 조건을 모두 만족해야 정답이다.
  - 정답의 모든 표준 값이 제안 사전에서 정답 저장 값으로 풀린다.
  - 제안 사전의 모든 저장 값이 정답 저장 값 안에 있다.
- 기준 table은 따로 센다.

### 3.3 실행 점수(live)

- 고정 gold QueryIR 20개를 쓴다. 모두 과목 코드와 분반을 select하여 개설강좌 단위 결과가 되게 한다.
- 답안으로 runtime profile을 만들고, 기존 compiler·AST policy·read-only executor로 실행한다.
- 결과 행 multiset이 seed 참조 평가(compiler와 무관한 Python 평가)와 같으면 정답이다.
- 정답에서 미지원인 field를 쓰는 질문은 거절(`MAPPING_MISSING`)이 정답이다.
- runtime이 답안을 거부하면(같은 column 중복, 타입 불일치 등) 그 변형의 질문은 모두 오답이다.
- 정답 답안(oracle)은 모든 변형에서 field·실행 모두 100%여야 한다. 이것이 benchmark 자체의 검증이다.

### 3.4 규칙 기반 기준선

- 현재 `RuleBasedSchemaAligner`를 쓴다(이름·타입, sample 없음).
- field마다 상위 후보 3개 중 이미 쓰인 column이 아닌 첫 후보를 신뢰도 순으로 고른다. 신뢰도가 검토 기준 0.60 미만이면 미지원으로 둔다.
- 변환, 값 사전, 기준 table은 제안하지 않는다(규칙에 없음).

## 4. 실행과 저장

- 오프라인: `python -m backend.adaptive.evaluation.variant_benchmark --check`.
  - spec 검증, gold 생성, oracle과 규칙 기준선의 field 점수를 계산한다.
  - DB 연결은 없다.
- live: `--live-synthetic`. 두 조건이 필요하다.
  - `SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN`: 7-03과 같은 loopback, `schema_adaptive_synthetic` 전용 검증.
  - label.
- live 실행의 흐름:
  - 변형마다 무작위 schema를 만들고 끝나면 지운다.
  - inspect한 catalog가 spec catalog와 구조적으로 같은지 확인한다.
  - oracle과 규칙 답안을 실행한다.
  - 규칙 답안은 live catalog로 다시 만들어 오프라인 답안과 같은지 확인한다.
- 결과 JSON은 `.schema-adaptive/reports/variant-benchmark/`(Git 비추적)에 둔다. stdout에는 집계만 낸다.
- 합성 자료이므로 결과 문서에는 변형별 점수와 실패 원인을 적는다.

## 5. 범위 밖

- AI 제안 구현(다음 단계).
- sample 값을 쓰는 규칙 추가.
- FK 없는 관계, 1:N 값, 표현할 수 없는 형식(예: `0900`).
- SQLite 경로.
- 실제 DB.
- 질문의 자연어 이해. 이번 평가는 gold QueryIR을 그대로 실행하므로 모델 호출이 없다.

## 6. 제약

- 합성 전용 DB와 무작위 schema만 쓰고 정리한다. legacy DB는 건드리지 않는다.
- DSN과 driver 오류 문구는 출력하지 않는다.
- 실행은 기존 read-only executor를 거친다.
- 사람 관문을 승인으로 표시하지 않는다. gold는 개발자 작성이며 L1/L3 전이다.
- push하지 않는다.
