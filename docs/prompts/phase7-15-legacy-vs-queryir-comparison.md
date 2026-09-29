# Phase 7-15 구현 지침: 같은 질문으로 legacy 챗봇과 QueryIR 경로 비교

## 배경

7-12~7-14로 legacy 챗봇의 view `v_course_info`를 QueryIR 경로로도 질의할 수 있게 되었다. 7-15의 목표는 팀원이 legacy 대비 기존 기능이 유지되는지 판단할 근거를 만드는 것이다. 이를 위해 **같은 질문 세트**를 두 경로에 보내고 결과를 기준 조건과 대조한다.

두 경로의 구성 차이(공정 비교를 위해 보고서에 명시):

| | legacy 챗봇 | QueryIR 경로 |
|---|---|---|
| 모델 | `text2sql_qwen` | `text2sql_qwen`(no-system 요청 형식) |
| 모델 출력 | SQL | QueryIR |
| 전처리 | 오타·키워드 교정, entity masking, vector cache | 없음 |
| 후처리 | 기본 `LIMIT 50`, 시간표 조건이 있으면 일치 강좌의 전체 시간표 행으로 확장(최대 200행), SQL 검증·재시도 | 반사 guard, 서버 고정 카드 projection, 모델 limit |
| 표현 범위 | view의 모든 column | canonical 15개 중 매핑된 12개 |

## 범위

### 1. 질문 세트 `backend/adaptive/evaluation/data/legacy-chat-compare-v1.json`

- 개발자가 작성한 30문항(`legacy-chat-compare-questions-v1`)이다. 결과를 보기 전에 고정하며, gold가 아니므로 사람 검토(L1) 전 상태다.
- 범위(`scope`)
  - `ONTOLOGY`(22): 매핑된 field만으로 답할 수 있다.
  - `OUTSIDE_SCOPE`(5): legacy는 답할 수 있으나 현재 ontology 밖이다(학년, 수업 방식, 선수과목, field 간 비교).
  - `UNSUPPORTED_FIELD`(3): 이 DB에 없는 연도·학기를 묻는다.
- 기준: 앞의 두 범위는 기준 SQL `SELECT DISTINCT subject_code, section FROM v_course_info WHERE …`의 개설강좌 key 집합이다. `UNSUPPORTED_FIELD`는 기준 없이 "답할 수 없음"이 올바른 동작이다.

### 2. 비교 모듈 `backend/adaptive/evaluation/legacy_chat_compare.py`

- 질문 세트를 strict load한다. 기준 SQL은 고정 prefix이고 `;`·주석이 없어야 한다.
- 판정은 순수 함수로 한다. 두 경로 모두 개설강좌 key(과목 코드, 분반) 집합 R을 기준 집합 G와 비교한다.
  - 기준 있음:
    - `EXACT`: R = G
    - `CORRECT_SUBSET`: R ≠ ∅이고 R ⊂ G. 결과 상한으로 잘린 정답 일부다.
    - `EMPTY`: R = ∅, G ≠ ∅
    - `INCORRECT`: 조건에 맞지 않는 결과를 포함한다.
    - `NO_ANSWER`: 미지원·이해 못 함·오류
    - `UNKEYED`: legacy 결과에 key column이 없다.
  - 기준 없음:
    - `REFUSED`: 올바른 거절
    - `ANSWERED_WITHOUT_BASIS`: 근거 없이 행을 반환했다.
    - `EMPTY_ANSWER`
    - `NO_ANSWER`
  - "수용 가능"은 `EXACT`·`CORRECT_SUBSET`·`REFUSED`다. 정밀도(|R∩G|/|R|)와 재현율(|R∩G|/|G|)을 함께 기록한다.
- live 실행(`--live`, loopback만 사용)
  - legacy `POST http://127.0.0.1:8000/api/v1/chat/query`와 QueryIR `POST http://127.0.0.1:8123/api/adaptive/v1/chat/query`에 질문을 보낸다.
  - 기준 SQL은 server-owned DSN 환경변수로 읽기 전용 실행한다.
  - QueryIR 성공 문항은 같은 모델 출력(이해한 filter)을 서버 정책만 바꿔 다시 실행한다. 모델은 다시 부르지 않고, 기존 `answer_chat_query` 경로를 쓴다.
    - `limit200`: limit를 200으로 올린다.
    - `limit200_contains`: 추가로 문자열 field(과목명·학과명·교원명·이수구분)의 `equals`를 `contains`로 바꾼다.
  - 원자료(legacy SQL, key 집합)는 Git 비추적 `.schema-adaptive/reports/phase7-15/`에만 저장한다. stdout은 요약 수치만 출력한다.
- `--summarize RUN.json`: 문항별 판정과 경로·변형별 집계를 계산한다. 행 값과 SQL은 출력하지 않는다.

### 3. 결과 문서 `docs/phase7-15-legacy-vs-queryir-comparison-results-2026-09-29.md`

- 문항별 판정, 범위별 집계, 두 가지 서버 정책 변형의 효과, 관찰된 실패 유형, 한계를 적는다.
- 결정 항목도 정리한다: 문자열 `equals` 완화, limit 정책, 학년 등 ontology 확장.

## 제외

- 서버 정책(limit, `equals` 완화)의 실제 적용. 사용자 결정 뒤에 한다.
- ontology 확장(학년 등), legacy 변경.
- 모델 성능 확정 수치. 질문과 기준이 개발자 작성이라 L1·L3 전 잠정치다.

## 보안·데이터

- legacy DB는 팀이 구성한 유사 데이터다(사용자 확인).
- 질문 세트에는 legacy DB의 학과·과목·교원 이름이 들어간다. 커밋 문서에는 문항과 판정·집계만 적고 행 값과 SQL은 적지 않는다.
- legacy 경로 호출은 legacy의 기존 query log에 기록될 수 있다(legacy 기존 동작).

## 테스트

- 질문 세트 strict load와 거부 사례: 중복 ID, scope·기준 불일치, 허용되지 않은 SQL 형태.
- 판정 함수의 각 분기와 정밀도·재현율.
- 변형 filter 재작성: `equals`→`contains` 대상 field만 바뀌는지.
- 집계.
- fake transport로 live 실행 흐름을 확인한다: 저장 경로, stdout에 값·SQL 없음.
