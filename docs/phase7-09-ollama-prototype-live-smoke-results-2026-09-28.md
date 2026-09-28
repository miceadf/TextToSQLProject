# Phase 7-09 합성 prototype Ollama live smoke 결과 (2026-09-28)

기준: [`docs/prompts/phase7-09-ollama-prototype-live-smoke.md`](prompts/phase7-09-ollama-prototype-live-smoke.md). 완전 합성 질문(코드에 고정)과 합성 SQLite fixture만 사용했다. 모델 응답 원문·prompt·QueryIR·SQL·행 값·session token·option ID는 이 문서에 기록하지 않았다.

## 환경과 실행

- Windows 데스크톱, `.venv` Python, 로컬 Ollama `0.34.4`(`/api/version`), 기본 loopback URL.
- model tag `text2sql_qwen:latest`, `/api/tags` digest `7070730bf2f29aa3e7cbb9b0b566b1c73fd93bfeab41363e4a1f128730a3cb5a`. weight 출처·사용 권한은 확인하지 않았다.
- prompt `query-ir-v1`, timeout 60초, retry 없음.
- `--check` 1회(모델 호출 0회, `CHECKED`) 뒤 `--live-synthetic` 1회.

## smoke 결과

전체 `status=COMPLETED`, 모델 호출 4회(기대 4회), 모든 case `boundary_ok=true`.

| case ID | DB | 동작 | start | HTTP | outcome | 호출 | 경과 |
| --- | --- | --- | --- | ---: | --- | ---: | ---: |
| `p709-flat-course` | flat | `Course.name` submit | PENDING | 422 | `CONSTRAINT_INVALID_QUERY_IR` | 1 | 약 9.5초(첫 호출, model load 포함 추정) |
| `p709-flat-instructor` | flat | `Instructor.name` submit | PENDING | 422 | `CONSTRAINT_INVALID_QUERY_IR` | 1 | 약 0.7초 |
| `p709-legacy-course` | legacy | `Course.name` submit | PENDING | 422 | `CONSTRAINT_INVALID_QUERY_IR` | 1 | 약 0.6초 |
| `p709-legacy-instructor` | legacy | `Instructor.name` submit | PENDING | 422 | `CONSTRAINT_INVALID_QUERY_IR` | 1 | 약 0.7초 |
| `p709-flat-cancel` | flat | cancel | PENDING | 200 | `CANCELLED` | 0 | — |
| `p709-flat-no-match` | flat | no-match | NOT_REQUIRED | — | `NOT_REQUIRED` | 0 | — |

관찰 요약: submit 4건 중 실행 성공 0건.

## 원인 진단(비노출 구조 분류)

smoke 뒤 같은 설정으로 별도 진단 호출 3건(flat 2건 + 재확인 1건)을 in-memory로만 분류했다. 원문은 출력·저장하지 않았다.

- 응답은 모두 JSON object였고 key는 정확히 `entity`·`select`·`filters`·`limit`, 기존 `QueryIR` 검증은 통과했다. prompt semantic context는 확인한 field 하나(`available_fields`=`[selected_ref]`)로 제한되어 있었다.
- 각 응답은 filter 1개를 포함했다. 값을 확인한 1건에서 filter는 `equals`이고 값이 **질문 문자열 전체와 정확히 같았다**.
- Phase 6-09 `_reject_reflected_context`는 요청 질문·trigger·physical 이름 등 원문 경계 문자열이 QueryIR에 그대로 반사되면 `CONSTRAINT_INVALID_QUERY_IR`로 거부한다. offline 재현에서 filter 값이 질문 전체 또는 trigger `강의`일 때 같은 422가 났고, 일반 문자열 filter(`contains`/`equals`)는 성공했다.

결론: 422는 **설계된 reflection guard가 모델 출력을 거부한 결과**이며 코드 결함이 아니다. 질문 전체에 대한 `equals` filter는 의미상으로도 0행을 낼 잘못된 IR이다. guard는 완화하지 않았다.

## 해석과 한계

- 확인된 것: live Ollama에서 startup·start·cancel·no-match 호출 0회, submit당 정확히 1회, 확인된 field 하나로 축소된 semantic context 전달, 모델 출력의 validation→guard 경로 통과, 기존 고정 HTTP 오류 taxonomy와 redaction 유지.
- 확인되지 않은 것: 성공 실행 경로(compile·policy·read-only execute)의 live 통과. 이번 모델 출력이 모두 guard에서 멈췄기 때문이다. 해당 경로는 합성 transport 테스트와 Phase 5-13 평가 경로로만 검증되어 있다.
- 4건은 정확도·성능 수치가 아니다. L1/L3 전 성능 주장에 쓰지 않는다.
- 관찰된 모델 경향(축소 context에서 질문 전체를 filter 값으로 복사)은 prompt 설계 문제일 가능성이 있다. 후속 모델 비교에서 다룰 후보이며, 이번 단계에서 prompt·guard를 바꾸지 않았다. `CONSTRAINT_INVALID_QUERY_IR`가 malformed IR과 reflection 거부를 구분하지 않아 진단이 어렵다는 점도 후속 후보다.

## 사람 관문

완전 합성 입력만 사용해 E3·L5 대상 자료가 없다. 실제 학교 DB(L2)·UX 확정(L4)과 무관하며 승인 상태는 바꾸지 않았다.

## 추가 기록: 내장 SYSTEM 제거 후 재실행 (Phase 7-10, 2026-09-29)

- 이 smoke의 원래 실행은 `text2sql_qwen:latest` Modelfile의 내장 CNU `courses` SYSTEM이 주입된 조건이었다(ADR-058).
- Phase 7-10에서 runtime 요청 형식을 명시하게 한 뒤 `--ollama-request-format alpaca-raw-no-system-v1`로 `--check`(호출 0회)와 `--live-synthetic` 1회를 다시 실행했다. 경계는 `COMPLETED`(호출 4/4, cancel·no-match 0회)였다.
- `Instructor.name` submit 2건(flat·legacy)은 `EXECUTED_SUCCEEDED`, columns가 선택 field와 일치했으나 0행이었다. **성공 실행 경로(QueryIR 검증→compile→policy→read-only 실행)가 처음으로 live로 통과**했다.
- `Course.name` submit 2건은 여전히 `CONSTRAINT_INVALID_QUERY_IR`였다. 비노출 구조 분류(진단 4 호출)에서 Course는 질문 전체를 값으로 한 `equals` filter(reflection guard 거부), Instructor는 질문의 일부 문자열을 값으로 한 `contains` filter(guard 통과, 0행)였다.
- 해석: 목록 전체를 묻는 질문에 불필요한 filter를 붙이는 경향은 내장 SYSTEM과 무관하게 남는 모델 출력 특성이다. 고정 합성 질문의 `합성 스모크 P709:` 접두어가 이를 유도했을 가능성도 있다. guard·prompt는 바꾸지 않았고 prompt 개선 후보로 남긴다.
