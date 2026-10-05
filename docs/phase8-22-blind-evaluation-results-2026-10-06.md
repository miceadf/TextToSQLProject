# Phase 8-22 결과: 팀원 DB 평가 실행 도구 (2026-10-06)

지침: [phase8-22-blind-evaluation-runner.md](prompts/phase8-22-blind-evaluation-runner.md). 팀원용 안내: [teammate-evaluation-db-guide.md](teammate-evaluation-db-guide.md).

## 1. 만든 것

`python -m backend.adaptive.evaluation.blind_eval check|run|report <평가 ID>`

- **질문 파일:** `.schema-adaptive/blind-evals/<평가 ID>/questions.json`(git 밖). 형식은 `blind-eval-questions-v1`이다. 정답은 과목 코드 목록이거나 `"decline"`이다.
- **check:** 형식만 검사한다. 개수와 해시만 출력하고, 오류는 질문 번호로만 알린다. 질문 내용과 정답 코드는 출력하지 않는다.
- **run**
  - 화면과 같은 챗봇 API(loopback만)로 질문마다 한 번 묻는다.
  - 실행 번호를 먼저 잡고(`run-NNN.json`), 끝나면 결과를 채운다.
  - 첫 실행 때 질문 파일 해시를 잠근다. 잠금 파일이나 앞선 실행과 해시가 다르면 실행을 거부한다.
  - 기록에는 git commit, 모델 이름(`OLLAMA_MODEL`만 읽음), 등록 상태·게시 버전, 질문별 결과(PASS/FAIL/CAPPED/ERROR), 이해한 조건, 빠진·남는 개수를 남긴다. 결과 행은 남기지 않는다.
- **report:** 실행별 집계를 보여 준다. 첫 실행과 재실행(commit)을 구분한다. 빠진 실행 번호, 질문이 다른 실행, 끝나지 않은 실행을 경고한다.

## 2. 동작 확인 (개발용 DB, 평가 수치 아님)

8-21 `code_school`을 다시 등록하고, 고정 질문 14개와 거절 질문 1개("강의실이 IT관인 수업")로 질문 파일을 만들었다.

- check: 15개(코드 14, 거절 1)만 출력.
- run 1: "첫 실행" 15/15. run 2: "재실행" 15/15, 따로 저장됨.
- 기록: 게시 버전 1, commit, 모델 이름이 들어갔다. commit에는 `+dirty`가 붙었다. 사용자의 `package.json` 변경이 커밋되지 않은 상태이기 때문이다.
- 확인 뒤 개발용 평가 폴더와 등록은 지웠다.

## 3. 리뷰

`gpt-6-luna` high review-only 지적 5건을 모두 재현하고 고쳤다. 회귀 테스트를 추가했다.

1. **loopback 밖으로 나갈 수 있었다.** 환경 변수 proxy와 redirect를 따랐다. 이제 proxy와 redirect를 쓰지 않는다.
2. **잠금 파일을 지우면 바뀐 질문으로 "재실행"할 수 있었다.** 이제 앞선 실행 기록의 해시와도 비교한다.
3. **동시에 두 번 실행하면 한 실행의 기록이 사라질 수 있었다.** 이제 묻기 전에 실행 번호를 먼저 잡는다.
4. **평가 폴더가 링크(symlink·junction)이면 private 폴더 밖을 쓸 수 있었다.** 이제 실제 경로를 확인한다. 이 Windows 계정은 symlink를 만들 수 없어 해당 테스트는 건너뛰었다.
5. **report가 빠진 실행을 알리지 않았다.** 이제 빠진 번호, 질문이 다른 실행, 끝나지 않은 실행을 경고한다.

## 4. 남은 것

- 팀원이 DB와 질문 파일을 만들면 Phase 9에서 실제로 돌린다. 그 전에는 AI가 그 파일을 열지 않는다.
- 정답이 150개를 넘는 넓은 질문, 결과가 200행 상한에 닿는 질문은 채점하지 않는다(`CAPPED`). 질문을 좁혀야 한다.
