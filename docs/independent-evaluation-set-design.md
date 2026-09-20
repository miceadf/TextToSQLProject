# 독립 합성 평가 세트 설계 기준

> 상태: **30건 작성·자동 검증 완료, 사람 검토·동결 전**. 새 세트를 모델에 실행하거나 확정 성능을 발표하지 않는다.
>
> 작성: 2026-09-19 · 초안: 2026-09-20 (`fixture-v1-independent-questions`, P2-041~070)

## 초안 현황 (2026-09-20)

- `backend/adaptive/evaluation/data/fixture_v1_independent_cases.yaml`에 30건을 작성했다. 기존 40건 파일은 수정하지 않았다.
- 정식 30~50건 조건의 `load_evaluation_set()`·`validate_evaluation_set()`을 통과했다. 0건 1개·1건 다수·여러 건을 포함하고 단일·복수 filter와 서로 다른 semantic field·operator를 사용한다.
- flat·normalized·legacy fixture에서 gold IR 90회(30×3) 실행이 기대 semantic 결과와 일치함을 확인했다. 이는 gold와 fixture의 자기 일관성이며 모델 정확도가 아니다.
- `backend/adaptive/evaluation/isolation.py`의 `check_set_isolation()`으로 개발 세트와의 분리(benchmark ID·case ID·정규화 질문 문장·paraphrase family·`_intent_identity()` 기준 intent)를 고정 code로 검증했다.
- 30건 모두 `NEEDS_HUMAN_REVIEW`이며 `reviewer_id`가 없다.
- 자동 검사로 증명하지 못하는 부분: 의미는 비슷하지만 문장이 다른 질문의 유사도, 한국어 질문의 실제 의도와 gold IR의 일치, 기대 행의 업무적 타당성. 사람 검토(L1·E2)와 동결이 남았다.
- 사람 결정 이전에 진행 가능한 합성 작업: 커버리지 리포트 생성, FakeModel oracle smoke 점검(모델 점수로 발표하지 않는다).

## Phase 5-17 문항 정리·커버리지·oracle smoke (2026-09-20)

- 문항 수정 근거: P2-057의 “13:00 이후”는 경계 포함 여부가 모호해 `greater_than 13:00`의 배타 조건이 드러나도록 “13:00보다 늦게”로 고쳤다(IR·기대 결과 불변). P2-059의 “요일에 'UE'가 들어가는”은 내부 요일 코드에 의존해 부자연스러우므로 “화요일에 열리는”으로 고치고 조건을 `day equals TUE`로 바꿨다(IR·기대 결과·intent family `tuesday-class` 일치 확인, 개발 세트와 intent 격리 유지). 나머지 28건은 질문·IR의 경계·단위·코드 표현이 일치해 수정하지 않았다. ID·benchmark ID·`NEEDS_HUMAN_REVIEW`·reviewer 공란을 보존하고 version을 올리지 않았다.
- 초안 gold set fingerprint(동결 아님): `8c797489ab04c4bfe76dfa72c407df3aedabd00ca44558390683ab8a276c419e`.
- 독립 세트 단독 커버리지(`python -m backend.adaptive.evaluation --input ...`, 개발 세트와 합산하지 않음): 30건 전건 `NEEDS_HUMAN_REVIEW`. 15개 semantic field 전부 사용. operator별 case 수 equals 20·contains 2·greater_than 4·greater_than_or_equal 3·less_than 2·less_than_or_equal 2. filter 수 1건 17·2건 11·3건 2. 결과 0건 1·1건 22·여러 건 7. normalized JOIN 단계 0건 5·1건 12·2건 12·3건 1. `coverage_gaps`는 사람 검토 대기·6개 offering 한계·연도/학기 무변별 3건뿐이며 0건·다단계 JOIN 부재 지적은 없다. case별 원문 표나 전체 JSON은 공개 문서에 두지 않는다.
- oracle smoke(`run-smoke --gold <독립 세트>`, 임시 비추적 경로 출력): 90 attempts 모두 구조 exact·실행 성공·semantic 일치, 실패 코드 없음, `config.oracle_smoke=true`, cross-schema 30/30 일치. gold IR을 그대로 응답받는 자기 일관성 점검이며 모델 정확도가 아니다. 검증용 임시 report는 Git에 추가하지 않았다.
- 한계: 커버리지·smoke 통과가 질문의 자연스러움·gold 의미·동결을 대체하지 않는다. L1·E2 사람 검토와 동결이 남았다.

## 목적

Phase 5-13/14의 개발 세트(40건)는 v1 결과를 본 뒤 v2를 설계하는 데 사용되었다. 같은 세트에서 반복 비교하면 개발 세트에 과적합된 프롬프트 선택이 일반화 성능으로 오인될 위험이 있다. 독립 합성 평가 세트는 다음을 보장한다:

1. 개발 세트의 질문 문장·paraphrase family를 재사용하지 않는다.
2. 기존 40건의 질문 의도(intent family)를 완전히 분리하거나, 질문 의도를 재사용하더라도 문장·paraphrase를 독립 생성한다.
3. gold 사람 승인 절차를 명시적으로 기록한다.

## 독립성 분리 기준

### 질문 격리 (Question Isolation)

- **문장 격리**: 기존 40건의 질문 원문·paraphrase를 그대로 복사하지 않는다.
- **paraphrase family 격리**: 기존 paraphrase family ID를 재사용하지 않는다.
- **의도 격리 (선택)**: 가능하면 기존 intent family를 재사용하지 않는다. 재사용할 때는 반드시 문장·paraphrase가 달라야 하며, benchmark version을 올린다.

### 스키마 격리

- 같은 `FixtureSeed`를 사용하면 **question holdout**이지 unseen school이 아니다.
- 동일 합성 스키마의 질문 분리는 논문에서 "합성 스키마 간 적응"으로 기술한다. "실제 다른 학교 일반화"라고 기술하지 않는다.
- 실제 unseen school 평가는 `docs/human-validation-checkpoints.md`의 E4/L6 관문을 따른다.

### 모델·프롬프트 격리

- 개발 세트에서 사용한 prompt 버전·전송 설정을 독립 세트 평가에 그대로 쓸 수 있다.
- 그러나 개발 세트 결과를 본 뒤 prompt를 선택한 경우, 독립 세트에서는 **동시에** 모든 후보 prompt를 평가하거나 선택 기준을 사전에 고정한다.
- 모델 tag·digest를 개발 세트와 독립 세트에서 각각 기록한다.

## 작성·검토·동결 순서

### 1단계: 작성 (Authoring)

- `FixtureSeed v1`의 offerings과 ontology를 재사용할 수 있다 (question holdout).
- 새 질문·gold Query IR·기대 결과를 작성한다.
- 질문은 한국어이며 기존 40건과 다른 문장을 사용한다.
- gold Query IR의 entity는 `CourseOffering`으로 고정한다 (현재 fixture 범위).
- 기존 `_intent_identity()`의 intent key 중복을 피한다 (의도 격리 선택 시).
- 모든 새 case는 `NEEDS_HUMAN_REVIEW` 상태로 시작한다.

### 2단계: 자동 검증

- `validate_evaluation_set()`을 통과해야 한다.
- fingerprint drift, 중복 질문, 물리 스키마 식별자 노출, offering key 검증을 자동으로 수행한다.
- `build_fixture_bundle()`로 새 fixture를 생성해 seed-only 정답과 대조한다.

### 3단계: 사람 검토 (Human Review)

- gold 작성자와 다른 팀원이 질문·gold IR·기대 결과를 검토한다.
- `docs/human-validation-checkpoints.md`의 L1 기준을 따른다.
- case별 승인·수정·보류를 기록한다.
- `HUMAN_APPROVED`로 전환할 때 `reviewer_id`를 명시한다.

### 4단계: 동결 (Freeze)

- 검토 완료 후 benchmark version을 확정한다.
- gold set fingerprint, seed fingerprint, ontology fingerprint, QueryIR contract fingerprint를 기록한다.
- 동결 후 gold content를 변경하지 않는다. 변경 시 version을 올린다.

## fingerprint 고정 항목

| 항목 | 고정 시점 | 변경 조건 |
|------|-----------|-----------|
| `benchmark_id` | 작성 시작 | 새 세트마다 고유 ID |
| `benchmark_version` | 동결 | gold content 변경 시 +1 |
| `seed_fingerprint` | 작성 시작 | FixtureSeed 변경 시 갱신 |
| `ontology_fingerprint` | 작성 시작 | ontology 변경 시 갱신 |
| `query_ir_fingerprint` | 작성 시작 | QueryIR contract 변경 시 갱신 |
| `gold_set_fingerprint` | 동결 | case 추가/제거/수정 시 갱신 |

## 모델·prompt version 기록

각 평가 실행 시 다음을 기록한다:

- `model_run_id`: 고유 실행 ID
- `model_label`: 모델 설명 (예: `fine-tuned-qwen-latest`, `base-instruct-qwen-latest`)
- `model_tag` + `model_digest`: Ollama `/api/tags`에서 확인한 정확한 tag와 digest
- `prompt_version`: `query-ir-v1` 또는 `query-ir-v2`
- `prompt_template_sha256`: versioned template의 UTF-8 SHA-256
- `generation_config_sha256`: `{"options":{"temperature":0},"stream":false,"timeout_seconds":<float>}`의 canonical JSON SHA-256

## 개발 세트 vs 독립 세트 결과 구분

- 개발 세트 결과는 `phase5-13`/`phase5-14` 보고서로 고정한다.
- 독립 세트 결과는 별도 문서에 기록한다.
- 두 세트의 수치를 섞어 보고하지 않는다.
- 논문에서 두 세트를 모두 보고할 때는 개발 세트를 "development holdout", 독립 세트를 "independent holdout"으로 구분한다.

## 현재 한계와 다음 단계

- 30건 초안은 자동 검증까지만 완료했으며 사람 검토·동결 전이다. “동결”, “사람 승인”, “독립 성능 검증 완료”, “학교 간 일반화 검증”으로 표현하지 않는다.
- 새 세트의 실제 작성은 완료했고, 검토·동결은 L1·E2 관문에서 수행한다.
- gold 40건과 새 30건 모두 `NEEDS_HUMAN_REVIEW`를 유지한다.
- E3/L5·L1/L3 팀 결정은 필요한 실제 사용·공유·성능 확정 관문까지 대기한다.
