# Phase 5-23 schema 변형 5종에서 두 모델 IR·직접 SQL arm 일관성 결과(잠정, 2026-09-29)

기준: [`docs/prompts/phase5-23-schema-variation-claim-scope.md`](prompts/phase5-23-schema-variation-claim-scope.md), ADR-061·ADR-062. 완전 합성 fixture와 개발 세트 40건만 사용했고 질문·prompt·출력·SQL·행 값은 기록하지 않았다.

> **잠정치.** gold 40건은 `NEEDS_HUMAN_REVIEW`(L1·L3 전)이며 확정 성능·우열 결론으로 쓰지 않는다.

## 조건

- fixture suite `extended-v1`: flat·normalized·legacy(기존) + coded(normalized 구조·약어 이름) + hybrid(2-table 부분 비정규화·영문 동의어 이름). 모두 같은 seed 6개 개설강좌.
- 두 arm 모두 단일 code fence 규칙(`single-fence-v1` / `direct-sql-extract-v1`), `query-ir-v1`·`direct-sql-v1`, temperature 0, timeout 60초, 호출 1회·retry 없음, 모델당 400 호출.
- fine-tune `text2sql_qwen:latest`(`alpaca-raw-no-system-v1`), base `qwen2.5-coder:7b-instruct`(`model-template`).
- report는 Git 비추적 `.schema-adaptive/reports/p523-finetune-ext`, `p523-base-ext`(IR·SQL report, paired comparison, `schema-consistency-v1`).
- core 3종의 variant별 수치는 Phase 5-22와 모두 같았다(재현 확인).

## variant별 정답 (각 분모 40)

| 모델 · arm | flat | normalized | legacy | coded | hybrid | 합계 /200 | spread | 모든 variant 정답 case |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fine-tune · IR | 31 | 31 | 31 | 31 | 31 | 155 | 0 | 31 |
| fine-tune · SQL | 30 | 11 | 31 | 8 | 10 | 90 | 23 | 7 |
| base · IR | 26 | 26 | 26 | 26 | 26 | 130 | 0 | 26 |
| base · SQL | 34 | 24 | 32 | 20 | 23 | 133 | 14 | 15 |

paired(분모 200): fine-tune 둘 다 85·IR만 70·SQL만 5·둘 다 오답 40, base 둘 다 104·IR만 26·SQL만 29·둘 다 오답 41.

세부:

- IR arm은 variant마다 실패 분류도 같았다(fine-tune `UNKNOWN_FIELD` 3·`SEMANTIC_MISMATCH` 6, base 출력 구조 무효 8·`UNKNOWN_FIELD` 3·`INVALID_VALUE_TYPE` 1·`SEMANTIC_MISMATCH` 2). case 단위 cross-schema 일관성은 fine-tune 37/40, base 28/40이며 나머지는 한 variant 이상 미실행이라 판정 불가다.
- 직접 SQL fine-tune: coded 실행 오류 32, hybrid 실행 오류 28·오답 2. 정책 통과(valid SQL)는 모두 40이다.
- 직접 SQL base: coded 기권 9·실행 오류 10·오답 1, hybrid 기권 7·실행 오류 6·오답 4.

## 관찰(잠정)

1. **IR arm은 두 모델 모두 5개 schema에서 정답 수가 같았다(spread 0).** 모델 입력에 물리 schema가 없고 Published Mapping과 compiler가 차이를 흡수하므로 설계상 예상과 맞는다.
2. **직접 SQL arm은 JOIN이 필요한 schema에서 떨어졌다.** 단일 table인 flat·legacy에서는 높고, JOIN이 필요한 normalized·coded·hybrid에서 낮다. 1개 JOIN만 필요한 hybrid에서도 크게 떨어졌다(fine-tune 10, base 23).
3. **이름 축보다 구조 축의 영향이 컸다.** 같은 단일 구조에서 영문(flat)과 약어(legacy)의 차이는 작고(30·31, 34·32), 같은 정규화 구조에서 약어(coded)는 영문(normalized)보다 조금 더 낮았다(11→8, 24→20).
4. base 모델의 전체 정답은 두 arm이 비슷해졌다(IR 130 대 SQL 133). 3종 기준(78 대 90)보다 차이가 줄어든 것은 늘어난 변형 중 JOIN이 필요한 schema가 더 많기 때문이며, 전체 정답 비교는 fixture 구성에 따라 달라지므로 우열 근거로 쓰지 않는다(ADR-061).
5. 모든 variant에서 정답인 case는 IR arm이 두 모델 모두 더 많았다(31 대 7, 26 대 15).

## 해석과 한계

- 방어 가능한 관찰은 ADR-061 범위와 같다: **QueryIR arm은 schema 구조·이름 변화에 대해 정확도가 일정하고, 직접 SQL arm은 JOIN이 필요한 schema에서 떨어진다.**
- IR arm의 일관성은 fixture별 Published Mapping이 정확하다는 전제에 의존한다. 이번 mapping은 개발자가 작성한 합성 fixture mapping이며 자동 mapping 정확도나 L2 검토를 대신하지 않는다.
- 5종 모두 같은 개발자가 같은 seed로 만든 합성 schema다. 독립 unseen schema(E4·L6)가 아니므로 학교 간 일반화를 주장하지 않는다.
- 코드값 변경, FK 없는 관계, view, 컬럼 타입 변경은 포함하지 않았다.
- 개발 세트 40건은 prompt 탐색에 쓰였고 holdout 30건은 실행하지 않았다. system 조건이 모델마다 다르다. 모델당 1회 실행, 통계 검정 없음.
- SQL arm은 column 이름을 비교하지 않아 IR arm보다 약간 관대하다(ADR-057).

## 사람 관문

완전 합성 입력만 사용했다(E3·L5 대상 없음). 수치는 L1/L3 전 잠정치이며 승인 상태는 바꾸지 않았다.
