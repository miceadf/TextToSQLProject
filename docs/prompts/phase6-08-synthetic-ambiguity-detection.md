# Phase 6-08: 합성 모호성 감지와 clarification 연결

기존 `clarification-gate-v2` 앞에 versioned 합성 ambiguity rule set 기반의 결정적 감지 경계를 추가한다. **질문 감지 → 기존 `prepare()` pending 생성 → 같은 규칙으로 재감지 → 기존 `resolve()` 확인/취소**를 한 기능 흐름으로 닫는다. 자유형 모델 추론, QueryIR 생성·수정, harness 실행, 실제 UI·사용자 세션은 구현하지 않는다.

## 먼저 확인

- 시작 시 `git status --short`와 최근 commit을 확인하고 기존 변경을 보존한다.
- `AGENTS.md`, `docs/next-session-handoff.md`, 계획 문서의 Phase 6·다음 시작점·ADR-031/034/035, `docs/human-validation-checkpoints.md`의 E3/L1/L3/L5를 따른다.
- `backend/adaptive/harness/clarification.py`, runtime ontology/mapping 계약, 해당 합성 테스트만 읽는다. legacy FastAPI/React·`backend/llm.py`는 변경하지 않는다.

## 구현 계약

1. 별도 작은 모듈에 `ambiguity-detector-v1` 계약을 둔다. caller가 제공하는 frozen rule set은 고정 version, 고유 rule ID, 1개 이상 literal trigger phrase, 서로 다른 semantic option 2~16개를 가진다. option은 opaque option key와 ontology entity/field `semantic_ref`만 허용한다. display 문장, 물리 table/column, SQL, 결과 행, 자유 답변 필드는 두지 않는다.
2. 모든 공개 입력은 Pydantic instance를 그대로 신뢰하지 않고 warning 비노출 plain JSON dump 후 재검증한다. rule ID/option key/trigger는 길이·문자·중복을 제한한다. Unicode NFKC + casefold + whitespace 정규화를 한 코드 출처로 사용한다. empty/control-only trigger, 정규화 후 중복 rule/trigger/option, 미지 semantic ref, unpublished mapping, request/mapping database 불일치는 고정 오류로 차단한다.
3. rule match는 정규화한 질문에 정규화 literal trigger 중 하나가 포함될 때만 성립한다. trigger는 2자 이상으로 제한해 한 글자 오탐을 줄인다. 이 규칙 기반 match는 자연어 의미 이해나 완전한 모호성 탐지가 아님을 문서화한다.
4. match 0개면 signal/pending 없이 통과한다. 정확히 1개면 해당 rule의 option으로 기존 `AmbiguitySignal`을 결정적으로 만든다. option ID는 raw rule ID/option key를 노출하지 않는 domain-separated SHA-256 기반 64자리 lowercase hex로 만들고 동일 입력·순서 변화에서 안정적이어야 한다. match 2개 이상은 서로 다른 ambiguity 축을 한 선택지 집합으로 합치지 않고 고정 conflict로 fail closed한다.
5. `prepare_detected_clarification()`은 감지 결과가 없으면 `None`, 하나면 기존 `clarification.prepare()`를 호출해 `PendingClarification`을 반환한다. caller가 별도 `AmbiguitySignal`을 주입할 수 없어야 한다.
6. `resolve_detected_clarification()`은 동일 request/ontology/mapping/rule set으로 다시 감지해 trusted signal을 재구성한 뒤 기존 `clarification.resolve()`를 호출한다. no-match, multi-match, rule/option/trigger/context drift는 확인으로 승격하지 않는다. 기존 pending option 전체 일치·질문/context fingerprint·정확히 한 선택·별도 취소 계약을 유지한다.
7. detector 결과·pending·result·고정 오류·stdout/log에 raw 질문, raw request/database/rule/option key, trigger phrase, 물리 mapping, SQL, 행, path, secret을 새로 저장하거나 반영하지 않는다. runtime rule set 자체는 caller-owned 입력이며 저장하지 않는다. low-entropy phrase/hash 추측 방지나 rule 작성자 인증을 주장하지 않는다.
8. 기존 `clarification-gate-v2`, `AmbiguitySignal`, `PendingClarification`, `ClarificationResult`, fingerprint 규칙과 오류 taxonomy를 깨지 않는다. detector 오류는 고정 code만 노출하고 기존 clarification 오류는 원래 고정 code로 유지한다.
9. 모델·repair provider·executor·파일·DB·network·Ollama·PostgreSQL·CNU 자료·private bundle을 호출하지 않는다. 기존 실패 code를 모호성으로 자동 승격하지 않는다. QueryIR 수정·재실행·UI 표시·single-use/expiry/session 저장은 후속 단계다.

## 합성 테스트

- match 0개: pending `None`, 차단·외부 호출 없음.
- match 1개: 결정적 signal 생성, 기존 pending 생성, 정확한 한 선택 `CONFIRMED`, 명시적 취소 `CANCELLED`.
- rule/option 입력 순서가 달라도 같은 option ID·정렬 pending·결과. 다른 rule/option 의미는 다른 ID.
- match 2개 이상: conflict, pending 없음. 서로 다른 ambiguity 축을 합치지 않음.
- trigger NFKC/case/whitespace 정규화, literal 포함 정책, 한 글자·blank/control trigger 거부.
- 중복 rule ID, 정규화 중복 trigger, option key/semantic ref 중복, 미지 entity/field, unpublished mapping, database mismatch 거부.
- prepare 뒤 질문·ontology·mapping·rule trigger/option 변경, match 소멸·추가 시 confirmation 거부. 기존 pending을 새 규칙으로 성공시키지 않음.
- forged `model_copy(update=...)`·`model_construct` request/ontology/mapping/rules/pending/choice 거부.
- raw path/secret형 request/database/rule/option/trigger가 pending/result/error/stdout/log에 없음. 단, caller-owned rule set·request 자체는 입력이므로 검사 출력에 직렬화하지 않는다.
- 기존 clarification 35개와 detector 신규 테스트 회귀. 관련 테스트만 실행한다. 전체 pytest/compileall/diff-check는 Codex 독립 검토 후 각각 한 번 실행한다.

## 완료 기준·보고

- 긴 diff·전체 로그 금지. 변경 파일, 감지 규칙, 호출 0회, 테스트 수·결과, 실패 원인, 남은 위험만 한국어로 요약한다.
- 계획·ADR·체크리스트·handoff·commit은 Codex 독립 검토 뒤 처리하므로 수정하지 않는다.
- 전체 `모호한 질문 감지 및 사용자 확인` 체크박스는 실제 UI/상호작용과 선택의 QueryIR 반영이 없으므로 완료 처리하지 않는다. Phase 6-08 하위 항목만 완료로 추가한다.
