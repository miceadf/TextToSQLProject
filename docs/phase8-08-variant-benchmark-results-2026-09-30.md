# Phase 8-08 결과: 합성 변형 benchmark와 규칙 기반 기준선 (2026-09-30)

> 합성 데이터만 쓴다. 변형·정답·질문은 개발자가 작성했고 L1/L3 전이다. AI mapping 제안과 비교할 기준 점수이며, 모델 성능 주장에는 쓰지 않는다.

## 1. 만든 것

- `variant_specs.py`:
  - seed: 개설강좌 12개, 학과 4개, 교원 6명, 과목 9개.
  - 12개 변형.
  - 변형마다 inspector와 같은 형태의 catalog와 정답(gold) mapping.
  - 정답은 column 주석에서 자동으로 만든다. 들어가는 것: 위치, FK 사본 허용, 변환 허용 집합, 값 사전, 기준 table.
- `variant_benchmark.py`:
  - 답안 형식 `VariantMappingAnswer`: field별 위치·변환·값 사전과 기준 table. 다음 단계 AI 제안의 출력 형식이다.
  - field 점수(DB 불필요).
  - 실행 점수(live): gold QueryIR 20개를 기존 compiler·AST policy·read-only executor로 실행하고, seed 참조 평가와 행 multiset을 비교한다.
  - 규칙 기반 기준선: 현재 aligner를 그대로 쓴다. 신뢰도 순으로 배정하고 같은 column은 중복 배정하지 않으며, 0.60 미만이면 미지원으로 둔다.
  - live 실행:
    - 로컬 PostgreSQL 전용 DB `schema_adaptive_synthetic`(사용자 승인으로 생성)의 무작위 schema에 변형을 하나씩 만든다.
    - inspect한 catalog가 spec catalog와 완전히 같은지 확인한 뒤 실행하고, 끝나면 지운다(남은 schema 0).
- 실행이 무효가 되는 조건: 오류, 정리 실패, oracle(정답 답안)이 만점이 아닌 경우. 이때 CLI는 실패로 끝난다.

## 2. 결과(`vb-run1`)

| 답안 | field 정답 | 기준 table | 질문 정답 |
|---|---:|---:|---:|
| oracle(정답) | 180/180 | 12/12 | **240/240** |
| 규칙 기반 | 163/180 | 1/12 | **205/240** |

oracle이 모든 변형에서 만점이므로, 변형·정답·참조 평가·실행 경로가 서로 맞다.

### 2.1 규칙 기반 변형별 결과

| 변형 | 축 | field | 기준 table | 질문 | 틀린 이유 |
|---|---|---:|:---:|---:|---|
| `base` | 구조 | 15/15 | X | 20/20 | – |
| `flat` | 구조 | 15/15 | O | 20/20 | – |
| `optional-schedule` | 구조 | 15/15 | X | 19/20 | 기준 table 없음 → 시간표 없는 강좌 2개 누락 |
| `credits-slash` | 형식 | 14/15 | X | 18/20 | 학점 변환 없음 → 숫자 비교가 SQL 오류 |
| `time-type` | 형식 | 13/15 | X | 16/20 | 시간 변환 없음 → 출력이 `HH:MM` 아님 |
| `category-code` | 코드값 | 14/15 | X | 16/20 | 값 사전 없음 → `전공`이 `M`과 불일치 |
| `weekday-english` | 코드값 | 14/15 | X | 17/20 | 값 사전 없음 → `화`가 `TUE`와 불일치 |
| `semester-code` | 코드값 | 14/15 | X | 18/20 | 값 사전 없음 → `2`가 `20`과 불일치 |
| `abbreviated` | 의미 | 10/15 | X | 12/20 | 이름 hint에 없는 약어(`limit_cnt`, `yoil`, `st_tm` 등) 5개 미지원 |
| `grade-trap` | 의미 | 15/15 | X | 20/20 | 학년 column을 연도로 매핑하지 않음(정답) |
| `capacity-trap` | 의미 | 14/15 | X | 18/20 | 이름이 같은 `capacity`(강의실 정원)를 수강 정원으로 선택 |
| `legacy-like` | 복합 | 10/15 | X | 11/20 | 학점·시간 변환, 요일 사전, 교원명(`professor`) 미지원, 기준 table |

축별 질문 정답:

| 축 | 질문 정답 |
|---|---:|
| 구조 | 59/60 |
| 형식 | 34/40 |
| 코드값 | 51/60 |
| 의미 | 50/60 |
| 복합 | 11/20 |

## 3. 해석

- 현재 규칙은 **이름 대응만** 한다. column 위치는 173/180으로 높지만, 다음은 하지 않는다.
  - 값 형식(변환), 코드값(사전), 행 기준(anchor)은 제안하지 않는다.
  - 이름이 같지만 뜻이 다른 column(강의실 정원)은 구분하지 못한다.
- 질문 기준으로 틀린 35개는 모두 위 네 가지 원인 중 하나다. 다음 단계 AI 제안이 줄여야 할 격차가 이것이다.
- legacy DB와 가장 닮은 `legacy-like`에서 규칙 기반은 질문 11/20이다. 7-12~8-06에서 개발자가 손으로 만든 변환·사전·anchor가 이 격차를 메운 것이다.

## 4. 한계

- 모든 것이 개발자가 작성한 합성 자료다.
- `abbreviated`의 이름 절반(`sbjt_nm`, `pnt`, `isu_gb`, `yyyy`, `hakgi`, `bunban` 등)은 기존 합성 fixture에서 왔다. 이 이름들은 ontology의 이름 hint에 이미 들어 있다. 그래서 규칙이 처음 보는 스키마보다 유리하다. AI 제안도 같은 ontology를 보므로 비교는 공정하지만, 처음 보는 스키마에 대한 주장은 할 수 없다.
- 규칙 기준선에는 sample 값이 주어지지 않는다(현재 aligner가 쓰지 않음). AI 제안은 sample 값을 쓸 수 있으므로, 비교할 때 입력 차이를 명시해야 한다.
- 질문은 gold QueryIR을 그대로 실행한다. 자연어 이해는 평가하지 않는다.
- FK 없는 관계, 1:N 값, 닫힌 목록 밖의 형식(예: `0900`)은 포함하지 않았다.
- 원자료는 `.schema-adaptive/reports/variant-benchmark/vb-run1.json`(Git 비추적)에 있다.
