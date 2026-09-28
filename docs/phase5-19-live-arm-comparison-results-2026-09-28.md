# Phase 5-19 로컬 모델 IR·직접 SQL arm paired 비교 결과(잠정, 2026-09-28)

기준: [`docs/prompts/phase5-19-live-arm-comparison.md`](prompts/phase5-19-live-arm-comparison.md), 계약은 Phase 5-18·ADR-057. 완전 합성 fixture 3종과 개발 세트 40건만 사용했다. 질문·prompt·모델 출력·SQL·행 값은 이 문서와 report에 기록하지 않았다.

> **잠정치.** gold 40건은 모두 `NEEDS_HUMAN_REVIEW`이며 L1(gold 검토)·L3(결과 검토) 전이다. 논문의 확정 성능이나 우열 결론으로 쓰지 않는다.

> **정정(2026-09-29, Phase 5-20).** 이 실행은 `text2sql_qwen:latest`의 Modelfile에 내장된 CNU `courses` schema SYSTEM이 두 arm의 모든 호출에 주입된 조건이었다(입력 token 114개 차이로 확인). 아래 "CNU schema 재현 경향"은 모델 암기라기보다 이 SYSTEM을 따른 결과였을 가능성이 높으므로 해석으로 쓰지 않는다. 오염을 제거한 재실행 결과(IR 93, SQL 72)는 [`phase5-20-decontaminated-arm-comparison-results-2026-09-29.md`](phase5-20-decontaminated-arm-comparison-results-2026-09-29.md)를 기준으로 한다. 아래 수치는 조건 기록으로만 보존한다.

## 환경

- Windows 데스크톱, 로컬 Ollama `0.34.4`, 기본 loopback URL, timeout 60초, temperature 0, attempt당 1회·retry 없음.
- model tag `text2sql_qwen:latest`(CNU `courses` 단일 schema로 fine-tune된 기존 모델), digest `7070730bf2f29aa3e7cbb9b0b566b1c73fd93bfeab41363e4a1f128730a3cb5a`.
- IR arm prompt `query-ir-v1`(ontology semantic field만), SQL arm prompt `direct-sql-v1`(fixture Catalog의 table·column·PK·FK), 템플릿 SHA-256은 report에 기록.
- 실행: `--check`(호출 0회) 뒤 `--live-synthetic` 1회, 240 호출, 약 1분 46초. report는 Git 비추적 `.schema-adaptive/reports/p519-text2sql-qwen-20260928/`.

## 결과 (분모 = 전체 scheduled attempt 120)

| 지표 | IR arm | SQL arm |
| --- | ---: | ---: |
| 실행 성공 | 99 | 29 |
| 정답(IR: semantic 일치, SQL: 위치 무관 결과 일치) | **90** | **17** |
| IR 구조 exact | 51 | — |
| SQL 정책 통과(valid SQL) | — | 69 |
| UNKNOWN(기권) | — | 0 |

IR arm 수치는 Phase 5-13(같은 tag·prompt)과 정확히 같아 temperature 0에서 재현되었다.

paired count:

| 범위 | 둘 다 정답 | IR만 | SQL만 | 둘 다 오답 | IR 정답 | SQL 정답 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 전체(120) | 12 | 78 | 5 | 25 | 90 | 17 |
| flat(40) | 4 | 26 | 2 | 8 | 30 | 6 |
| normalized(40) | 1 | 29 | 1 | 9 | 30 | 2 |
| legacy(40) | 7 | 23 | 2 | 8 | 30 | 9 |

## 실패 구성

- SQL arm outcome: `SQL_UNKNOWN_TABLE` 51(flat 29, legacy 22), `EXECUTION_ERROR` 40(normalized 38, legacy 2), `INCORRECT_RESULT` 11, `COLUMN_COUNT_MISMATCH` 1, `CORRECT` 17.
- IR arm 실패: `UNKNOWN_FIELD` 21, `SEMANTIC_MISMATCH` 9(Phase 5-14의 기존 분류와 동일).
- 비노출 구조 진단(별도 SQL arm 120 호출, in-memory 분류, 수치가 live와 일치):
  - `SQL_UNKNOWN_TABLE` 51건 전부가 **기존 CNU schema table(`courses`·`v_course_info`·`course_schedule`)만** 참조했다.
  - `EXECUTION_ERROR` 40건 전부가 `no such column`이었다. normalized fixture는 table 검사를 통과했으나 column 이름에서 실패했다.

## 해석과 한계

- ~~관찰: 기존 fine-tuned 모델은 동적 schema를 prompt로 받아도 학습한 CNU table·column을 재현하는 경향이 강했다.~~ **정정됨:** 모델이 내장 SYSTEM으로 CNU schema를 직접 받은 조건이었으므로 암기의 근거가 아니다(Phase 5-20).
- 같은 모델에서 QueryIR 경로는 물리 schema를 모델에 노출하지 않고 compiler가 fixture별 SQL을 만들기 때문에 이 암기 문제를 우회한다. 다만 IR arm에도 `UNKNOWN_FIELD` 21건 등 별도 실패가 있다.
- 공정성 한계:
  - 모델은 CNU 단일 schema SQL로 fine-tune되었으므로 동적 schema SQL은 학습 분포 밖이다. base instruct 모델(§15.1-3)과의 비교가 있어야 "QueryIR 방식 자체"의 효과를 분리할 수 있다. 로컬에는 base tag가 아직 없다.
  - SQL arm 비교는 column 이름을 보지 않고 값만 비교해 IR arm보다 약간 관대하다(ADR-057).
  - 개발 세트 40건은 prompt 탐색(Phase 5-14)에 쓰인 세트다. 독립 holdout 30건은 사람 검토·동결 전이라 실행하지 않았다.
  - 표본이 작고 gold가 미승인이라 통계 검정은 하지 않았다.

## 부수 기록

- Phase 5-19 CLI 테스트 작성 중 SQL provider가 transport factory를 이름으로 묶어 monkeypatch를 우회한 결함이 있었고, 그 테스트 실행 1회가 존재하지 않는 합성 tag로 로컬 Ollama에 120회 요청했다. server log상 모두 `404`(약 1ms, 생성 없음)였다. factory를 module lookup으로 고치고 회귀 테스트를 추가했으며 이후 전체 테스트는 Ollama 호출이 없었다.

## 사람 관문

완전 합성 입력만 사용해 E3·L5 대상 자료가 없다. 수치는 L1/L3 전 잠정치이며 승인 상태는 바꾸지 않았다.
