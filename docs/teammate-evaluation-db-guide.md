# 팀원용: 평가 DB와 질문 파일 만들기

이 문서는 다른 학교 DB에 대한 적응성을 평가하려고, 팀원이 평가용 DB와 질문을 만드는 방법이다. 규칙은 2026-10-01 사용자 결정(ADR-086, [human-validation-checkpoints.md](human-validation-checkpoints.md))을 따른다.

## 1. 지켜야 할 것

1. 다른 대학의 공개 수강편람·시간표를 참고해 **흉내 낸** DB를 만든다. 학교명과 교수명은 지어낸다.
2. 질문과 정답은 **DB를 만든 사람이 먼저** 정하고 고정한다.
3. 개발자(AI 포함)는 평가 전까지 그 DB와 질문을 **보지 않는다**. 질문 파일을 채팅이나 저장소에 올리지 않는다.
4. 첫 실행 결과는 그대로 남는다. 그 뒤 고친 결과는 "재실행"으로 따로 기록된다.

## 1-1. 내 PC 환경 점검

저장소 루트에서 아래 명령을 실행한다. 빠진 것과 해결 방법을 줄마다 보여 준다. 접속 정보와 비밀번호는 출력하지 않는다.

```bash
.venv\Scripts\python.exe -m backend.adaptive.env_check
```

- `.venv`가 아직 없으면 `python -m backend.adaptive.env_check`로 실행한다. 이때도 패키지는 `.venv`가 있으면 그 기준으로 본다.

- `[FAIL]`이 있으면 그 줄의 해결 방법대로 고친 뒤 다시 실행한다.
- `[WARN]`은 하려는 일에 따라 무시해도 된다. 예: 평가용 DB 연결(`PG_SOURCE_*`)은 DB를 만든 뒤 넣는다.
- 이 명령의 통과는 L7(재현성) 통과가 아니다.

## 2. DB 만들기 (PostgreSQL)

- 합성 DB(`schema_adaptive_synthetic`)에 학교마다 schema를 하나 만든다(예: `school_a`). 레거시 DB에는 만들지 않는다.
- table·column 이름은 그 학교 자료에 맞게 자유롭게 짓는다. 한 table이어도, 여러 table이어도 된다.
- 과목 코드(학수번호)는 꼭 있어야 한다. 채점에 쓴다.
- 학점·정원·수강 인원·대상 학년·연도 같은 숫자 칸은 `integer`나 `numeric` 형식으로 만든다. 글자 칸이나 직접 만든 도메인 형식이면 게시가 막힌다(9-06). 학점을 `3/3/0`처럼 적는 글자 칸은 된다.
- 이번 개발에서 확인한 형식은 다음과 같다(8-21). 그 밖의 형식도 괜찮고, 그런 형식에서 적응성을 보는 것이 평가의 목적이다.
  - 넓은 단일 table
  - 공통코드 table
  - 교시 번호 시간표
  - 한 칸에 묶인 시간표

## 3. 화면에서 등록·검토·게시

1. `backend/.env`에 `PG_SOURCE_<이름>=<합성 DB DSN>`을 넣고 `npm run dev:all`로 켠다.
2. 화면의 **DB 변경 → PostgreSQL 등록**에서 연결과 schema를 고르고 등록한다.
3. 검토에서 항목마다 승인·다시 지정·"이 DB에 없음"을 고른다. DB를 만든 사람이 검토자다.
4. 필요하면 값 사전(약어, 코드의 뜻, 교시표)과 확장 항목을 넣는다.
5. **게시**한다. 게시가 막히면 막힌 항목과 이유, 할 일이 한 번에 나온다(9-07). 두 항목을 같은 칸에 연결했다면 한 칸에는 한 항목만 연결할 수 있으니, 남길 항목을 정하고 다른 쪽은 "이 DB에 없음"으로 둔다(9-04 리허설). 학점·시간을 `3-2-2`, `09:00-10:15`처럼 여러 값을 묶은 글자 칸에 연결하면 지금은 게시가 되어도 답이 틀리거나 오류가 날 수 있다. 그런 칸은 "이 DB에 없음"으로 두는 것이 안전하다. 챗봇에서 몇 개 물어 보는 것은 괜찮다. 단, 평가 질문 자체를 미리 물어 보지는 않는다.

## 4. 질문 파일 쓰기

`.schema-adaptive/blind-evals/<평가 ID>/questions.json` (예: `.schema-adaptive/blind-evals/school-a-v1/questions.json`). 이 폴더는 git에 올라가지 않는다.

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

- 정답 `codes`는 그 질문에 나와야 하는 과목 코드 전체다. 자기 DB에서 SQL로 뽑는다. 1~150개다.
- `"decline"`은 그 DB에 없는 정보라서 "답할 수 없다"가 맞는 질문이다.
- 질문은 학생이 실제로 칠 법한 말로 쓴다. 약어·띄어쓰기·조건 여러 개를 섞어도 된다.
- 다 쓰면 형식만 확인한다. 이 명령은 질문 내용을 보여 주지 않는다.
  ```bash
  python -m backend.adaptive.evaluation.blind_eval check school-a-v1
  ```

## 5. 실행

```bash
python -m backend.adaptive.evaluation.blind_eval run school-a-v1
```

```bash
python -m backend.adaptive.evaluation.blind_eval report school-a-v1
```

- 첫 실행 때 질문 파일이 잠긴다. 그 뒤 질문을 고치면 실행이 거부된다. 질문을 바꾸려면 새 평가 ID를 쓴다.
- 결과 파일(`run-001.json` …)에는 질문마다 PASS/FAIL/CAPPED/ERROR와 개수만 남는다. 결과 행 자체는 남지 않는다.
