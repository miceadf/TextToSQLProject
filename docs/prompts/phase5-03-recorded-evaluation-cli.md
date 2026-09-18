# 다음 개발 프롬프트: Phase 5-03 RecordedModel 평가 CLI

다음 단계인 **기존 recording을 evaluation runner에 연결하는 읽기 전용 CLI**만 개발해줘. 이번 작업이 끝나면 멈추고 recording 수집 기능은 시작하지 마.

## 먼저 확인

- `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5와 현재 체크포인트
- `docs/human-validation-checkpoints.md`의 E3, L1, L3, L5
- `backend/adaptive/harness/recorded.py`
- `backend/adaptive/evaluation/__main__.py`, `run_cli.py`, `runner.py`, `run_contracts.py`, `run_report.py`
- 관련 테스트와 최근 Conventional Commit 형식
- 시작 시 `git status --short`

## 목표

외부에서 이미 생성된 `recorded-model-v1` JSON을 `RecordedModel`로 읽고, 기존 synthetic gold와 flat·normalized·legacy fixture를 대상으로 `run_evaluation()`을 실행하는 CLI를 추가한다.

이 CLI는 모델을 호출하거나 recording을 생성하지 않는다. recording의 `model_run_id`와 각 요청의 context fingerprint를 검증해 재생할 뿐이다. 결과는 재생 평가 report이며, recording 출처나 실제 모델 성능의 증명은 아니다.

## 이번 범위

- `python -m backend.adaptive.evaluation run-recorded` subcommand
- 명시적 `--recording`, `--gold`, `--variants`, `--model-label`, `--output`, `--force` 인자
- `load_model_recording()`과 `RecordedModel` 재사용
- `EvaluationRunConfig.model_run_id`를 recording 값에서만 설정
- `oracle_smoke=False` 고정; 사용자가 이를 바꾸는 옵션은 만들지 않음
- 기존 fixture bundle과 `run_evaluation()` 재사용
- report의 model run ID·label·variant가 실행 입력과 일치하는지 검증
- recording·gold·output 경로 충돌 차단과 기존 atomic non-overwrite 저장 정책 재사용
- 사람이 읽는 summary에 다음 경고 표시
  - offline replay이며 live model 실행이 아님
  - gold가 `NEEDS_HUMAN_REVIEW`이면 확정 성능이 아님
  - recording의 출처는 파일 내부 주장만으로 증명되지 않음
- 합성 recording 기반 CLI 테스트
- 계획 문서의 Phase 5 상태, 다음 체크포인트, 날짜별 변경 이력 갱신

## 실행 계약

- recording 전체 계약이 잘못되면 실행 전 fatal error로 종료하고 report를 만들지 않는다.
- 누락 record, recorded provider failure, question/database/mapping mismatch는 개별 attempt 실패다. 평가 전체를 중단하거나 gold로 대체하지 않는다.
- 모델 실패가 포함된 정상 evaluation run도 report 저장에 성공한다. 이는 CLI 인프라 실패와 구분한다.
- `model_run_id`를 CLI에서 별도로 받지 않는다. recording의 값과 다른 ID를 덮어씌울 경로를 만들지 않는다.
- `model_label`은 사람이 읽는 제한된 식별자일 뿐 provenance 증명이 아니다. 기존 `EvaluationRunConfig` 형식을 따른다.
- variant와 case 순서, request ID, report core 결정성은 기존 runner 계약을 유지한다.
- 기존 `run-smoke`와 inspection CLI 동작을 깨지 않는다.

## 개인정보·저장 안전

- 실제 recording은 private artifact다. 저장소에 복사하거나 수정하지 않는다.
- CLI와 report는 recording 경로, 질문, 응답 JSON, literal, SQL, 결과 row를 출력하지 않는다.
- 오류 메시지에도 recording payload를 포함하지 않는다.
- output은 gold 또는 recording과 같은 파일, symlink, hardlink가 될 수 없다.
- 기본은 덮어쓰기 금지다. `--force`에서만 원자 교체한다.
- 테스트 recording은 합성 질문·값만 사용한다.

## 최소 테스트

- 합성 recording으로 작은 case×variant 집합 실행 및 report 생성
- report의 `model_run_id`가 recording과 같고 `oracle_smoke=False`임
- 성공 response가 기존 harness와 semantic 비교까지 전달됨
- 누락·recorded failure·context mismatch가 attempt로 남고 뒤 사례가 계속됨
- attempt 실패가 있어도 정상 run report는 저장됨
- malformed recording과 잘못된 schema version은 report 생성 전 실패
- output과 recording/gold의 동일 경로 및 same-file 충돌 차단
- 기본 non-overwrite와 `--force` atomic replacement
- stdout·오류·report에 질문·응답·절대 recording 경로가 노출되지 않음
- 기존 inspection과 `run-smoke` 회귀 없음
- 관련 테스트 후 전체 `tests/` 최종 1회, `compileall`, `git diff --check` 통과

## 제외 범위

- Ollama 또는 다른 provider 호출
- recording 생성·수집·내보내기·수정
- 모델 설정, prompt, seed, temperature, 모델 파일 hash를 새 recording schema에 추가
- PostgreSQL 및 실제 CNU 데이터 사용
- 전체 40×3 실제 모델 benchmark, 지연·GPU·메모리 측정
- 모델 정확도 또는 학교 일반화 성능 주장
- gold 사람 승인 상태 변경
- Query IR·ontology·mapping·compiler·runner 지표 변경
- 관리자 UI, repair loop, 레거시 API 연결
- 루트 `pytest`의 `backend/test_correction.py` import 문제 수정

## 사용량 절감 규칙

- 범위가 작으므로 **서브 에이전트를 사용하지 않는다**.
- 위에 지정한 파일과 직접 관련된 테스트만 먼저 읽는다. 저장소 전체 재분석은 하지 않는다.
- 기존 loader, runner, 저장 helper를 재사용하고 새 추상화는 꼭 필요할 때만 만든다.
- 개발 중에는 관련 테스트만 실행하고 전체 `tests/`는 마지막에 한 번 실행한다. 프론트엔드 파일을 바꾸지 않으므로 frontend lint/build는 실행하지 않는다.
- recording schema 변경이나 수집기 구현이 필요해지면 범위를 넓히지 말고 이유와 후속 단계로 보고한다.
- 완료 보고는 변경 파일, 검증 결과, 사람 확인, 남은 한계만 짧게 쓴다.

## 사람 확인

합성 recording CLI 테스트에는 새 사람 승인이 필요 없다. 실제 recording을 만들거나 공유하기 전 E3·L5를 확인한다. 재생 report를 모델 성능으로 사용하기 전 L1·L3를 통과해야 한다. 승인 상태를 자동으로 변경하지 않는다.

## 완료·커밋

- CLI가 existing `RecordedModel`과 evaluation runner를 우회 없이 연결함
- 실패 attempt를 숨기지 않는 결정적 privacy-safe report 생성
- 기존 CLI·runner·legacy 시스템 회귀 없음
- `compileall`, `git diff --check` 통과, working tree clean
- 과거 Conventional Commit 형식으로 CLI, 테스트, 문서를 적절히 분리 커밋
- 각 커밋 전 staged diff 확인, 원격 push 금지

다음 작업은 **live provider의 응답을 안전하게 recording으로 수집하는 writer/capture 계약**으로 남긴다. 이번에는 읽기 전용 평가 CLI만 구현해줘.
