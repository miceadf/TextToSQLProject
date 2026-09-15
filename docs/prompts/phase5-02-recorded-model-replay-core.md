# 다음 개발 프롬프트: Phase 5-02 RecordedModel 재생 코어

다음 단계인 `RecordedModel`의 **오프라인 재생 코어만** 개발해줘. 이번 작업은 작은 단위로 끝낸다. 완료 후 멈추고 후속 작업을 자동으로 시작하지 마.

## 먼저 확인

- `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5, 현재 체크포인트, 개인정보·평가 방침
- `backend/adaptive/harness/model.py`의 `QueryModel`·`FakeModel`, `HarnessRequest`와 Query IR 검증 경로
- `backend/adaptive/evaluation/runner.py`, `run_contracts.py`, `run_report.py`의 결정적 request ID·실패 집계 방식
- `docs/human-validation-checkpoints.md`의 조기 확인과 실제 모델 평가 전 승인 관문
- 작업 시작 시 `git status --short`, 최근 Conventional Commit 형식

## 목표와 경계

이미 **외부에서 기록된** 모델 응답을 파일에서 읽어 `QueryModel.generate_ir()`로 그대로 재생한다. 모델을 호출하거나 gold Query IR로 빈 응답을 채우지 않는다. 기록 파일의 구조와 재생 일치 여부만 검증하며, 이번 단계 결과를 모델 성능으로 주장하지 않는다.

현재 노트북에는 실행 중인 Ollama 서버와 PostgreSQL이 없다. 40개 synthetic gold 사례는 모두 `NEEDS_HUMAN_REVIEW`다. 세 SQLite 스키마도 독립된 다른 학교가 아니다. 이 경계는 테스트·문서에서 유지한다.

## 이번 범위

- versioned JSON recording 계약과 엄격한 loader. 입력 경로는 명시적으로 받는다.
- 각 기록의 결정적 `request_id`, `database_id`, 질문의 SHA-256 digest, runtime mapping의 안정적 fingerprint, 재생 결과를 저장한다. 모델 run 식별자와 recording schema version도 명시한다.
- 성공 기록은 **원래 JSON 값의 구조와 타입을 보존**해 반환한다. 잘못된 Query IR도 임의 수정하지 않아 기존 하네스가 `MODEL_INVALID_OUTPUT`으로 판정할 수 있게 한다.
- 기록된 모델 실패와 누락된 request는 gold로 대체하지 않고 명시적인 안전한 실패로 처리한다. runner에서 예정 attempt가 사라지지 않도록 한다.
- 중복 key, 잘못된 version, malformed JSON, 입력 계약 위반, question/database/mapping 불일치를 거부한다. 실패 메시지에 질문·응답·literal 원문을 넣지 않는다.
- 고정된 합성 recording으로 `RecordedModel`을 기존 `run_evaluation()`에 **Python API로만 주입**하는 소수의 통합 테스트를 추가한다. 기존 runner·CLI의 지표 계약은 바꾸지 않는다.
- 계획 문서의 Phase 5 진행 상태와 날짜별 변경 이력을 갱신한다. `RecordedModel` 체크박스는 이번 계약·테스트가 실제 완료된 경우에만 체크한다.

## 기록 파일 안전성

- 실제 모델 응답에는 질문·식별값이 들어갈 수 있으므로 실제 recording은 private artifact로 취급한다. 저장소에 실제 응답, 실제 CNU 데이터, 민감한 Query IR을 추가하지 않는다.
- 저장소에 넣는 테스트 자료는 **합성 값만** 사용한다. 기본 report에는 recording 원문을 복제하지 않는다.
- loader는 읽기 전용이다. 자동 다운로드, 모델 호출, 원본 파일 수정, recording 생성 기능은 넣지 않는다.
- `oracle_smoke=False`는 모델 성능의 증거가 아니다. 출처가 검증되지 않은 recording을 실제 모델 실행으로 주장하지 않도록 문서에 제한을 적는다.

## 최소 테스트

- 동일 입력의 결정적 재생; 질문·DB·mapping이 다른 기록의 오사용 차단
- JSON 객체·문자열·비정상 IR 등 원래 응답 타입 보존과 기존 검증기 실패 전달
- 기록된 오류·누락 항목이 runner의 실패 attempt로 남고 뒤 사례가 계속 실행됨
- 중복 ID, 잘못된 version, malformed JSON, 비정상 field type 거부
- 오류 메시지·기본 report에서 실제 질문·응답 값 비노출
- 합성 recording을 통한 flat/normalized/legacy 중 작은 사례 집합의 재생; 전체 40×3 재측정은 요구하지 않음
- 관련 단위 테스트, 기존 `tests/` 회귀, `compileall`, `git diff --check` 통과

## 제외 범위

- Ollama·PostgreSQL 연결과 실제 응답 수집
- recording 생성·내보내기 CLI, evaluation CLI의 새 subcommand
- 전체 40×3 benchmark 실행, 모델 정확도·지연·메모리 측정
- gold 사람 승인, Query IR·ontology·mapping·compiler 변경
- 관리자 UI, repair loop, 레거시 API 연결
- 앞선 점검에서 발견된 루트 `pytest`의 `backend/test_correction.py` import 문제 수정(별도 작업으로 기록만)

## 사용량 절감 규칙

- 이번 크기에서는 **서브 에이전트를 만들지 않는다**. 압축된 서브 에이전트 답변은 메인 대화 맥락을 줄일 수 있지만, 에이전트 실행 자체의 사용량을 없애지는 않는다.
- 현재 범위에 필요한 파일만 읽고 수정한다. 관련 테스트를 먼저 실행하고 전체 `tests/`는 최종 1회 실행한다. 긴 성공 로그는 요약한다.
- 예상 밖의 구조 변경이 필요하면 범위를 넓히지 말고 이유와 다음 작은 단계를 보고한다.
- 완료 보고는 변경 파일, 테스트 결과, 남은 위험·다음 체크포인트만 간결하게 쓴다.

## 이번 단계의 사람 확인

합성 recording 재생 코어의 자동 테스트를 위해 40개 gold의 정식 사람 승인을 요구하지 않는다. 완료 보고에는 `docs/human-validation-checkpoints.md`의 E1~E4 조기 확인 필요 여부를 짧게 알리고, 실제 모델 recording을 평가 결과로 사용하기 전 L1·L3, 실제 자료를 기록·공유하기 전 E3·L5가 필요함을 명시한다. 사람 승인 상태를 코드나 문서에서 임의로 완료 처리하지 않는다.

## 완료·커밋

기능과 테스트가 완료되면 과거 Conventional Commit 형식으로 적절히 분리 커밋한다. 커밋 전 staged diff를 확인한다. 원격 push는 하지 않는다. 작업 트리를 깨끗하게 마친다.

다음 작업은 **recording 수집·실행 CLI와 provenance 검증**으로 남긴다. 이번에는 재생 코어만 구현해줘.
