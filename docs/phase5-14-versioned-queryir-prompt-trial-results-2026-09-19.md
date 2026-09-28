# Phase 5-14 versioned QueryIR prompt 단일 탐색 비교 (2026-09-19)

> 추가 기록(2026-09-29, Phase 5-20·7-11): 이 실행의 Ollama 호출은 `text2sql_qwen:latest` Modelfile에 내장된 CNU `courses` schema SYSTEM이 주입된 조건(`model-template`)이었다. 수집 당시 provenance에는 이 조건이 드러나지 않는다. no-system 조건과 비교할 때는 ADR-058~060을 따른다.

> 상태: **같은 미승인 합성 개발 세트의 잠정 결과**. Gold 40건 모두 `NEEDS_HUMAN_REVIEW`. 모델 성능 확정 또는 일반화 근거가 아니다.

## 가설과 조건

Phase 5-13 v1의 `UNKNOWN_FIELD` 21건을 private 응답에서 읽기 전용으로 분류했다. 첫 unknown field 기준 ontology 밖 semantic field 12건, entity 접두사 불일치 5건, 물리 column 형태 4건, 대소문자·표기 차이 0건, 그 외 0건, 판별 불가 0건이다. 중복 unknown 참조 0건, 서로 다른 범주가 한 attempt에 공존한 경우 3건이다. 분류 범주 합계는 21건이며 중복·혼합은 별도 보조 건수다.

가설: 허용 semantic field 문자열을 목록에서 정확히 복사하도록 지시하면 `UNKNOWN_FIELD`가 감소한다. 기존 `query-ir-v1` 기본값과 전송 설정을 유지하고, 명시적 `query-ir-v2` 한 가지를 추가했다. v1 template SHA-256은 `9546599f1b1cb0f733c7ff6a7d02c0d47d6dba6a0767263acc37869bb74774d5`, v2는 `e1400acd35e2601cc35f51062b5516c35e14bec41a46ac8eecd9b4d0a3f43b83`이다. 두 버전의 generation config SHA-256은 `48ba68c94123f52c8d0d5f531f69fa29cdea6107a8a2fe69e2d937cfa14126e0`으로 같다.

Ollama 0.34.2의 정확한 `text2sql_qwen:latest` tag와 digest를 다시 확인했다. 합성 40질문 × 3 fixture의 같은 의미 context를 사용하고 새 run ID에 맞게 request ID만 재생성했다. `--check` 후 v2 `--live`를 한 번 실행해 120 target 모두 수집 성공, provider 실패 0건이었다. Private bundle strict load, 선언 provenance 확인, `run-recorded` 오프라인 재생, case×variant 120쌍 대응을 검증했다. 재시도·fallback·응답 보정은 없다. V1/v2 private target·bundle·상세 report는 Git 비추적 상태다.

## Paired 잠정 결과

| 항목 (분모 120) | v1 | v2 | v2만 성공/발생 | v1만 성공/발생 |
| --- | ---: | ---: | ---: | ---: |
| 구조적 QueryIR exact | 51 | 0 | 0 | 51 |
| 하네스 실행 성공 | 99 | 42 | 3 | 60 |
| 합성 gold semantic 일치 | 90 | 36 | 3 | 57 |
| `UNKNOWN_FIELD` | 21 | 72 | 54 | 3 |
| provider 실패 | 0 | 0 | — | — |
| recording 누락 | 0 | 0 | — | — |

V2의 나머지 실패는 `MODEL_INVALID_OUTPUT` 6건, semantic mismatch 6건이다. V2 `UNKNOWN_FIELD` 72건의 첫 unknown 기준 분류는 entity 접두사 불일치 46, ontology 밖 6, 물리 column 형태 5, 그 외 15, 판별 불가 0이다. 중복 참조 27개, 혼합 범주 attempt 50건이다. **가설은 이 실험에서 지지되지 않았다.** V2는 기본값으로 전환하지 않는다.

이 세트는 v1 결과를 본 뒤 v2를 설계한 개발 세트다. 결과는 독립 검증 정확도나 실제 CNU DB 성능을 뜻하지 않는다. Gold 사람 검토와 provenance 독립 검증도 미완료다. E3/L5·L1/L3 팀 결정은 필요한 실제 사용·공유·성능 확정 관문까지 대기한다.
