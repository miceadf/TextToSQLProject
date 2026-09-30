# Phase 8-12 지침: 업로드 흐름에 AI mapping 제안 연결

작성일: 2026-09-30. 사용자 결정: 합성 benchmark 개선을 멈추고 프로토타입을 완성한다. 이론·실습 시간(8-11)은 넣지 않는다.

## 1. 배경과 범위 조정(프로토타입 타협)

8-08~8-10은 원래 table을 변형한 합성 benchmark의 점수를 올리는 데 집중했다. 그 결과 제가 만든 함정에 맞추는 과적합이 생겼다(예: `capacity`를 강의실 좌석으로 둔 함정, 세트마다 뜻이 다른 `credit_hours`). 그래서 다음처럼 범위를 조정한다.

- 멈추는 것:
  - benchmark 개선과 새 보류 세트.
  - 8-11 일반화 설계(DB별 ontology 확장, 매개변수형 변환, 화면 field 목록 일반화).
  - prompt 개선, 추가 모델 비교, Phase 9.
- 프로토타입 완성 순서:
  1. **8-12(이번):** 업로드한 DB의 mapping 초안에 AI 제안을 연결한다.
  2. **8-07 축소판:** 최소 관리자 화면.
  3. **처음부터 끝까지 browser smoke와 시연 절차.**

## 2. 목표

관리자가 SQLite DB를 업로드하면 로컬 모델이 canonical field마다 column을 고르고, 그 column이 검토 화면의 첫 후보가 된다. 모델이 없거나 실패하면 지금처럼 규칙 기반 후보만 쓴다. 검토·게시 절차와 실행 경로는 바꾸지 않는다.

## 3. 설계

### 3.1 무엇을 쓰는가

- 8-10 v2의 **column 선택 단계만** 쓴다. prompt 문장은 v2와 같다(`ai-mapping-v2/columns`).
- 값 처리(변환, 값 사전)는 쓰지 않는다. 업로드 DB의 게시 mapping은 지금 `table.column`만 담기 때문이다.
  - 변환·값 사전의 업로드 지원은 8-07 코드표 입력과 함께 다룬다.
- 모델: 로컬 loopback Ollama, 기본 `qwen2.5-coder:7b-instruct`, temperature 0, JSON schema 제한 출력. 한 번 업로드에 한 번 호출한다.

### 3.2 입력

- 모델 입력은 column별로 이름, 선언 type, 저장 값 예시(최대 10개, 100자 이하)다.
- 예시 값은 등록 중 메모리에서만 읽는다. catalog·proposal 파일과 HTTP 응답에는 넣지 않는다.
  - 기존 규칙(저장 artifact에 sample 없음)을 유지한다.
  - schema fingerprint는 sample과 무관하다.

### 3.3 결과 조립

- field별 후보 순서는 AI가 고른 column, 그다음 규칙 후보(같은 column은 한 번만)다. 최대 4개다.
- AI 후보:
  - `confidence`는 같은 column의 규칙 점수를 그대로 쓴다. 모델은 보정된 확신도를 주지 않기 때문이다.
  - `review_status`는 규칙 점수가 자동 승인 기준(0.90) 이상이면 `AUTO_APPROVE_CANDIDATE`, 아니면 `NEEDS_REVIEW`다. AI 선택만으로 자동 승인 후보가 되지 않는다.
  - 근거(evidence)는 규칙의 세 근거에 `AI_PROPOSAL` 한 줄을 더한다. 문장은 고정 형식이며 예시 값을 넣지 않는다.
- 한 column은 한 field에만 쓴다. 중복이면 뒤 field의 AI 선택을 버린다.
- AI가 "none"을 고른 field는 규칙 후보를 그대로 둔다.
- `unmapped_fields`는 AI 후보가 없고 규칙 최고 점수가 0.60 미만인 field다.

### 3.4 실패와 대체

- 모델 설정이 없으면 규칙 제안만 쓴다(지금과 같음).
- 다음 경우에는 규칙 제안으로 대체하고 등록은 계속한다.
  - 호출 실패, 시간 초과, JSON 오류, schema 위반.
  - 조립 결과가 계약 검사를 통과하지 못함: database ID·fingerprint·ontology 버전이 같아야 하고, field 목록이 같아야 하며, 모든 후보 column이 catalog에 있어야 한다.
- 대체 사유는 저장하지 않는다. 응답의 `proposer`로만 구분한다.

### 3.5 공개 응답

- 업로드 응답 버전을 `private-sqlite-upload-v2`로 올린다.
- `mapping.proposer`:
  - `AI`: AI 후보가 하나라도 있음.
  - `RULES`: 모델 설정 없음.
  - `AI_UNAVAILABLE`: 모델 설정은 있으나 AI 후보가 없음(실패 또는 전부 none).
- 근거 signal에 `AI_PROPOSAL`을 더한다.
- 검토 화면 후보에 `source`(`AI`/`RULES`)를 더한다.
- 물리 이름은 지금처럼 관리자 화면에만 보인다. SQL·예시 값·모델 원출력은 공개하지 않는다.

### 3.6 서버 동작

- 등록(모델 호출 포함)은 threadpool에서 실행한다. 한 업로드가 모델을 기다리는 동안 다른 요청이 막히지 않게 하기 위해서다.
- 설정:
  - `synthetic_prototype` CLI: `--mapping-model-tag TAG`(명시적 opt-in). base URL은 `--ollama-base-url`과 공유하고, 없으면 `http://127.0.0.1:11434`를 쓴다.
  - dev launcher: loopback `OLLAMA_BASE_URL`이 유효하면 켠다. 모델 tag는 `backend/.env`의 `MAPPING_MODEL`이 있으면 그것을, 없으면 기본 tag를 쓴다. 상태 줄에 `mapping_ai=enabled|disabled (…)`를 표시한다.

## 4. 평가 코드와의 관계

- `OllamaJsonModel`·`ProposalError`·`schema_conforms`와 column prompt 조립은 제품 모듈 `backend/adaptive/mapping/ai_proposal.py`로 옮긴다. `evaluation/ai_mapping.py`는 그것을 가져다 쓴다.
- 8-09·8-10의 prompt 문장이 바뀌지 않았음을 테스트로 고정한다(모든 변형·버전에서 옮기기 전과 같은 문자열).

## 5. 테스트

- fake model로 확인할 것:
  - AI 첫 후보, 규칙 후보 dedupe, 최대 4개.
  - confidence·status 규칙, 중복 column 버림, none 처리.
  - 실패·schema 위반·계약 위반 시 규칙으로 대체.
  - 예시 값이 prompt에만 들어가고 저장 파일·응답에는 없음.
- 업로드 HTTP:
  - `proposer` 세 값, v2 응답, threadpool 실행.
  - 기존 오류 code 유지.
- 검토 요약의 `source`.
- CLI·launcher 설정 파싱.
- frontend parser: v2, `AI_PROPOSAL`, `proposer`, `source`.
- 실제 모델 smoke:
  - 합성 SQLite(기존 flat 합성 DB 형태) 한 개를 로컬 모델로 한 번 등록하고, 제안 결과를 private 보고서로 확인한다.
  - 점수 주장은 하지 않는다.

## 6. 제약

- 기존 제약을 그대로 유지한다.
  - 32 MiB, Git 비추적 private 저장, immutable 등록, strict reload, PENDING_REVIEW 격리, 게시된 mapping만 실행.
  - 모델 출력은 제안일 뿐이며 게시 전 사람 검토를 대신하지 않는다.
  - 사람 관문을 표시하지 않고, push하지 않는다.
- 범위 밖:
  - 업로드 DB의 변환·값 사전.
  - 일부 field를 `unsupported`로 두는 게시(지금은 15개 field 모두 필요).
  - 관리자 설명 입력.
  - 이 셋은 8-07에서 다룬다.
