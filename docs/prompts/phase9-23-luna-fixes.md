# Phase 9-23 지침: Luna 리뷰(9-11~9-22) 확인 지적 고침 + 남은 회귀 테스트

Luna(`gpt-6-luna` high) 리뷰 지적 4건을 Claude가 확인했다. 각 고침에 회귀 테스트를 둔다. 다른 동작은 바꾸지 않는다.

## 1. 삭제 뒤 값 힌트 cache가 남음 (High, 코드로 확인)

- `api/postgres_registration.py` `delete()`(약 242~248행)는 live context와 reader 설정만 지우고 `api/value_hints.py` `ChatValueIndexes` cache(key = database_id, schema fingerprint, mapping version)는 지우지 않는다. 같은 id·같은 구조로 다시 등록해 v1을 게시하면 이전 등록의 값·별칭이 힌트로 다시 쓰인다.
- 고침: 등록 삭제 때 그 database_id의 cache 항목을 모두 지운다(`ChatValueIndexes`에 `forget(database_id)` 같은 메서드, 삭제 경로에서 호출). 서버가 cache 객체에 접근하는 기존 방식을 따른다.
- 테스트: cache에 올라간 등록 삭제 → 같은 id 재등록·같은 key → 새로 build됨(이전 값 미사용).

## 2. `Path.is_junction()` 없는 Python에서 삭제 실패 (Low, 확인)

- `registration/postgres_registrations.py` `delete_registration`(약 358~372행)이 `Path.is_junction()`을 부른다(Python 3.12+). `env_check.py`는 Python 3.10 이상을 지원한다. 3.10/3.11에서는 `AttributeError` → `STORAGE_ERROR`.
- 고침: 이웃 코드의 호환 처리처럼 `getattr(path, "is_junction", None)`이 없으면 Windows에서는 `os.path.isjunction`(3.12+) 또는 reparse point 속성(`os.lstat(...).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT`)으로 판정하는 helper를 쓴다. 판정할 수 없으면 안전하게 거절하지 말고, symlink 검사는 그대로 유지한다.
- 테스트: `is_junction`이 없는 Path를 흉내 낸 경우에도 일반 삭제가 성공하고, reparse point로 표시된 경로는 거절.

## 3. 사용 중단 필드에 대한 검토 변경 요청이 받아들여짐 (Low, 코드로 확인)

- `mapping/review.py` `approve`(약 45~71행)와 remap/ignore가 ontology 현재 버전의 사용 중단 필드(`CourseOffering.year`)를 거르지 않는다. v2 proposal에 year가 있으면 화면에 안 보이는데도 API로 APPROVE가 저장된다.
- 고침: 검토 변경(APPROVE/REMAP/IGNORE)과 일괄 경로에서 `ontology.retired_fields`에 든 필드는 기존 "알 수 없는 필드" 오류와 같은 고정 코드로 거절한다. 읽기(v2 초안 로드)는 그대로 된다.
- 테스트: v2 초안에서 year APPROVE/REMAP/IGNORE → 거절, 다른 필드는 정상.

## 4. 요청 동사의 잘못된 알림 (Low, 재현함)

- `api/unmatched_terms.py`: "교양 과목 정리해줘" + 조건 `Course.category ∋ 교양` → `['정리해줘']`. "보여줘/알려주세요/찾아주세요"는 알림 없음.
- 고침: 특정 낱말 추가가 아니라 **요청 어미 일반 규칙**으로: 낱말이 `해줘`, `해 줘`, `줘`, `주세요`, `해주세요`, `해봐`, `할래`, `해` 등 요청·명령 어미로 끝나면 그 어미를 뗀 뒤 남는 어간이 일반어이거나 비면 알리지 않는다. 어간 자체(예: "정리")는 일반 동사 목록(정리, 추천, 검색, 확인, 비교, 나열, 소개 등 짧은 고정 목록)에 있으면 알리지 않는다. "컴융 전공 보여줘"의 "컴융" 알림은 그대로 나와야 한다.
- 테스트: 위 4개 질문 + "컴융 전공 정리해줘" → `['컴융']`; 9-21의 30문항 고정 판정 표가 그대로인지.

## 5. 남은 회귀 테스트: `direct_sql_pg --reference-database`

- 9-10 측정 중 Claude가 `backend/adaptive/evaluation/direct_sql_pg.py`에 `--reference-database`를 더했다(QueryIR arm이 profile `legacy-view`를 쓸 때 기준 SQL을 다른 등록 연결로 실행). 테스트가 없다.
- 테스트: 가짜 `load_context`로 `--arm queryir --set legacy --database legacy-view --reference-database X`가 기준 계산에 X를 쓰는지, 옵션이 없으면 `--database`를 쓰는지. 기존 동작은 바꾸지 않는다.

## 확인과 지킬 것

- 관련 테스트 파일만 실행한다(전체 pytest는 Claude가 커밋 전에 한 번).
- 사용자 서버·데이터 안 건드림, live 모델 호출 없음, 커밋 없음, `backend/.env` 값 출력 없음. UTF-8로 쓴다.
- 문서: `docs/schema-adaptive-text-to-sql-plan.md` 변경 이력 2026-10-13 "9-23 Luna 지적 4건 고침 + reference-database 테스트" 한 줄, 로드맵에서 9-11~9-22의 Luna 리뷰 대기 표시가 있으면 완료로.
- 끝나면 바꾼 파일과 테스트 결과를 짧게 적는다.
