# Phase 6-13 구현 지침: 합성 clarification ASGI 프로토타입

## 목표

Phase 6-12의 `create_clarification_http_app()`에 완전 합성 dependency를 조립해 팀원이 로컬 HTTP로 start → option 선택 → submit → 읽기 전용 결과를 재현할 수 있는 독립 ASGI 프로토타입을 만든다.

이 단계는 배포용 서비스가 아니다. legacy app, 실제 CNU 자료, Ollama, PostgreSQL, React, 외부 네트워크와 연결하지 않는다.

## 구현 범위

1. `backend/adaptive/api/synthetic_prototype.py`를 추가한다.
   - 공개 version 상수를 둔다.
   - 고정 synthetic database ID 하나만 허용한다.
   - 기존 `DEFAULT_SEED`와 `build_flat_database()`로 임시 SQLite DB를 만든다.
   - 최소 runtime `Ontology`, `Published MappingProfile`, `DetectorRuleSet`을 코드 내부 합성 상수로 만든다.
   - context loader는 고정 database ID만 받고 매 호출마다 방어적 복사본을 반환한다.
   - deterministic constraint-aware provider는 선택된 semantic field에 대응하는 하나의 유효 `QueryIR`만 반환한다. 질문에서 임의 SQL이나 물리 식별자를 해석하지 않는다.
   - 기존 `ClarificationSessionCoordinator`, `SQLiteReadOnlyExecutor`, `SingleTableSQLiteCompiler`, `ConservativeSqlPolicy`, Phase 6-12 factory만 조립한다.
   - 임시 directory 수명은 app 수명에 묶고 종료 시 정리한다. 중복 종료도 안전해야 한다.
   - `create_synthetic_clarification_prototype()` factory와 import 가능한 `app`을 제공한다.

2. 안전한 로컬 실행 진입점을 제공한다.
   - `python -m backend.adaptive.api.synthetic_prototype`로 실행한다.
   - host는 `127.0.0.1`로 고정한다. 외부 bind option을 제공하지 않는다.
   - port만 제한된 정수로 선택 가능하게 한다.
   - reload를 끄고 access log를 끈다.
   - `.env`, 실제 DB URL, Ollama 설정을 읽지 않는다.
   - 시작 시 질문·token·SQL·행·경로·secret을 출력하지 않는다.

3. `docs/synthetic-clarification-prototype.md`에 짧은 재현 절차를 작성한다.
   - 안전 실행 명령, 고정 database ID, start/submit JSON 예시를 제공한다.
   - start 응답에서 받은 실제 `session_token`과 선택한 `option_id`를 submit에 넣도록 설명한다.
   - 합성/로컬/무인증/비배포 경계를 분명히 적는다.
   - 결과를 모델 정확도나 실제 데이터 검증으로 해석하지 않는다.

4. 필요하면 `backend/adaptive/api/__init__.py`에 factory/version/database ID만 공개한다. module-level `app`을 package import에서 재수출해 불필요한 임시 DB side effect를 만들지 않는다.

## 계약과 안전 조건

- Phase 6-12 endpoint와 HTTP response/error 계약을 변경하지 않는다.
- legacy `backend.main`을 import·수정·mount하지 않는다.
- 외부 입력으로 ontology, mapping, rules, provider, executor, QueryIR, SQL, DB path를 주입받지 않는다.
- SQLite는 기존 read-only executor로만 질의한다.
- 합성 DB 밖 파일, 네트워크, 환경변수, private bundle에 접근하지 않는다.
- provider는 선택 constraint를 정확히 준수한다. 알 수 없는 semantic ref는 fixed provider failure로 닫는다.
- prototype 생성 실패와 CLI 설정 오류는 원문 데이터나 로컬 경로를 노출하지 않는 고정 오류로 종료한다.
- public response는 기존 Phase 6-12 redaction을 그대로 유지한다.
- session은 기존 메모리 TTL/at-most-once 한계를 그대로 가진다. 인증, rate limit, cross-process persistence를 추가하거나 주장하지 않는다.

## 테스트

`tests/adaptive/test_synthetic_clarification_prototype.py`에 합성 테스트를 추가한다.

- factory가 만든 app으로 start → 각 option 선택 → submit 성공을 end-to-end 검증한다.
- 반환 row/column이 `DEFAULT_SEED`의 해당 semantic field와 일치함을 검증한다.
- cancel, token 재사용, no-match `NOT_REQUIRED`, 알 수 없는 database ID를 검증한다.
- 고정 dependency와 방어적 context 복사, read-only DB, 종료 정리와 중복 종료를 검증한다.
- CLI가 loopback/reload off/access-log off만 사용하는지 실제 서버를 열지 않고 monkeypatch로 검증한다.
- import가 legacy, Ollama, PostgreSQL, `.env`를 불러오지 않는지 검증한다.
- 응답·stdout·stderr·log에 SQL, token, 임시 경로, 질문 원문, secret이 새지 않는지 검증한다.
- Phase 6-12 관련 테스트도 함께 실행한다.

## 작업 제한

- 구현 중 신규 테스트와 Phase 6-08~12 관련 테스트만 실행한다.
- 전체 pytest, compileall, `git diff --check`, commit, push는 실행하지 않는다.
- 계획 문서, ADR, checklist, changelog, handoff는 수정하지 않는다.
- 긴 diff와 전체 테스트 로그를 응답에 출력하지 않는다. 변경 파일, 핵심 계약, 실패 원인, 테스트 요약, 남은 위험만 한국어로 간결히 보고한다.

