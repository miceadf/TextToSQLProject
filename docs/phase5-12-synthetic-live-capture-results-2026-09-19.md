# Phase 5-12 합성 private live capture 결과 (2026-09-19)

> 추가 기록(2026-09-29, Phase 5-20·7-11): 이 실행의 Ollama 호출은 `text2sql_qwen:latest` Modelfile에 내장된 CNU `courses` schema SYSTEM이 주입된 조건(`model-template`)이었다. 수집 당시 provenance에는 이 조건이 드러나지 않는다. no-system 조건과 비교할 때는 ADR-058~060을 따른다.

기준: `docs/prompts/phase5-12-synthetic-live-private-capture.md`. 완전 합성 질문·ontology·mapping 2건만 로컬 Ollama에 전송했다. 질문·모델 응답·프롬프트·HTTP body·물리 mapping·SQL·DB 행은 이 문서와 공개 로그에 기록하지 않았다.

## 환경과 실행

- 로컬 Ollama 0.34.2, 정확한 model tag `text2sql_qwen:latest`를 `ollama list`와 `/api/tags`에서 확인했다. 선언 digest는 `/api/tags` 값과 일치한다. 모델 출처의 독립 증명은 아니다.
- `capture-targets-v1`의 합성 target 2건, 명시적 base URL·timeout 30초·model run ID·새 private output을 사용했다. 기존 bundle 덮어쓰기·fallback·retry 없음.
- `--check` 먼저 성공: target 2건, 선언 provenance fingerprint `86e32b0572e9d644f4a1a245b9b499d072e292df7698f501acd62db21f8f03d0`. 이후 `--live` 1회 실행해 성공 2건·실패 0건을 private bundle에 저장했다.
- Private target·bundle은 Git ignore와 비추적 상태를 확인했다. `recording-bundle-v1` strict loader로 manifest·artifact hash·건수·`DECLARED_UNVERIFIED` provenance를 검증했다. `RecordedModel`이 입력 순서대로 두 응답을 재생했다.

## 안전한 형식 분류

| case ID | capture | JSON 여부·타입 | QueryIR 구조·semantic 검증 |
| --- | --- | --- | --- |
| `synthetic-live-01` | 성공 | 파싱됨·object | `VALID_QUERY_IR` |
| `synthetic-live-02` | 성공 | 파싱됨·object | `VALID_QUERY_IR` |

응답 본문은 QueryIR로 보정하거나 SQL로 실행하지 않았다. 이 결과는 두 합성 입력의 adapter 전송, private storage, strict load, context-bound replay와 현재 형식·semantic 계약만 검증한다. 모델 정확도, 40×3 benchmark, 학교 간 일반화 또는 안정적인 지연의 증거가 아니다.

E3·L5와 L1·L3는 각 실제 사용·공유·성능 확정 관문까지 대기한다. Gold 40건은 모두 `NEEDS_HUMAN_REVIEW`다.
