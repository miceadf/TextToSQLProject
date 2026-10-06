# 시연 절차: 화면에서 DB를 등록하고 바로 질문하기 (프로토타입)

작성일: 2026-09-30. Phase 8-16 browser smoke에서 실제로 끝까지 돌린 순서다.

> **주의 (사람 관문)**
> - 실제 학교 자료를 쓰거나 외부에서 시연하기 전에는 E3/L5 판단이 필요하다([human-validation-checkpoints.md](human-validation-checkpoints.md)).
> - 외부 시연에서는 SQL 표시를 끈다(`SHOW_SQL=0`). 켜 두면 실제 table·column 이름이 보인다.
> - 모든 결과는 L1/L3 전 잠정치다.

## 1. 준비

1. `backend/.env`에 다음을 둔다.
   - `DATABASE_URL`: 레거시 PostgreSQL. 화면에서는 "기본 PostgreSQL 서버"로 보인다.
   - `OLLAMA_BASE_URL`: loopback, 예: `http://127.0.0.1:11434`.
   - `OLLAMA_MODEL`: 챗봇 모델. 예: `text2sql_qwen:latest`.
   - 선택 항목:
     - `MAPPING_MODEL`: mapping 제안 모델. 없으면 `qwen2.5-coder:7b-instruct`를 쓴다.
     - `PG_SOURCE_<이름>=<DSN>`: 다른 PostgreSQL 연결을 추가한다.
     - `SHOW_SQL=0`: SQL 표시를 끈다.
2. Ollama에 두 모델이 있는지 확인한다. `ollama list`에 `text2sql_qwen`과 `qwen2.5-coder:7b-instruct`가 보여야 한다.
3. 서버 세 개(레거시 8000, adaptive 8123, 화면 5173)를 켠다.
   ```bash
   npm run dev:all
   ```
4. adaptive 상태 줄을 확인한다: `chat_sql=shown, mapping_ai=enabled, ollama=enabled, pg_registration=enabled (N sources)`.
5. 브라우저에서 `http://localhost:5173`을 연다.

## 2. 시연 순서 (약 10분)

| 단계 | 화면 조작 | 말할 요점 |
|---|---|---|
| 1 | **DB 변경** → "PostgreSQL 등록"에서 연결 `기본 PostgreSQL 서버`, schema `public`. Database ID `demo-legacy`, Label `Legacy DB (screen)` → **등록** | 접속 정보는 서버에만 있다. Label은 영문·숫자만 쓴다. AI 제안에 20~30초 걸린다. |
| 2 | 결과 요약에 "제안: AI 제안", table 8개·column 38개 | 로컬 모델이 column 이름과 저장 값 예시를 보고 field마다 첫 후보를 골랐다. |
| 3 | 검토자 이름 → **mapping 검토 시작** | 사람 검토 없이는 게시되지 않는다. |
| 4 | 13개 field **승인**. 학기(`enrollment.semester`)와 개설 연도(`course_year`)는 사유를 적고 **이 DB에 없음** | **이름 함정 사례:** `course_year`는 수강 대상 학년이고, `enrollment`는 학생 수강 기록 table이다. 사람이 걸러야 하는 부분이다. |
| 5 | 값 사전 **새로고침** → field `학과명` → `공과대학 컴퓨터융합학부` → 약어 `컴융, 컴융부` → **추가** | 약어는 학교마다 다르므로 DB별 데이터로 넣는다. **약어 후보(규칙/규칙+AI)** 버튼도 보여 준다. 겹치면 막고, 다른 글자 속에 들어 있으면 경고한다. |
| 6 | **사전 적용해 게시** → "게시했습니다 (mapping v1)" | 변환(학점 `3/3/0`, 시간, 요일)과 기준 table은 서버가 규칙으로 정했다. 서버 재시작은 필요 없다. |
| 7 | 페이지 새로고침 → DB 변경 → "등록된 PostgreSQL DB"에서 `게시됨(v1)` → **열기** | 새로고침해도 검토를 다시 열 수 있다. |
| 8 | DB 목록에서 `Legacy DB (screen)` 선택 → **적용** → 챗봇 열기 | 목록에 바로 추가됐다. |
| 9 | "컴융 전공 과목 보여줘" | 82건. 이해한 조건은 `학과명 = 공과대학 컴퓨터융합학부, 이수 구분에 "전공" 포함`. SQL 패널에 `dept_name = '공과대학 컴퓨터융합학부' AND category LIKE '%전공%'`. |
| 10 | "학과공통 과목 보여줘", "월요일 오전 수업 보여줘" | 약어 없이도 DB 값 조각(`학과공통`)과 요일·시간을 이해한다. |
| 11 | (개발자 profile `Legacy DB (QueryIR)`에서) "컴융 3학년 전공 과목 보여줘" | 대상 학년(8-18)으로 33건. 화면 등록한 DB에서는 검토에서 대상 학년을 `subject.target_year`에 연결하면 같다. |
| 12 | (개발자 profile `Legacy DB (QueryIR)`에서) "교111 강의실 수업 보여줘", "교111에서 하는 수업" | 이 DB에 없는 정보는 "없어 답할 수 없습니다"로 답한다. 0건이면 어느 조건이 맞지 않았는지 알려 준다(8-20). |

단계 9~10은 smoke에서 확인한 결과다. 모델 출력은 매번 같다고 보장할 수 없다(temperature 0이어도 환경에 따라 달라질 수 있음).

## 2-1. (선택) 다른 학교 DB 장면 (약 5분, Phase 8-17)

모델이 레거시 DB에 묶이지 않는다는 것을 보여 주는 장면이다. 데이터는 모두 지어낸 합성 데이터다.

준비(한 번만):
1. 합성 DB DSN을 `SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN`에 두고 schema를 만든다. 이 DSN은 loopback, DB 이름 `schema_adaptive_synthetic`이어야 한다.
   ```bash
   python -m backend.adaptive.fixtures.other_school create
   ```
2. `backend/.env`에 `PG_SOURCE_OTHER_SCHOOL=<같은 DSN>`을 넣고 서버를 다시 켠다. 화면 연결 목록에 `other-school`이 보인다.

| 단계 | 화면 조작 | 말할 요점 |
|---|---|---|
| 1 | 연결 `other-school`, schema `other_school`, ID `other-school`, Label `Other School` → **등록** | 이름이 `hg_nm`, `gm_nm`처럼 전혀 다르고, 요일은 `MON`, 시간은 `0900`이다. |
| 2 | 검토: 13개 승인, 학기 → `gangjwa.hakgi_cd`, 교수명 → `gyosu.gs_nm`, 대상 학년 → `gyogwamok.suggang_hangnyeon` remap | AI가 처음 보는 이름에서도 대부분 맞게 골랐다. 대상 학년은 AI가 학기 코드로 잘못 골라 사람이 고친다(8-18). |
| 3 | 값 사전: 학과 `소웨`·`전전`·`기공`, 학기 `10=1학기`·`20=2학기`, 요일 `MON=월요일` … | 약어와 코드의 뜻은 그 학교 관리자가 넣는다. 코드표 table이 있으면 "코드표에서 가져오기"로 한꺼번에 넣는다(8-24). |
| 3-1 | 확장 항목: 이름 `강의실`, key `classroom`, 강의 시간, `siganpyo.gangui_sil`, 글자 → **추가/수정**. 필요하면 `단과대학`(`hakgwa.dh_nm`, 학과)도 | 공통 항목에 없는 그 학교만의 정보를 코드 수정 없이 더한다(8-19). |
| 4 | **사전 적용해 게시** → DB 선택 | 강의 목록에서 주 2회 수업이 한 줄에 두 시간으로 보인다. |
| 5 | "소웨 전공필수 과목 보여줘", "금요일 오전 수업 보여줘", "소웨 1학년 과목 보여줘", "IT관 303호 수업 보여줘", "IT대학 과목 보여줘" | SQL 패널에 이 학교의 table 이름이 보인다. 시간은 `HH:MM`으로 바꿔 비교한다. 대상 학년이 카드 상세에 보인다. |

지우기(게시한 DB는 켜 있는 서버에 남으므로 서버를 끈 뒤): `python -m backend.adaptive.fixtures.other_school drop`, 그리고 `.schema-adaptive/postgres-registrations/other-school`·`.schema-adaptive/postgres-profiles/other-school`.

## 3. 되돌리기 (다시 시연하기 전)

1. 서버를 끈다(Ctrl+C).
2. 시연용 등록을 지운다.
   ```bash
   rm -rf .schema-adaptive/postgres-registrations/demo-legacy .schema-adaptive/postgres-profiles/demo-legacy
   ```
3. `legacy-base`·`legacy-view`(개발자가 만든 profile)는 지우지 않는다.
4. 레거시 DB는 읽기만 했으므로 되돌릴 것이 없다.

## 4. 알려진 한계 (질문이 나오면)

- 업로드한 SQLite DB는 QueryIR 챗봇·값 사전을 지원하지 않는다. 이번 개발 범위는 PostgreSQL 전용이다(ADR-089).
- PostgreSQL 등록에는 버전 되돌리기와 test query 미리보기가 아직 없다.
- 규칙 약어 후보에는 어색한 것도 섞인다. 모두 관리자 승인을 거친다. 한 글자 약어는 넣을 수 없다.
- 공통 항목 밖 개념(강의실, 수업 방식, 여석 등 11개)을 물으면, 그 DB에 확장 항목이 없을 때 "이 DB에는 강의실 정보가 없어 답할 수 없습니다"라고 답한다(8-20). 목록에 없는 말은 여전히 다른 항목에 들어갈 수 있다. 0건이면 "분반에서 '교111' 값을 찾지 못했습니다"처럼 어느 조건이 맞지 않았는지 알려 준다.
- 챗봇 모델은 레거시 데이터로 fine-tuning된 7B 모델이다. 복합 조건(예: "오후" + 요일 + 학점)에서는 조건을 빠뜨릴 수 있고, SQL 패널로 확인할 수 있다.
