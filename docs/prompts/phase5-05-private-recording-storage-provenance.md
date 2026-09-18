# 다음 개발 프롬프트: Phase 5-05 Private recording storage와 provenance manifest

다음 단계인 **private recording atomic storage와 선언형 provenance manifest만** 개발해줘. 완료 후 멈추고 provider 호출·수집 CLI·evaluation 연동은 시작하지 마.

## 먼저 확인

- `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5와 현재 체크포인트
- `docs/human-validation-checkpoints.md`의 E3, L1, L3, L5
- `backend/adaptive/harness/recorded.py`, `recording_capture.py`
- `backend/adaptive/dataset/pseudonym_storage.py`의 generation·manifest-last·private path 정책
- `tests/adaptive/test_recorded_model.py`, `test_recording_capture.py`
- 최근 Conventional Commit 형식과 시작 시 `git status --short`

## 목표

완성된 `ModelRecording`을 저장소의 gitignore된 private workspace에 결정적으로 저장하고, payload를 복제하지 않는 versioned provenance manifest와 함께 다시 검증해 읽을 수 있게 한다.

이번 provenance는 caller가 제공한 정보를 구조화하고 fingerprint로 묶는 **선언형 metadata**다. 실제로 특정 모델·설정에서 생성되었다는 암호학적 증명이나 독립 검증은 아니다.

## 이번 범위

- versioned `RecordingProvenance` Pydantic 계약
- versioned `RecordingBundleManifest`와 artifact metadata 계약
- 결정적 canonical JSON과 SHA-256 fingerprint
- `ModelRecording` 성공·실패·전체 record 수 집계
- content-addressed immutable generation
- recording artifact를 먼저 쓰고 manifest를 마지막 commit marker로 게시
- 기본 overwrite 금지와 명시적 overwrite 정책
- 저장 직후 byte hash·Pydantic 계약·record count 재검증
- bundle loader에서 manifest, recording hash, generation ID와 metadata 교차 검증
- `.schema-adaptive` 아래 private 경로만 허용
- symlink·git-tracked target·불완전 bundle·변조 artifact 차단
- 오류 메시지에서 recording payload·질문·응답·절대 경로 비노출
- 합성 recording 기반 storage·loader 테스트
- 계획 문서의 Phase 5 상태와 다음 체크포인트 갱신

## provenance 계약

최소한 다음 정보를 가진다. 실제 필드명은 기존 명명 규칙에 맞추되 의미는 유지한다.

- provenance schema version
- `verification_status=DECLARED_UNVERIFIED` 고정
- provider kind: `SYNTHETIC`, `OLLAMA`, `OPENAI_COMPATIBLE`, `OTHER` 중 하나
- provider ID와 provider/adapter version
- model ID
- 선택적 model artifact SHA-256
- prompt template SHA-256
- generation configuration SHA-256
- collector/tool version

provider ID, model ID, version은 제한된 길이의 안전한 label이어야 한다. 로컬 파일 경로, URL credential, prompt 원문, 환경변수, 사용자 이름을 넣지 않는다. hash 입력의 원문이나 비밀값은 manifest에 저장하지 않는다.

같은 recording과 같은 provenance는 동일한 generation ID와 manifest core를 만든다. timestamp, 임시 경로, UUID, hostname처럼 실행마다 달라지는 값은 넣지 않는다. 실제 수집 시각이 필요하면 후속 실행 log에서 별도로 다루고 deterministic bundle identity와 섞지 않는다.

## bundle 저장 계약

기존 pseudonymization bundle의 검증된 패턴을 재사용하되 recording에 필요한 최소 구조만 만든다.

```text
<private-output>/
  manifest.json
  generations/
    <generation-id>/
      recording.json
```

- generation ID는 recording bytes와 provenance canonical metadata를 포함한 material의 SHA-256으로 계산한다.
- `recording.json`은 기존 `ModelRecording`의 결정적 UTF-8 JSON 직렬화다.
- manifest에는 상대 경로, byte 수, SHA-256, recording schema version, model run ID, record count와 provenance만 둔다.
- 질문·response JSON·literal·SQL·absolute path를 manifest에 복제하지 않는다.
- generation은 불변이다. 같은 ID가 이미 있으면 모든 bytes와 계약이 정확히 같은 경우만 재사용한다.
- 새 generation을 임시 directory에 완성·검증한 뒤 publish한다.
- `manifest.json`을 마지막에 원자적으로 게시한다. 기본은 기존 manifest를 덮어쓰지 않는다.
- overwrite가 허용되더라도 기존 generation을 수정하지 않고 commit marker만 검증된 generation으로 교체한다.
- 저장 실패 시 이전 정상 manifest를 손상시키거나 일부 generation을 정상 bundle로 보이게 하지 않는다.

## private path와 안전 정책

- caller가 `repository_root`와 output directory를 명시한다.
- output은 repository의 `.schema-adaptive`보다 아래에 있어야 한다.
- repository root, `.schema-adaptive` 자체, tracked path에는 쓰지 않는다.
- private root, output, generations, artifact, manifest의 symlink redirect를 거부한다.
- git tracking 여부를 확인할 수 없으면 안전하게 실패한다.
- loader는 manifest가 가리키는 경로가 bundle 밖으로 탈출하지 못하게 한다.
- 예상하지 않은 artifact, 잘못된 hash/count/version/model run ID를 명시적으로 거부한다.
- 오류에는 payload, 절대 경로, 내부 예외 원문을 넣지 않는다.

## loader 계약

- loader 입력은 bundle directory 또는 manifest path 중 하나로 명확히 고정한다.
- manifest가 없거나 generation이 불완전하면 실패한다.
- recording bytes의 hash와 크기를 먼저 확인하고 기존 strict `load_model_recording()`과 동일한 JSON 계약으로 검증한다.
- manifest의 model run ID·schema version·record count가 실제 recording과 정확히 같아야 한다.
- provenance fingerprint와 generation ID를 다시 계산한다.
- 성공 시 검증된 `ModelRecording`과 manifest를 반환하되 payload를 stdout에 출력하지 않는다.
- loader는 bundle을 수정하거나 자동 복구하지 않는다.

## 최소 테스트

- 합성 `ModelRecording`과 provenance의 정상 bundle 저장·load
- 같은 입력의 동일 canonical bytes, hashes, generation ID와 manifest core
- 성공·실패·전체 count 정확성
- provenance 필수 field, enum, label 길이, SHA-256 형식 검증
- `DECLARED_UNVERIFIED` 이외 상태 거부
- prompt·configuration 원문 대신 hash만 저장됨
- 기본 overwrite 차단과 명시적 overwrite의 manifest-only 교체
- 기존 immutable generation이 변경되지 않음
- recording, manifest, hash, byte count, model run ID, record count 변조 탐지
- incomplete generation과 missing manifest 거부
- bundle 밖 상대 경로와 path traversal 거부
- `.schema-adaptive` 밖, repository root, symlink, tracked target 차단
- 저장 실패 시 이전 manifest 보존 및 임시 artifact 정리
- 오류·manifest에 질문·응답·literal·절대 경로 비노출
- loader가 기존 `RecordedModel`에서 재생 가능한 recording 반환
- 기존 capture·replay·evaluation 테스트 회귀 없음
- 관련 테스트 후 전체 `tests/` 최종 1회, `compileall`, `git diff --check` 통과

## 제외 범위

- Ollama·HTTP·PostgreSQL 연결
- provider adapter와 예외 분류
- live response 수집·capture orchestration
- storage·capture·evaluation CLI 추가 또는 변경
- `run-recorded`에서 bundle 직접 읽기
- 실제 recording과 실제 CNU 데이터 사용
- 암호화, 서명, remote attestation, 비밀키 관리
- latency·GPU·메모리 측정과 모델 성능 주장
- Query IR·mapping·compiler·runner 변경
- 관리자 UI, repair loop, 레거시 API 연결
- 루트 `pytest`의 기존 import 문제 수정

## 사용량·서브 에이전트 규칙

- 기본은 메인 에이전트 단독 구현이다. provenance 계약과 atomic storage가 같은 bundle 불변식을 공유하므로 병렬 builder는 만들지 않는다.
- 다만 Windows path·symlink·atomic publish·tamper 경계가 예상보다 커져 독립 보안 검토가 실질적으로 필요한 경우에만 **리뷰 에이전트 1명**을 사용할 수 있다. 이때 코드를 분담하지 말고 완성된 diff의 경로 탈출·부분 저장·payload 노출만 검토하게 한다.
- 이는 서브 에이전트의 전면 금지가 아니다. 후속 provider adapter와 수집 CLI가 서로 독립적인 구현 단위로 커지면 제한적으로 분담할 수 있다.
- 지정한 파일과 직접 관련된 storage 테스트만 읽고 저장소 전체를 다시 분석하지 않는다.
- pseudonym storage 패턴을 참고하되 recording에 불필요한 구조를 복사하지 않는다.
- 개발 중에는 관련 테스트만 실행하고 전체 `tests/`는 마지막에 한 번만 실행한다. 프론트엔드 검사는 실행하지 않는다.
- provider 호출이나 CLI 변경이 필요하면 범위를 넓히지 말고 후속 단계로 보고한다.
- 완료 보고는 변경 파일, 검증 결과, 남은 한계와 사람 확인만 간결하게 작성한다.

## 사람 확인

합성 recording storage 테스트에는 새 사람 승인이 필요 없다. 실제 recording을 저장·반출·공유하기 전 E3·L5를 확인한다. bundle provenance는 선언 정보이며 승인 증거가 아니다. recording 기반 결과를 모델 성능으로 확정하기 전 L1·L3를 통과해야 한다.

## 완료·커밋

- private atomic bundle 저장과 strict loader가 결정적으로 동작
- manifest와 generation이 recording·provenance에 content-bound됨
- payload 비노출과 path·symlink·tamper 안전 정책 검증
- 기존 capture·replay·evaluation 회귀 없음
- `compileall`, `git diff --check` 통과, working tree clean
- 과거 Conventional Commit 형식으로 contracts, storage/loader, 테스트, 문서를 적절히 분리 커밋
- 각 커밋 전 staged diff 확인, 원격 push 금지

다음 작업은 **provider-neutral capture orchestrator와 실패 분류 계약**으로 남긴다. Ollama adapter와 실제 수집 CLI는 그 이후 데스크톱 단계에서 개발해줘.
