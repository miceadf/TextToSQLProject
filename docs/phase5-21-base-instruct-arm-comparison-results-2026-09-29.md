# Phase 5-21 base instruct 모델 IR·직접 SQL arm paired 비교 결과(잠정, 2026-09-29)

기준: [`docs/prompts/phase5-21-base-instruct-arm-comparison.md`](prompts/phase5-21-base-instruct-arm-comparison.md). 완전 합성 fixture 3종과 개발 세트 40건만 사용했고 질문·prompt·출력·SQL·행 값은 기록하지 않았다.

> **잠정치이며, 이 실행의 IR arm 수치는 방식 비교로 쓸 수 없다.** 아래 "비교 비대칭" 참고. gold 40건도 `NEEDS_HUMAN_REVIEW`(L1·L3 전)다.

## 환경

- 로컬 Ollama `0.34.4`, tag `qwen2.5-coder:7b-instruct`(Ollama 공식 레지스트리, Q4_K_M, manifest digest `d9bb33f2…c8fb446`, model blob `sha256-60e05f21…bfc2463`), 저장 경로 `C:/Users/micea/.ollama/models`.
- 등록 TEMPLATE은 ChatML, 기본 SYSTEM은 일반 문구 `You are Qwen, created by Alibaba Cloud. You are a helpful assistant.`
- 요청 형식 `model-template`(사용자 결정). fine-tune 모델의 5-20 조건(`alpaca-raw-no-system-v1`, system 없음)과 system 조건이 다르다.
- `query-ir-v1`·`direct-sql-v1`, temperature 0, timeout 60초, 호출 1회·retry 없음. `--check`(호출 0회) 뒤 live 1회, 240 호출 약 2분. report는 Git 비추적 `.schema-adaptive/reports/p521-qwen25-coder-7b-instruct-template/`.

## 결과 (분모 120)

| 지표 | IR arm | SQL arm |
| --- | ---: | ---: |
| 정답 | 0 | 90 |
| 실행 성공 | 0 | 102 |
| SQL 정책 통과 | — | 108 |
| UNKNOWN(기권) | — | 12 |

- IR arm 실패는 120건 모두 `IR_GENERATED:MODEL_INVALID_OUTPUT`이었다.
- SQL arm variant별 정답: flat 34, normalized 24, legacy 32. 실패는 기권 12(flat 2·normalized 6·legacy 4), `INCORRECT_RESULT` 12, `EXECUTION_ERROR` 6(normalized).

## IR arm 원인 진단(비노출 구조 분류)

IR prompt는 물리 schema를 포함하지 않아 세 fixture에서 같으므로 flat 40건을 별도 진단 호출해 분류했다.

- 40건 모두 **단일 Markdown code fence로만 감싼 출력**이었다(prompt의 "no Markdown" 지시 위반).
- fence 안쪽: 유효 QueryIR(구조·semantic 검증 통과) 28, 구조 검증 실패 8, semantic 검증 실패 4.

## 비교 비대칭

- SQL arm 추출 규칙(`direct-sql-extract-v1`)은 단일 fence를 벗긴다. IR arm은 `OllamaQueryModel`이 JSON 파싱에 실패한 문자열을 그대로 넘겨 fence가 있으면 무조건 실패한다(Phase 5-07 계약).
- fine-tune 모델은 fence 없는 JSON을 내서 이 비대칭이 드러나지 않았지만, base 모델에서는 IR arm 전체를 0으로 만들었다.
- 따라서 이 실행의 IR 0/120 대 SQL 90/120은 **방식의 차이가 아니라 추출 규칙 차이**를 주로 반영한다. 방식 비교에 쓰려면 두 arm에 같은 추출 규칙을 적용한 재실행이 필요하다(결정 대기).

## 관찰(잠정)

- base 모델의 SQL arm(90/120, normalized 24/40)은 fine-tune 모델의 5-20 SQL arm(72/120, normalized 11/40)보다 높았다. 동적 schema SQL에서는 CNU 단일 schema fine-tune이 오히려 불리했을 가능성이 있다. 다만 system 조건이 달라(5-21 일반 system, 5-20 system 없음) 직접 비교에는 한계가 있다.
- fence 안쪽 유효 QueryIR 비율(28/40)은 참고값이며 실행·semantic 정답이 아니다.

## 사람 관문

완전 합성 입력만 사용했다(E3·L5 대상 없음). 수치는 L1/L3 전 잠정치이며 승인 상태는 바꾸지 않았다.
