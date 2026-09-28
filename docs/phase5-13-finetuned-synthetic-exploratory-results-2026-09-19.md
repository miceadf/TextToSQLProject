# Phase 5-13 fine-tuned 모델 합성 탐색 평가 (2026-09-19)

> 추가 기록(2026-09-29, Phase 5-20·7-11): 이 실행의 Ollama 호출은 `text2sql_qwen:latest` Modelfile에 내장된 CNU `courses` schema SYSTEM이 주입된 조건(`model-template`)이었다. 수집 당시 provenance에는 이 조건이 드러나지 않는다. no-system 조건과 비교할 때는 ADR-058~060을 따른다.

> 상태: **잠정·미승인 탐색 결과**. Gold 40건 모두 `NEEDS_HUMAN_REVIEW`. 논문용 확정 모델 성능 아님.

## 환경과 실행 범위

- 로컬 Ollama 0.34.2, 정확한 `text2sql_qwen:latest` tag와 `/api/tags` digest 확인. 모델 weight 출처와 선언 provenance는 독립 검증하지 않았다.
- 기존 합성 benchmark v1의 질문 40건 × flat·normalized·legacy fixture 3개로 `capture-targets-v1` 120건을 결정적으로 생성했다. 요청 ID는 평가 runner의 규칙과 동일하다. Gold IR·기대 결과·SQL·DB 행은 capture 입력과 모델 prompt에 넣지 않았다.
- `--check` 120건 통과 후 명시적 `--live` 1회 실행. capture 성공 120건, provider 실패 0건. Retry·fallback·overwrite 없음.
- Git 비추적 private bundle의 manifest·artifact hash·count·`DECLARED_UNVERIFIED` provenance를 strict load했다. 기존 `run-recorded`를 합성 SQLite fixture에만 연결해 오프라인 재생했다. Private target·bundle·상세 report는 Git 비추적 상태다.

## 잠정 집계

| 항목 | 건수 / 전체 120 attempt |
| --- | ---: |
| 예정·완료 attempt | 120 / 120 |
| 구조적 QueryIR exact | 51 / 120 |
| 하네스 실행 성공 | 99 / 120 |
| 합성 gold semantic 일치 | 90 / 120 |
| 비교 불가 | 0 / 120 |

통제된 실패 유형: `IR_VALIDATED:UNKNOWN_FIELD` 21건, `RESULT_VALIDATED:SEMANTIC_MISMATCH` 9건. 실패와 불일치를 전체 120 attempt 분모에서 제외하지 않았다. 모델 응답을 QueryIR로 보정하지 않았다.

이 집계는 합성 fixture와 미승인 gold에 한정된 탐색값이다. 실제 CNU DB 정확도, 다른 학교 스키마 일반화, 최종 fine-tuned 모델 성능을 증명하지 않는다. `run-recorded`는 offline replay이며 provenance도 `DECLARED_UNVERIFIED`다. E3/L5·L1/L3 결정은 각 실제 사용·공유·성능 확정 관문까지 대기한다.

공개 문서·stdout에 질문·응답·프롬프트·물리 mapping·SQL·결과 행·HTTP body·절대 경로를 복제하지 않았다.
