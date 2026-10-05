# Phase 8-22 지침: 팀원 DB 평가 실행 도구

작성일: 2026-10-06. 사용자 결정: 진행(ADR-089 순서: 8-21 → 평가 실행 도구 → Phase 9).

## 1. 목적

- 2026-10-01 사용자 결정(ADR-086)의 독립 학교 평가 규칙을 도구로 지킨다.
  - ② 질문과 정답은 DB를 만든 팀원이 먼저 고정한다.
  - ③ AI(개발자)는 평가 전까지 그 DB와 질문을 보지 않는다.
  - ④ 첫 실행 결과는 그대로 남기고, 그 뒤 수정은 따로 표시한다.
- 평가는 화면과 같은 챗봇 API로 한다. 모델 호출은 질문당 한 번이다.

## 2. 팀원이 하는 일

1. 다른 대학 공개 수강편람을 참고해 PostgreSQL DB를 흉내 내어 만든다. 학교명과 교수명은 지어낸다.
2. 화면에서 그 DB를 등록하고, 검토와 게시를 직접 한다. 관리자 검토가 곧 L2다.
3. 질문 파일을 쓴다(아래 형식). 정답은 과목 코드 목록이거나 `"decline"`이다. `"decline"`은 그 DB에 없는 정보라서 거절이 맞는 질문이다.
   - 정답 코드는 팀원이 자기 DB에서 직접 SQL로 뽑는다.
4. 파일을 `.schema-adaptive/blind-evals/<평가 ID>/questions.json`에 둔다. 이 폴더는 git에 올라가지 않는다.
5. `check`로 형식만 확인한다. 이 명령은 질문 내용을 출력하지 않는다.

## 3. 질문 파일 형식 (`blind-eval-questions-v1`)

```json
{
  "format": "blind-eval-questions-v1",
  "database_id": "school-a",
  "author": "팀원 이름",
  "fixed_at": "2026-10-10",
  "questions": [
    {"id": "a01", "text": "컴공 전공필수 과목 보여줘", "expect": {"codes": ["CS101", "CS205"]}},
    {"id": "a02", "text": "강의실이 공학관인 수업", "expect": "decline"}
  ]
}
```

- 질문은 1~200개. `id`는 영문·숫자·밑줄·하이픈으로 쓰고 서로 달라야 한다. `text`는 1~500자다.
- `codes`는 과목 코드(챗봇 결과 카드의 과목 코드)이며 1~150개다. 결과가 너무 많은 질문은 좁혀서 쓴다(챗봇은 최대 200행).

## 4. 실행과 기록

- `python -m backend.adaptive.evaluation.blind_eval check <평가 ID>`
  - 형식 검사만 한다. 개수와 오류 위치(질문 번호)만 출력한다.
- `python -m backend.adaptive.evaluation.blind_eval run <평가 ID> [--base-url http://127.0.0.1:8123]`
  - 질문을 화면과 같은 챗봇 API로 보낸다. 주소는 loopback만 받는다.
  - 결과를 `run-001.json`, `run-002.json` …으로 저장한다. 이미 있는 파일은 덮어쓰지 않는다.
  - 첫 실행 때 질문 파일의 SHA-256을 고정한다(`questions.lock.json`). 그 뒤 질문 파일이 바뀌면 실행을 거부한다(질문을 고치려면 새 평가 ID로 한다).
  - 각 실행에 git commit, 모델 이름(`backend/.env`의 `OLLAMA_MODEL`), 시각, 등록 게시 버전을 함께 남긴다.
- `python -m backend.adaptive.evaluation.blind_eval report <평가 ID>`
  - 실행별 집계를 출력한다. 첫 실행은 "첫 실행"으로, 그 뒤는 "재실행(commit …)"으로 표시한다.

## 5. 채점

- 정답이 코드 목록인 질문
  - `PASS`: 결과 카드의 과목 코드 집합이 정답과 같다.
  - `FAIL`: 다르다. 빠진 개수와 남는 개수를 남긴다.
  - `CAPPED`: 결과가 200행 상한에 닿아 비교할 수 없다.
- 정답이 `"decline"`인 질문: `UNSUPPORTED` 또는 `NOT_UNDERSTOOD`면 `PASS`다.
- 오류 응답(HTTP 오류, 연결 실패)은 `ERROR`다. 실행은 멈추지 않는다.
- 기록에는 응답 상태, 행 수, 이해한 조건, 빠진·남는 코드 수만 남긴다. 결과 행 전체는 저장하지 않는다.

## 6. 제약

- 질문 파일과 실행 기록은 `.schema-adaptive/` 아래에만 둔다. 문서에는 집계만 쓴다.
- 이 도구는 DB에 직접 접속하지 않는다. 화면과 같은 API만 쓴다.
- AI가 만든 개발용 DB(8-17, 8-21)로는 도구 동작만 확인한다. 독립 평가 수치로 쓰지 않는다.
- push하지 않는다.
