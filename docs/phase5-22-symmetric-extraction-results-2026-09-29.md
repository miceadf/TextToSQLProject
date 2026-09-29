# Phase 5-22 대칭 추출 규칙으로 두 모델 IR·직접 SQL arm 재실행 결과(잠정, 2026-09-29)

기준: [`docs/prompts/phase5-22-symmetric-fence-extraction.md`](prompts/phase5-22-symmetric-fence-extraction.md). 완전 합성 fixture 3종과 개발 세트 40건만 사용했고 질문·prompt·출력·SQL·행 값은 기록하지 않았다.

> **잠정치.** gold 40건은 `NEEDS_HUMAN_REVIEW`(L1·L3 전)이며 확정 성능·우열 결론으로 쓰지 않는다.

## 조건

- 두 arm 모두 같은 단일 code fence 규칙: SQL arm `direct-sql-extract-v1`, IR arm `single-fence-v1`(같은 탐지 함수, fence가 정확히 1개일 때만 안쪽 사용, 그 외 보정 없음). runtime은 바뀌지 않았다.
- fine-tune: `text2sql_qwen:latest`, `alpaca-raw-no-system-v1`(system 없음).
- base: `qwen2.5-coder:7b-instruct`, `model-template`(일반 기본 system `You are Qwen, created by Alibaba Cloud. You are a helpful assistant.`).
- `query-ir-v1`·`direct-sql-v1`, temperature 0, timeout 60초, 호출 1회·retry 없음, 모델당 240 호출(약 1분 30초·2분). report는 Git 비추적 `.schema-adaptive/reports/p522-*`.

## 결과 (분모 120, 괄호는 flat / normalized / legacy 각 40)

| 모델 | IR 정답 | SQL 정답 | 둘 다 | IR만 | SQL만 | 둘 다 오답 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| fine-tune | 93 (31/31/31) | 72 (30/11/31) | 68 | 25 | 4 | 23 |
| base | 78 (26/26/26) | 90 (34/24/32) | 70 | 8 | 20 | 22 |

- fine-tune 결과는 Phase 5-20과 모든 수치가 같다(fence 없는 JSON을 내므로 규칙 영향 없음).
- base IR: 실행 성공 84, 구조 exact 36, cross-schema 일관성 28/40. 실패는 출력 구조 무효 24(fence 안쪽 JSON이 QueryIR shape가 아님, variant당 8), `UNKNOWN_FIELD` 9, `INVALID_VALUE_TYPE` 3, `SEMANTIC_MISMATCH` 6.
- base SQL: 실행 성공 102, 정책 통과 108, 기권 12(flat 2·normalized 6·legacy 4), `INCORRECT_RESULT` 12, `EXECUTION_ERROR` 6(normalized).

## 관찰(잠정)

1. **IR arm은 두 모델 모두 schema 구조와 무관하게 같은 정답 수**를 냈다(31·31·31, 26·26·26). prompt에 물리 schema가 없고 compiler가 JOIN을 만들기 때문이다.
2. **직접 SQL arm은 정규화 schema에서 떨어졌다**(fine-tune 30→11, base 34·32→24).
3. base 모델에서는 **단일 table schema에서 직접 SQL이 IR보다 높았고**(flat 34 대 26, legacy 32 대 26), **정규화 schema에서는 비슷했다**(24 대 26). 전체로는 SQL 90 대 IR 78이다.
4. fine-tune 모델의 큰 IR 우위(93 대 72, normalized 31 대 11)는 QueryIR 방식의 효과와 함께 **CNU 단일 schema fine-tune이 동적 schema SQL을 약화시킨 효과**가 섞인 결과로 보인다. base의 SQL(90)이 fine-tune의 SQL(72)보다 높다.
5. base 모델의 IR 실패 상당수(24)는 QueryIR JSON shape를 지키지 못한 출력이다. `query-ir-v1` prompt는 fine-tune 모델에서만 탐색되었으므로(Phase 5-14) base 모델에 불리할 수 있다.

## 해석과 한계

- 현재 근거로 "QueryIR이 직접 SQL보다 정확하다"는 일반 주장은 할 수 없다. 방어 가능한 관찰은 **QueryIR이 schema 구조 변화에 대해 정확도가 일정하고, 직접 SQL은 정규화 schema에서 떨어진다**는 점이며, 이는 schema 적응성 주장과 맞닿아 있다.
- system 조건이 다르다(fine-tune은 system 없음, base는 일반 기본 system).
- fixture는 같은 6개 개설강좌의 구조 변형 3종뿐이고, 개발 세트 40건은 prompt 탐색에 쓰였다. holdout 30건 미실행.
- SQL arm은 column 이름을 비교하지 않아 IR arm보다 약간 관대하다(ADR-057).
- 모델당 1회 실행, 통계 검정 없음.

## 사람 관문

완전 합성 입력만 사용했다(E3·L5 대상 없음). 수치는 L1/L3 전 잠정치이며 승인 상태는 바꾸지 않았다.
