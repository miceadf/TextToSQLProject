# Phase 7-05 구현 지침: private PostgreSQL startup profile

## 목표

Phase 7-04의 server-owned PostgreSQL runtime profile을 loopback synthetic prototype CLI 시작 경계에 연결한다. Git 비추적 private metadata와 별도 환경변수의 DSN을 엄격히 읽고, 이미 승인·publish된 immutable mapping만 runtime registry에 주입한다.

이번 단계는 startup configuration 한 흐름만 완결한다. frontend parser/UI, SQLite upload 변경, mapping 편집·승인·publish, Ollama 연결, 실제 CNU 데이터, 외부 PostgreSQL, legacy 시스템은 변경하거나 사용하지 않는다.

## 구현 범위

1. `backend/adaptive/api` 아래에 private PostgreSQL profile loader를 추가한다.
2. CLI에 다음 server/operator 전용 옵션을 추가한다.
   - `--repository-root PATH`: private 파일 검증 기준 저장소. 기본값은 현재 작업 디렉터리.
   - 반복 가능한 `--postgres-profile-id DATABASE_ID`: 로드할 profile ID.
3. profile ID마다 다음 고정 경로만 읽는다.
   - `.schema-adaptive/postgres-profiles/<database_id>/profile.json`
   - `.schema-adaptive/postgres-profiles/<database_id>/mapping.json`
4. 성공한 profile들을 `create_synthetic_clarification_prototype(postgres_profiles=...)`에 전달한다.
5. profile 옵션이 없으면 환경변수, private 파일, PostgreSQL driver/connection을 읽거나 호출하지 않고 기존 동작을 그대로 유지한다.

## private storage 계약

- `recording_bundle_storage.preflight_private_snapshot_path` / `read_private_snapshot_bytes`의 기존 repository/private guard를 재사용한다. 별도 약한 검사기를 만들지 않는다.
- 두 JSON 모두 저장소 내부, Git ignored, Git untracked여야 한다. symlink/junction/reparse/hardlink/redirect/escape를 기존 guard가 거부하게 유지한다.
- profile ID는 기존 PostgreSQL database ID 형식만 허용한다. ID로 고정 경로를 조합하므로 absolute path, traversal, separator를 허용하지 않는다.
- 각 파일은 작고 제한된 크기여야 한다. 기존 read helper에 크기 제한이 없으면 loader에서 읽은 직후 명시적 상한(각 256 KiB 이하)을 적용한다. 초과 파일은 내용 parsing 전 거부한다.
- UTF-8만 허용하고 duplicate JSON key를 거부한다.
- 파일이나 DSN을 수정·복사·생성하지 않는다.

## `profile.json` strict 계약

정확히 다음 필드만 허용한다. 누락, 추가 필드, 타입 coercion을 거부한다.

```json
{
  "version": "postgres-runtime-profile-v1",
  "database_id": "synthetic-postgres",
  "label": "Synthetic PostgreSQL",
  "schema": "public",
  "dsn_env": "SCHEMA_ADAPTIVE_POSTGRES_DSN_SYNTHETIC",
  "mapping_sha256": "64자리 소문자 hex"
}
```

- `database_id`는 CLI ID 및 mapping의 `database_id`와 모두 같아야 한다.
- `label`은 기존 `PostgresRuntimeProfile`의 안전한 label 형식을 만족해야 한다.
- `schema`는 기존 `PostgresConnectionConfig` schema 형식을 만족해야 한다.
- `dsn_env`는 `SCHEMA_ADAPTIVE_POSTGRES_DSN_` prefix와 대문자 영숫자/underscore만 허용하고 길이를 제한한다. 환경변수 이름이나 값은 HTTP/app public state/log/error/repr/stdout/stderr에 노출하지 않는다.
- `mapping_sha256`는 원본 `mapping.json` bytes의 SHA-256과 일치해야 한다.
- 동일 profile ID, DSN 환경변수 이름, mapping 대상의 중복은 startup 전에 거부한다.
- 입력 ID 순서와 무관하게 결과 순서는 database ID 기준으로 결정적이어야 한다.

## `mapping.json` strict 계약

- `PublishedMappingProfile`의 정확한 JSON shape만 허용한다. 모델의 기본 coercion/extra-ignore에 기대지 말고 recursive strict transport validation 또는 동등한 검증으로 extra/missing/wrong scalar type/duplicate key를 거부한다.
- `status`는 명시적으로 `PUBLISHED`여야 한다. 누락된 status의 모델 default를 허용하지 않는다.
- `database_id`, 양의 정수 `version`/`ontology_version`, fingerprint, non-empty `published_by`, timezone-aware ISO datetime, fields object를 검증한다.
- 각 field key와 value shape를 엄격히 검증하고 빈 semantic field/table/column/reviewer를 거부한다. `reason`은 string 또는 null만 허용한다.
- hash 확인 뒤 strict parse하며, 최종적으로 `PublishedMappingProfile`을 생성한다.
- Phase 7-04 runtime validation과 request-time schema fingerprint drift check는 그대로 유지한다. startup profile loader가 이를 우회하지 않는다.

## DSN 및 오류 redaction

- DSN은 `profile.json`에 절대 저장하지 않는다. 승인된 `dsn_env`가 지목하는 process environment에서만 읽는다.
- missing/empty DSN을 거부한다. DSN 문자열, 비밀번호, host, port, user, schema, 실제 path, mapping physical identifier, 원본 parser/driver 예외를 출력하지 않는다.
- loader public failure는 고정된 단일 메시지의 전용 예외로 정규화한다.
- CLI startup 실패도 기존 `synthetic prototype invalid configuration` 같은 고정 문구만 stderr에 내고 non-zero로 종료한다. 사용자 입력값을 argparse 오류에 반사하지 않는다.
- object `repr`/`str`은 redacted 상태를 유지한다.

## CLI 경계

- global module-level `app = create_synthetic_clarification_prototype()`에는 private profile을 자동 주입하지 않는다. ASGI import는 현재와 같은 synthetic-only 기본값이다.
- CLI `main`에서만 profile을 로드한다. 모든 profile strict load가 성공한 뒤 app을 조립하고 uvicorn을 시작한다.
- startup validation 실패 시 uvicorn은 시작하지 않는다.
- host는 계속 `127.0.0.1` 고정, reload/access log는 계속 비활성화한다.
- cleanup은 성공 startup과 이후 종료에서 기존처럼 한 번 실행된다. app 생성 전 실패에는 존재하지 않는 cleanup을 호출하지 않는다.

## 테스트

관련 테스트만 실행한다. 실제 PostgreSQL/Ollama/network를 사용하지 않는다.

- profile 옵션 없는 CLI가 loader/env/PostgreSQL을 건드리지 않고 기존 loopback 계약을 유지.
- 정상 private ignored/untracked metadata + mapping + env로 `PostgresRuntimeProfile` 생성, CLI factory 전달.
- 여러 ID의 결정적 정렬 및 duplicate ID/env 거부.
- tracked/not-ignored/outside-repo/symlink/reparse/hardlink/path escape 거부(기존 guard를 과도하게 중복 테스트하지 말고 연결 계약 중심).
- oversize, invalid UTF-8, duplicate JSON key, extra/missing/wrong type, 잘못된 version/ID/schema/env 이름/hash/status/time/field shape 거부.
- missing/empty env 거부.
- profile과 mapping ID 불일치 거부.
- startup 실패 시 app factory/uvicorn 미호출.
- DSN, secret marker, path, schema, physical identifier, parser 원문이 exception/repr/stdout/stderr/log에 없음.
- 기존 Phase 7-04 registry/clarification routing과 CLI/import isolation 관련 테스트 회귀 없음.

## 금지

- 실제 CNU 데이터, 실제 운영 PostgreSQL, Ollama live 호출, Docker 추가 금지.
- frontend/UI와 upload/review/publish flow 변경 금지.
- legacy 시스템 변경 금지.
- 계획/ADR/handoff/변경 이력 수정, 전체 pytest, compileall, commit, push 금지. 이는 메인 Codex가 담당한다.

## 완료 보고

긴 diff와 전체 로그를 출력하지 않는다. 한국어로 변경 파일, 핵심 계약, 관련 테스트 결과, 실패 원인, 남은 위험만 간결히 보고한다.
