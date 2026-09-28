# Phase 7-10 구현 지침: runtime Ollama 요청 형식 명시

## 배경

Phase 5-20에서 `text2sql_qwen:latest`의 내장 SYSTEM(CNU `courses` schema)이 `system`을 지정하지 않은 모든 `/api/generate` 호출에 주입됨을 확인했다(ADR-058). 평가 CLI는 요청 형식을 명시하게 했지만 runtime(Phase 7-07 provider, 7-08 startup, 7-09 smoke)은 여전히 기본 형식으로 호출한다. `alpaca-raw-no-system-v1`은 등록 TEMPLATE이 Alpaca인 모델에서만 유효하므로 모든 모델의 기본값으로 둘 수 없다.

## 범위

1. 요청 형식 모듈을 runtime 계층 `backend/adaptive/harness/ollama_request_format.py`로 옮긴다. `backend/adaptive/evaluation/ollama_request_format.py`는 호환 re-export만 남긴다. runtime은 evaluation을 import하지 않는다.
2. `OllamaStartupConfig`에 `request_format`을 추가하고 버전을 `ollama-startup-config-v2`로 올린다.
   - `parse_ollama_startup_config(model_tag, base_url=None, timeout=None, request_format=None)`: `None`은 `model-template`(기존 호출자 호환). 값은 `model-template`·`alpaca-raw-no-system-v1`만, 문자열 외 거부.
   - `build_startup_ollama_model(config, transport=None)`는 기본 loopback transport든 주입 transport든 **config의 형식을 한 번만** 적용한다.
   - repr/str은 고정 문자열이며 형식·URL·tag를 넣지 않는다.
3. 평가 쪽 `OllamaDirectSqlModel`도 `config.request_format`을 직접 적용한다. `arm_live_cli`는 형식을 config로만 전달하고 transport를 미리 감싸지 않는다(이중 적용 방지). 5-19·5-20 label·run ID 규칙은 그대로다.
4. prototype CLI(`synthetic_prototype`)와 smoke CLI(`ollama_prototype_smoke`)에 `--ollama-request-format`을 추가한다.
   - **Ollama를 켤 때 필수**다. 없으면 고정 설정 오류(prototype: stderr 고정 문구·exit 2, smoke: `CONFIG_ERROR`). 반복·축약은 기존 strict parser 규칙대로 거부한다.
   - Ollama를 켜지 않으면(`--ollama-model-tag` 없음) 이 option도 줄 수 없다.
   - smoke 출력 JSON에 `request_format`을 추가한다.
5. startup·import에서 network 0회, clarification start 0회, submit 1회 계약은 그대로다. Phase 7-07 constraint 축소와 QueryIR 검증 경계는 바꾸지 않는다.

## 테스트

- config: 형식 기본값·허용값·거부값, repr 고정, 위조 config 거부.
- builder: no-system 형식에서 transport가 받는 payload가 `raw: true`·정확한 Alpaca 문자열, `model-template`에서는 변화 없음, 주입 transport에도 한 번만 적용.
- prototype CLI: Ollama option에 형식 누락 시 exit 2, 형식만 단독 지정 시 exit 2, 정상 시 factory에 전달된 model이 형식을 적용.
- smoke CLI: 형식 필수, 출력에 형식 포함, synthetic transport로 `raw: true` 확인.
- 평가: `arm_live_cli` 두 arm 모두 no-system에서 240 호출 전부 `raw: true`(이중 적용 없음), 기본 형식 label·run ID 불변.
- 회귀: Phase 7-07/7-08/7-09/5-18/5-19/5-20 관련 테스트.

## live 확인

구현·검토·전체 테스트 뒤 7-09 smoke를 `--ollama-request-format alpaca-raw-no-system-v1`로 1회 실행한다. 성공 실행 경로가 live로 통과하는지와 7-09의 질문 복사 경향이 남는지 관찰만 기록한다. 결과가 나빠도 guard·prompt는 바꾸지 않는다.

## 금지

- 모든 모델에 대한 no-system 기본값, startup에서 `ollama show`·network 호출.
- raw SQL fallback, guard 완화, frontend·legacy·PostgreSQL profile 변경.
- 성능 주장, 사람 승인 상태 변경.
