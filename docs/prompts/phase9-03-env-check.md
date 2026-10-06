# Phase 9-03 지침: 팀원용 환경 점검 명령

## 배경

- 팀원이 평가용 DB를 만들고 화면에서 등록·평가하려면(ADR-086, [teammate-evaluation-db-guide.md](../teammate-evaluation-db-guide.md)) 각자 PC에서 `npm run dev:all`과 평가 도구가 돌아가야 한다.
- 지금은 무엇이 빠졌는지 서버를 켜 보고 오류로 알아내야 한다. 명령 하나로 확인하고 해결 방법을 알려 준다.
- L7(재현성) 준비에도 쓴다. 이 명령의 통과가 L7 통과는 아니다.

## 명령

```bash
python -m backend.adaptive.env_check
```

## 확인할 것 (읽기 전용)

1. Python 버전, 저장소의 `.venv`, 필요한 Python 패키지(설치 여부만, import하지 않음).
2. Node·npm, `node_modules`(vite, concurrently).
3. `backend/.env`: `DATABASE_URL`, `OLLAMA_MODEL` 등 키가 있는지. 값은 출력하지 않는다.
4. PostgreSQL:
   - `DATABASE_URL`(레거시 DB)에 읽기 전용 세션으로 접속해 `SELECT 1`. public schema의 table·view 수.
   - `PG_SOURCE_*` 연결마다 접속해 schema 이름 목록(팀원이 만든 schema가 보이는지).
   - `SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN`(있으면): loopback·`schema_adaptive_synthetic` 규칙 검사 후 접속.
5. Ollama: base URL이 loopback인지, `/api/tags`에 챗봇 모델과 mapping 모델이 있는지. proxy를 쓰지 않는다.
6. 포트 8000·8123·5173이 이미 쓰이는지(켜진 서버가 있으면 경고만).
7. `.schema-adaptive/`에 쓸 수 있는지(임시 파일을 만들고 바로 지운다).

## 원칙

- DSN·비밀번호는 출력하지 않는다. 모델 이름과 연결 이름(`PG_SOURCE_` 뒤 이름)은 확인에 필요해 보이되, 이상한 글자가 있으면 가린다. 접속 실패는 오류 종류 이름만 보인다(오류 문장에 호스트·사용자가 들어갈 수 있다).
- 쓰기 SQL을 실행하지 않는다. 세션은 읽기 전용, 접속 시간 제한 5초.
- 결과는 줄마다 `[OK]`·`[WARN]`·`[FAIL]`과 해결 방법. `FAIL`이 하나라도 있으면 종료 코드 1.
- 의존성(접속, HTTP, 포트, 명령 실행)은 주입할 수 있게 해 테스트에서 실제 네트워크를 쓰지 않는다.

## 확인

- 단위 테스트: 모두 정상, `.env` 없음, 접속 실패 문장에 비밀이 있어도 출력에 없음, loopback 아닌 Ollama·합성 DSN, 모델 없음, 포트 사용 중, 종료 코드.
- 실제 PC에서 한 번 실행해 결과를 확인한다(값이 출력되지 않는지 포함).
- 팀원 안내([teammate-evaluation-db-guide.md](../teammate-evaluation-db-guide.md))에 명령을 적는다.
- Luna review-only → 확인된 지적만 고침 → 전체 테스트.
