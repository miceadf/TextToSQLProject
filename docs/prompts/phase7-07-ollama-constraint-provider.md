# Phase 7-07 구현 지침: Ollama constraint-aware provider 합성 통합

## 목표

기존 `OllamaQueryModel`을 adaptive clarification의 `ConstraintAwareProvider` 경계에 연결한다. 사용자가 선택한 semantic constraint를 server-owned bridge가 검증하고, Ollama prompt에 전달되는 ontology/mapping semantic view를 해당 entity 또는 field로 제한한 뒤 DB 독립 QueryIR을 정확히 한 번 생성한다. 생성된 QueryIR은 기존 Phase 6-09/10 validation·constraint compliance·compiler·policy·read-only executor 경로를 그대로 통과한다.

이번 단계는 injectable synthetic transport로 통합 계약만 완결한다. CLI Ollama 설정, 기본 provider 전환, live Ollama 호출, raw SQL fallback, model 재학습, 실제 PostgreSQL/CNU 자료, frontend, legacy 시스템은 변경하거나 사용하지 않는다.

## 구현 범위

1. `backend/adaptive/harness`에 `OllamaQueryModel` 전용 constraint-aware bridge를 추가한다.
2. bridge는 네 인자 `generate_ir(request, ontology, mapping, constraint)`를 제공하고 내부 `OllamaQueryModel.generate_ir(request, restricted_ontology, restricted_mapping)`을 최대 1회 호출한다.
3. `create_synthetic_clarification_prototype`에 명시적 server-owned Ollama model 주입 인자를 추가한다.
   - 인자가 없으면 기존 `_FixedConstraintProvider`를 그대로 사용한다.
   - 정확한 `OllamaQueryModel` 계열만 bridge로 감싼다. 임의 HTTP/request 입력으로 provider를 고를 수 없다.
   - global module-level `app`과 기본 CLI는 model을 주입하지 않아 Ollama import 외 network 호출 0회를 유지한다.
4. 성공적으로 주입된 bridge를 기존 `create_clarification_http_app` provider 자리에만 연결한다. compiler/policy/executor/router/registry/catalog는 변경하지 않는다.

## bridge strict 계약

- public 입력 네 개를 plain JSON round-trip으로 strict 재검증한다. forged/constructed Pydantic instance, extra field, coercion, wrong type을 fail closed한다.
- mapping은 `PUBLISHED`이고 request와 같은 database ID여야 한다.
- constraint version과 request ID hash, database ID hash, ontology fingerprint, mapping fingerprint가 현재 입력과 정확히 일치해야 한다.
- `selected_semantic_ref`는 ontology와 mapping 양쪽에 존재하는 entity 또는 field만 허용한다.
- field constraint라면 restricted ontology/mapping에는 해당 field와 그 entity만 둔다.
- entity constraint라면 해당 entity와 `Entity.` prefix의 mapped fields만 둔다. field가 하나도 없으면 호출 전 거부한다.
- semantic view는 결정적 순서/값을 가지며 원본 request/ontology/mapping/constraint를 변경하지 않는다. model에는 방어적 deep copy만 전달한다.
- model 호출은 검증 성공 뒤 정확히 1회, retry/model substitution/repair/raw SQL fallback 없이 수행한다.
- model output은 가공·보정하지 않고 기존 `run_constrained_generation`이 QueryIR 구조·semantic·constraint compliance를 검증하게 한다.

## prompt·정보 비노출 계약

- 기존 `OllamaQueryModel` prompt builder가 physical `MappingProfile`을 직렬화하지 않는 계약을 유지한다.
- synthetic transport가 받은 prompt에는 선택 범위의 canonical semantic entity/field와 operator/type만 있어야 한다.
- DSN, host, port, username, password, physical schema/table/column, mapping fingerprint, request/database fingerprint, option ID, session token, SQL을 prompt·public exception·repr·HTTP response·log에 노출하지 않는다.
- question은 기존 `OllamaQueryModel` 계약대로 prompt에 포함될 수 있으나 오류·repr·HTTP response/log에 반사하지 않는다.
- bridge public failure는 고정 code/message의 전용 오류로 정규화한다. underlying transport/model/parser 예외 원문을 보존하거나 출력하지 않는다.
- bridge와 app state의 repr/str에 model tag/base URL/transport/question/credential이 나타나지 않게 한다.

## DBMS 독립성

- bridge는 SQLite/PostgreSQL dialect를 알지 못하며 SQL을 받거나 반환하지 않는다.
- 동일 semantic ontology/mapping/constraint라면 database ID가 달라도 같은 제한 semantic prompt shape를 만든다.
- 이후 기존 database ID router가 SQLite 또는 PostgreSQL compiler/policy/executor를 선택한다. bridge가 router를 우회하지 않는다.
- PostgreSQL profile의 startup validation과 request-time schema drift 재검사를 약화하지 않는다.

## factory·HTTP 상태 전이

- app startup은 model transport를 호출하지 않는다.
- clarification `start`는 기존처럼 ambiguity pending만 만들며 model transport 호출 0회다.
- 정확한 option `submit`에서만 model transport 1회 후 기존 실행 경로를 탄다.
- cancel, invalid/expired/reused token, context drift, invalid selection은 model 호출 0회다.
- provider timeout/error/empty/non-JSON/invalid QueryIR/constraint violation은 기존 fixed HTTP error taxonomy로 redacted 변환하며 session은 기존 one-shot 소비 계약을 유지한다.
- SQLite와 주입 PostgreSQL context 모두 같은 bridge를 공유하되 request의 database ID와 mapping/context를 혼합하지 않는다.

## 테스트

관련 Python 테스트만 실행한다. 모든 Ollama 응답은 injected synthetic transport이며 network·실 DB를 사용하지 않는다.

- field constraint가 prompt semantic context를 정확히 한 field로 제한하고 valid QueryIR을 반환.
- entity constraint가 해당 entity의 mapped fields만 포함.
- physical table/column·DSN-like marker·option/token/hash가 prompt/envelope/error/repr/log에 없음.
- SQLite와 PostgreSQL ID의 semantic prompt가 dialect/physical identifier 없이 동등한 shape를 사용.
- request/mapping ID mismatch, stale mapping, forged input, fingerprint/version mismatch, unknown/unmapped ref는 transport 0회 고정 오류.
- model mutation이 caller context에 영향 없음.
- transport success/non-JSON/empty/timeout/error가 호출 예산과 fixed redaction을 지킴.
- default factory는 기존 fixed provider와 network 0회를 유지.
- injected Ollama model factory는 startup/start 0회, submit 1회, cancel/invalid submit 0회.
- generated QueryIR은 기존 constraint validation 및 DB별 compiler/policy/executor를 우회하지 않음.
- Phase 6-09/10/11/12/13과 Phase 7-04 관련 회귀 테스트 실행.

## 금지

- Ollama live 호출, network, model tag 조회, 재학습, 성능 측정 금지.
- CLI/env/config flag 추가와 기본 provider 전환 금지.
- raw SQL prompt/output fallback 또는 dialect별 prompt 추가 금지.
- frontend, PostgreSQL profile/startup loader, SQLite registration/review/publish, legacy 변경 금지.
- 실제 CNU/학교 DB와 private artifact 사용 금지.
- 계획/ADR/handoff/사람 체크포인트 수정, 전체 pytest, compileall, commit, push 금지. 이는 메인 Codex가 담당한다.

## 완료 보고

긴 diff와 전체 로그를 출력하지 않는다. 한국어로 변경 파일, 핵심 계약, 관련 테스트 결과, 실패 원인, 남은 위험만 간결히 보고한다.
