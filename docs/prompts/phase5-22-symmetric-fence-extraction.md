# Phase 5-22 구현 지침: arm 간 대칭 code fence 추출과 두 모델 재실행

## 배경

Phase 5-21에서 base 모델은 IR 출력 40건을 모두 단일 Markdown code fence로 감쌌고 안쪽 28건은 유효 QueryIR이었다. SQL arm(`direct-sql-extract-v1`)은 단일 fence를 벗기지만 IR arm은 벗기지 않아 IR 0/120이 되었다. 사용자는 평가 계층에서 IR arm에도 같은 규칙을 적용하는 방식을 선택했다.

## 범위

1. `direct_sql.py`의 fence 탐지를 공개 함수 `find_code_fences(text) -> list[str]`로 노출하고 `extract_sql`이 그대로 사용한다(동작 불변).
2. 신규 평가 전용 `backend/adaptive/evaluation/ir_extract.py`
   - `IR_EXTRACT_NONE = "none"`, `IR_EXTRACT_SINGLE_FENCE = "single-fence-v1"`.
   - `SingleFenceIrModel(delegate)`: delegate 출력이 문자열이고 `find_code_fences`가 정확히 1개를 찾으면 안쪽을 strip해 `ollama.py`와 같은 strict JSON 규칙(duplicate key·non-finite 거부)으로 파싱해 반환한다. 파싱 실패, fence 0개·2개 이상, 비문자열은 원래 출력을 그대로 반환해 기존 분류(`MODEL_INVALID_OUTPUT` 등)를 유지한다. 그 외 보정은 없다.
   - runtime(`api`·`harness`)은 이 모듈을 import하지 않는다.
3. `arm_live_cli`에 `--ir-extract {none,single-fence-v1}`(기본 `none`, 반복 거부). `single-fence-v1`은 IR arm model만 감싼다(SQL arm은 원래 같은 규칙).
   - 기본값에서는 label·run ID가 5-19~5-21과 같다.
   - `single-fence-v1`은 label에 `.fence`를 붙이고 run ID prefix를 `p522-`로 시작해 기존 run과 섞이지 않게 한다(형식이 no-system이면 `p522-nosys-fence-`, `model-template`이면 `p522-fence-`).
   - stdout 요약에 `ir_extract`를 추가한다.
4. 재실행: fine-tune(`text2sql_qwen:latest`, `alpaca-raw-no-system-v1`)과 base(`qwen2.5-coder:7b-instruct`, `model-template`)를 `single-fence-v1`로 각 1회. 결과가 나빠도 규칙·prompt를 조정하지 않는다.

## 테스트

- `find_code_fences` 도입 전후 `extract_sql` 동작 동일(기존 테스트).
- wrapper: 단일 fence JSON→dict, 안쪽 비JSON·duplicate key·NaN→원본 문자열, fence 0·2개→원본, 비문자열·dict는 그대로, delegate 1회 호출.
- CLI: fence로 감싼 oracle IR을 내는 synthetic transport에서 `none`은 IR 0, `single-fence-v1`은 IR 120; label·run ID·요약 필드; 잘못된 값·반복은 설정 오류.
- runtime 격리 테스트에 `ir_extract` 추가.

## 기록

결과 문서 `docs/phase5-22-symmetric-extraction-results-2026-09-29.md`: 두 모델의 두 arm 지표, paired count, 5-20·5-21과의 대조, 한계. 수치는 L1/L3 전 잠정치다.
