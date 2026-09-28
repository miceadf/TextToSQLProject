# Phase 5-20 구현 지침: Ollama 내장 SYSTEM 오염 제거와 paired 비교 재실행

## 배경

`text2sql_qwen:latest`의 Modelfile에는 Alpaca TEMPLATE(CRLF)과 CNU `courses` schema를 담은 SYSTEM이 등록되어 있다(SYSTEM의 한국어는 UTF-8이 아닌 CP949 byte로 저장됨). `system`을 지정하지 않은 `/api/generate` 호출은 이 SYSTEM을 매번 주입한다. token 수 측정에서 기본 호출 161 대 SYSTEM 없는 raw 호출 47로 114 token 차이를 확인했고, raw 재현 template에 임의 SYSTEM을 넣은 호출이 Ollama의 `system` 지정 호출과 같은 49 token이어서 template byte 재현을 확인했다. Phase 5-08·5-12~5-14·5-19·7-09의 live 호출은 모두 이 SYSTEM이 주입된 조건이었다.

## 범위

1. 신규 `backend/adaptive/evaluation/ollama_request_format.py`
   - `model-template`: 기존 동작(Ollama가 등록 TEMPLATE·SYSTEM 적용).
   - `alpaca-raw-no-system-v1`: `raw: true`, 등록 TEMPLATE을 SYSTEM 빈 값으로 byte 단위 재현한 문자열로 prompt를 감싼다. payload 원본은 복사 후 변경하고, 이미 `raw`·`system`·`template`이 있으면 거부한다.
2. `arm_live_cli.py`에 `--ollama-request-format`(기본 `model-template`)을 추가하고 **두 arm에 같은 wrapper**를 적용한다.
   - 기본 형식은 Phase 5-19의 label·run ID를 그대로 유지한다.
   - 다른 형식은 label에 `.nosys`를 붙이고 run ID prefix를 `p520-nosys-`로 바꿔 tag와 형식 조합이 기본 형식의 ID와 충돌하지 않게 한다.
3. runtime(`OllamaQueryModel`·prototype)은 이번 단계에서 바꾸지 않는다. runtime 호출도 같은 오염 조건이라는 점을 후속 항목으로 기록한다.

## 테스트

- wrapper의 정확한 byte 문자열, 원본 비변이, 중복 key 거부.
- CLI 기본 형식은 `raw` 없음·5-19 label 유지, no-system 형식은 240 호출 모두 `raw: true`와 정확한 prefix/suffix, 잘못된 형식은 설정 오류.
- 기본 형식 tag가 no-system 형식의 run ID와 충돌하지 않음.

## live 실행과 문서

- `--ollama-request-format alpaca-raw-no-system-v1`로 개발 세트 40×3을 1회 실행하고, 최종 코드로 1회 재실행해 재현을 확인한다.
- Phase 5-19 결과 문서의 "CNU schema 암기" 해석을 오염 가능성으로 정정하고, 원래 수치는 조건과 함께 보존한다.
- 수치는 gold 미승인 잠정치다(L1/L3 전).
