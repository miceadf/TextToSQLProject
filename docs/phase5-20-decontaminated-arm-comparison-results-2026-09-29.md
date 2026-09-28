# Phase 5-20 내장 SYSTEM 제거 후 IR·직접 SQL arm paired 비교 결과(잠정, 2026-09-29)

기준: [`docs/prompts/phase5-20-ollama-system-decontamination.md`](prompts/phase5-20-ollama-system-decontamination.md). 계약은 Phase 5-18·ADR-057, 요청 형식은 ADR-058. 완전 합성 fixture 3종과 개발 세트 40건만 사용했고 질문·prompt·출력·SQL·행 값은 기록하지 않았다.

> **잠정치.** gold 40건은 `NEEDS_HUMAN_REVIEW`이며 L1·L3 전이다. 확정 성능·우열 결론으로 쓰지 않는다.

## 오염 확인

- `text2sql_qwen:latest` Modelfile의 SYSTEM(CNU `courses` table·column, 한국어는 CP949 byte)은 `system` 미지정 `/api/generate` 호출마다 주입된다.
- 같은 prompt의 입력 token 수: 기본 호출 161, 등록 TEMPLATE을 SYSTEM 없이 raw로 재현한 호출 47(차이 114). raw 재현 template에 임의 SYSTEM을 넣은 호출과 Ollama의 `system` 지정 호출이 둘 다 49로 같아 template 재현이 byte 단위로 맞음을 확인했다.

## 실행

- 로컬 Ollama `0.34.4`, tag `text2sql_qwen:latest`(digest `7070730b…a3cb5a`, GGUF 메타데이터 `general.name=Model_Gguf`·`quantized_by=Unsloth`로 fine-tune 산출물), timeout 60초, temperature 0, 호출 1회·retry 없음.
- 두 arm 모두 `alpaca-raw-no-system-v1`. 240 호출 약 1분 37초. 최종 코드로 재실행해 모든 수치가 같음을 확인했다. report는 Git 비추적 `.schema-adaptive/reports/p520-text2sql-qwen-nosys-final/`.

## 결과 (분모 120)

| 지표 | 5-19 IR (오염) | **5-20 IR** | 5-19 SQL (오염) | **5-20 SQL** |
| --- | ---: | ---: | ---: | ---: |
| 정답 | 90 | **93** | 17 | **72** |
| 실행 성공 | 99 | 111 | 29 | 92 |
| IR 구조 exact / SQL 정책 통과 | 51 | 51 | 69 | 120 |

paired count(5-20):

| 범위 | 둘 다 정답 | IR만 | SQL만 | 둘 다 오답 | IR 정답 | SQL 정답 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 전체(120) | 68 | 25 | 4 | 23 | 93 | 72 |
| flat(40) | 28 | 3 | 2 | 7 | 31 | 30 |
| normalized(40) | 11 | 20 | 0 | 9 | 31 | 11 |
| legacy(40) | 29 | 2 | 2 | 7 | 31 | 31 |

- IR arm은 세 schema에서 모두 31/40이고 cross-schema 일관성 37/40이다. prompt에 물리 schema가 없어 모델 출력이 schema와 무관하고 compiler가 fixture별 SQL을 만든다. 실패는 `UNKNOWN_FIELD` 9, `SEMANTIC_MISMATCH` 18.
- SQL arm은 단일 table인 flat·legacy에서 IR과 거의 같고(30·31) normalized에서 11/40으로 떨어진다. normalized 실패 29건 중 28건이 `EXECUTION_ERROR`이며, 비노출 진단(40 호출)으로 `no such column` 27·`ambiguous column` 1, 실패 SQL의 JOIN 수는 0개 14·1개 13·3개 1이었다. 정규화 schema에서 column이 속한 table을 잘못 고르거나 필요한 JOIN을 만들지 못하는 경향으로 보인다.

## 해석과 한계

- 관찰: 이 fine-tune 모델·합성 fixture에서 QueryIR 경로의 이점은 **여러 table JOIN이 필요한 정규화 schema에 집중**되었고, 단일 table schema에서는 직접 SQL과 거의 같았다. 이는 "schema 구조가 달라질 때 compiler가 JOIN을 대신 처리한다"는 설계 가설과 일치하는 관찰이다.
- Phase 5-19의 SQL 17/120과 "CNU schema 암기" 해석은 내장 SYSTEM 오염 때문이었을 가능성이 높아 정정한다(5-19 문서 참고).
- 한계:
  - 모델은 CNU 단일 schema SQL로 fine-tune되었다. base instruct 모델 비교(§15.1-3)가 있어야 QueryIR 효과와 fine-tune 효과를 분리할 수 있다.
  - fixture는 같은 6개 개설강좌의 구조 변형 3종뿐이고, 개발 세트 40건은 prompt 탐색(5-14)에 쓰였다. holdout 30건은 미실행.
  - SQL arm은 column 이름을 비교하지 않아 IR arm보다 약간 관대하다.
  - 표본이 작고 gold가 미승인이라 통계 검정을 하지 않았다.
- 후속: runtime `OllamaQueryModel`·prototype 호출도 같은 SYSTEM이 주입되는 조건이다(7-09 포함). runtime 요청 형식은 별도 단계에서 명시해야 한다.

## 사람 관문

완전 합성 입력만 사용했다(E3·L5 대상 없음). 수치는 L1/L3 전 잠정치이며 승인 상태는 바꾸지 않았다.
