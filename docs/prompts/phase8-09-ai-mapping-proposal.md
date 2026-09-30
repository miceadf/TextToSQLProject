# Phase 8-09 지침: AI mapping 제안과 규칙 기반 비교

작성일: 2026-09-30. 사용자 승인 개발 순서 4번.

## 1. 목적

8-08 benchmark(12개 변형, 고정)에서 로컬 LLM이 낸 mapping 제안을 규칙 기반과 같은 방식으로 채점한다. benchmark·정답·질문은 바꾸지 않는다.

AI의 역할은 **제안**까지다. 제안은 검증기를 통과한 뒤에도 사람 검토 전에는 게시되지 않는다. 이번 단계는 평가만 하며 runtime 게시 경로는 바꾸지 않는다.

## 2. 제안 파이프라인

모델은 로컬 Ollama `qwen2.5-coder:7b-instruct`(범용 instruct)를 쓴다.

- 이유: 챗봇용 fine-tune 모델은 QueryIR 생성 전용이다.
- 연결: 기존 loopback 전용 transport(proxy·redirect 차단, 응답 크기 상한)를 쓴다.
- 생성 설정: temperature 0, JSON schema 제한 출력.

단계는 다음과 같다.

1. **column 선택(변형당 1회)**
   - 입력:
     - field 15개의 이름·설명·타입.
     - 모든 column의 `table.column`·선언 타입·저장 값 예시(최대 5개).
   - 출력: field마다 후보 column 하나 또는 `none`. schema의 enum이 실제 column만 허용한다.
   - 두 field가 같은 column을 고르면 표준 field 순서상 먼저인 field만 남긴다.
2. **값 처리(매핑된 field마다 1회)**
   - 입력:
     - field 설명·타입, column 선언 타입, 저장 값 예시(최대 10개).
     - 닫힌 목록 변환의 설명.
     - 표준 값 목록(아래)이 있는 field는 그 목록.
   - 출력: 변환 하나 또는 `none`. 표준 값 목록이 있는 field는 `{stored, canonical}` 쌍의 값 사전도 낸다. schema의 enum이 저장 값과 표준 값만 허용한다.
3. **결정적 검증기**(runtime과 같은 규칙):
   - 타입이 맞지 않는 변환은 버린다(ontology 타입·column 선언 타입).
   - 값 사전은 다음 경우에 버린다.
     - 식별 field이거나 column이 문자열이 아닐 때.
     - 모든 항목이 저장 값 = 표준 값(무의미)일 때.
     - 저장 값이나 표준 값이 목록 밖일 때, 또는 key가 중복될 때.
   - 변환과 사전을 함께 내면 변환만 남긴다.
4. **기준 table**: `CourseOffering.section`이 매핑된 table로 정하는 결정적 규칙이다(개설강좌 단위). AI가 고르지 않는다.
   - 같은 규칙을 규칙 기반에도 붙인 비교군(`rules+anchor`)을 둔다. 그래야 AI의 기여와 anchor 규칙의 기여를 나눌 수 있다.

### 표준 값 목록 `canonical-values-v1`

- 이수구분: `전공`·`교양`·`일반선택`
- 요일: `월`~`일`
- 학기: `1`·`2`

시스템이 쓰는 표준 표현이다. ontology YAML은 고치지 않는다(기존 평가 세트의 ontology fingerprint 보존). 제안기 입력으로만 둔다.

## 3. 비교군과 채점

- 비교군:
  - `oracle`(정답).
  - `rules`(8-08과 같음).
  - `rules+anchor`.
  - `ai`(검증기와 anchor 규칙 포함).
- 채점은 8-08과 같다: field 점수, 기준 table, live 질문 240개. oracle 만점이 아니면 실행 전체가 무효다.
- 입력 차이: 규칙은 이름·타입만 보고, AI는 저장 값 예시와 표준 값 목록도 본다. 이 차이를 결과에 적는다.
- 모델 원출력, 검증기가 버린 항목, 호출 수·시간은 Git 비추적 보고서(`.schema-adaptive/reports/variant-benchmark/`)에 둔다.
- AI 답안은 저장해 두었다가 DB 실행을 다시 할 때 재사용한다(모델을 다시 부르지 않음).

## 4. 범위 밖

- 제안을 관리자 검토 화면에 연결하는 일(8-07).
- 모델 fine-tune과 여러 모델 비교.
- prompt 반복 튜닝. prompt는 v1로 한 번 고정하고 실행한다. 결과를 보고 고치면 새 버전으로 기록한다.
- 실제 DB에 적용하는 일(L2 전 금지).

## 5. 제약

- 모델 호출은 loopback만 쓰고, 합성 catalog와 합성 값만 보낸다.
- DSN·비밀값을 prompt나 출력에 넣지 않는다.
- 합성 DB는 무작위 schema만 쓰고 정리한다.
- 사람 관문은 승인으로 표시하지 않는다. 결과는 개발자 작성 benchmark 위의 잠정치이며 L1/L3 전이다.
- push하지 않는다.
