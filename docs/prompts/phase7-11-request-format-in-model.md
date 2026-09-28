# Phase 7-11 구현 지침: 요청 형식을 모델 안으로 옮기고 provenance에 반영

## 배경 (Codex 독립 검증 결과, 226a633 기준)

1. `alpaca-raw-no-system-v1`에서 non-callable transport가 wrapper에 가려 startup에서 거부되지 않고 호출 시점까지 지연된다. `model-template`은 startup에서 거부되어 형식별 동작이 다르다.
2. `model-template`과 `alpaca-raw-no-system-v1` 모델의 `declared_provenance()`가 완전히 같다. 요청 형식이 transport wrapper에서만 적용되어 `OllamaQueryModel`이 형식을 모르기 때문이다. 지금 capture CLI는 형식 option이 없어 이 경로에 닿지 않지만, no-system 수집을 시작하면 오염 조건(5-12·5-13 recording)과 구별할 수 없다.

두 결함의 공통 원인은 형식을 모델 밖에서 적용하는 구조다.

## 범위

1. `apply_request_format(transport, request_format)`는 형식과 무관하게 **먼저** `callable(transport)`를 검사하고 실패 시 고정 `RequestFormatError`를 낸다.
2. `OllamaQueryModel(..., request_format=None)`
   - `None`은 `model-template`. 허용값 외는 고정 `ValueError("INVALID_OLLAMA_REQUEST_FORMAT")`.
   - 기존 transport callable 검사 뒤 생성자 안에서 형식을 한 번 적용한다. `request_format` 속성으로 노출한다.
   - `declared_provenance()`의 generation config에 형식을 반영한다. **`model-template`은 기존 hash를 그대로 유지**(5-15 paired 계약과 기존 bundle 호환)하고, 그 외 형식만 `request_format`, `raw: true`, 재현 template SHA-256을 추가한다. URL·질문·prompt·SYSTEM 원문은 넣지 않는다.
3. `build_startup_ollama_model`은 transport를 감싸지 않고 `request_format=config.request_format`을 모델에 넘긴다. 형식 적용 지점은 runtime에서 모델 생성자 하나다.
4. 평가 SQL provider(`OllamaDirectSqlModel`)는 기존처럼 `apply_request_format`을 쓰며, 진입 callable 검사로 같은 fail-fast 동작을 얻는다.
5. 5-12·5-13 private recording이 내장 SYSTEM 주입 조건이었고 provenance에 드러나지 않는다는 점을 문서에 기록한다. `live_capture`에 형식 option을 추가하는 일은 이번 범위가 아니다.

## 테스트

- non-callable transport: 두 형식 모두 `apply_request_format`·`OllamaQueryModel`·`build_startup_ollama_model`·`OllamaDirectSqlModel`에서 생성 시점에 고정 오류.
- provenance: `model-template` 값이 변경 전 기록값(`generation_config_sha256=48ba68c9…`)과 동일, no-system은 다르고 같은 설정끼리는 동일, URL·prompt 원문 비포함.
- 형식 이중 적용 없음: startup builder·smoke·arm CLI 경로에서 no-system prompt prefix가 정확히 1회.
- 회귀: Phase 5-15·5-18~5-20·7-07~7-10 관련 테스트.

## 금지

live Ollama 호출, model 다운로드, `live_capture` 기능 추가, guard·prompt 변경, 성능 주장, 사람 승인 상태 변경.
