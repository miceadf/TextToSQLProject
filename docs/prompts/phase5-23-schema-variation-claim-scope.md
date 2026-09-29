# Phase 5-23 구현 지침: 주장 범위 축소와 schema 변형 fixture 확장

## 배경

Phase 5-22 대칭 추출 재실행에서 IR arm은 두 모델 모두 세 fixture에서 같은 정답 수를 냈고(fine-tune 31·31·31, base 26·26·26), 직접 SQL arm은 정규화 schema에서 떨어졌다(fine-tune 30→11, base 34·32→24). base 모델 전체 정답은 직접 SQL 90이 IR 78보다 높다. 따라서 "QueryIR이 직접 SQL보다 정확하다"는 일반 주장은 근거가 없고, 방어 가능한 주장은 **schema 구조 변화에 대한 정확도 일관성**이다. 사용자는 이 방향으로 평가 설계를 정리하고 schema 변형 fixture를 늘리는 것을 승인했다.

현재 fixture 3종은 이름 축(영문 snake vs 약어)과 구조 축(단일 table vs 5-table 정규화)이 섞여 있다: flat(영문·단일), normalized(영문·정규화), legacy(약어·단일). 약어·정규화 조합과 중간 구조가 없어 "정규화 schema에서 직접 SQL이 떨어진다"가 이름 때문인지 JOIN 때문인지 분리할 수 없다.

## 범위

### 1. 문서: 주장 범위와 평가 설계

- 계획 문서 §15.3에 구현된 변형과 지원하지 않는 변형(코드값 변경, FK 없는 관계, view, 컬럼 타입 변경)을 구분해 적는다. 지원하지 않는 변형은 ontology value mapping·FK 추론·L2 검토가 필요하므로 이번 범위에서 제외한다.
- §16.2에 연구 질문 "QueryIR 방식은 같은 질문의 정확도를 물리 schema 구조 변화에 대해 일정하게 유지하는가"를 추가하고, §16.4에 "QueryIR이 직접 SQL보다 정확하다"는 일반 우열 주장을 하지 않는다는 제한을 적는다.
- 일관성 지표를 정의한다: arm별 variant 정답 수의 최대−최소(spread), 모든 variant에서 정답인 case 수(all-variant correct).
- ADR-061(주장 범위)과 ADR-062(확장 fixture suite 계약)를 추가한다.

### 2. 새 fixture 변형 2종 (같은 seed, 같은 논리 행)

- `coded`: normalized와 같은 5-table 구조·FK에 약어 이름을 쓴다. `tb_dept(dept_cd, dept_nm)`, `tb_prof(prof_no, prof_nm)`, `tb_sbjt(sbjt_cd, sbjt_nm, pnt, isu_gb, dept_cd→tb_dept)`, `tb_lect(lect_no, sbjt_cd→tb_sbjt, prof_no→tb_prof, yyyy, hakgi, bunban, limit_cnt, aply_cnt)`, `tb_lect_tm(tm_no, lect_no→tb_lect UNIQUE, yoil, st_tm, ed_tm)`. legacy와 이름 계열이 같고 normalized와 구조가 같아 이름 축과 구조 축을 분리한다.
- `hybrid`: 2-table 부분 비정규화. `subjects(subject_code, subject_title, credit_hours, course_type)`, `class_sections(section_id, subject_code→subjects, dept_code, dept_name, professor, term_year, term, section_no, seat_limit, seats_taken, weekday, time_from, time_to)`. 학과가 과목이 아니라 분반 행에 있어 §15.3의 "컬럼의 다른 테이블 이동"·"테이블 병합"을 1개 JOIN 수준으로 재현한다. 다른 영문 동의어 이름을 써 기존 fixture와 이름이 겹치지 않는다.
- 두 변형의 Published Mapping은 `fixture_mapping.py`에 명시적으로 추가하고 catalog 대조를 그대로 거친다. 자동 proposal은 기존처럼 만들기만 하고 publish하지 않는다.

### 3. suite 계약 (기본 동작 불변)

- `FixtureVariant`에 `CODED`, `HYBRID`를 추가한다. suite는 `core-v1`(flat·normalized·legacy)과 `extended-v1`(core + coded·hybrid) 두 개로 고정한다.
- `build_fixture_bundle(..., variants=CORE_VARIANTS)`: 기본값은 core 3종이며 생성 파일·manifest가 이전과 같다. 허용 값은 두 suite의 variant 집합뿐이다.
- runner·직접 SQL runner는 bundle에 들어 있는 variant를 enum 순서로 준비하고, bundle variant 집합이 두 suite 중 하나와 정확히 같을 때만 진행한다. `config.variants`가 bundle에 없는 variant를 요구하면 실패한다.
- `run_contracts.FixtureVariant` literal에 두 값을 추가하고 `variants` 최대 길이를 5로 늘린다. 기존 report는 그대로 유효하므로 schema version은 바꾸지 않는다.
- case 단위 cross-schema consistency는 attempt 수가 설정 variant 수와 같고 3 이상일 때만 계산한다(3 variant 설정의 기존 결과와 같다).
- 런타임(`api`·`harness`·registry)은 bundle을 쓰지 않으므로 prototype registry에 새 변형이 나타나지 않는다. `capture_targets`는 core 3종 고정을 유지한다.

### 4. 평가 CLI

- `arm_live_cli --fixture-suite {core-v1,extended-v1}`(기본 `core-v1`, 반복·빈 값·unknown 거부).
- `extended-v1`은 label에 `.ext`를 붙이고 run ID prefix를 `p523-ext`로 시작한다(no-system이면 `-nosys`, fence면 `-fence`를 이어 붙인다). core 설정의 label·run ID는 5-19~5-22와 같다.
- stdout 요약에 `fixture_suite`와 arm별 일관성 지표(`spread`, `all_variants_correct`)를 추가한다. 일관성 지표는 report에서 계산하는 평가 전용 순수 함수로 둔다.

### 5. 실행

- 두 모델을 `extended-v1`·`single-fence-v1`로 각 1회 실행한다(fine-tune `alpaca-raw-no-system-v1`, base `model-template`; 모델당 400 호출). 결과가 나빠도 prompt·규칙·fixture를 조정하지 않는다.

## 테스트

- 새 builder: seed canonical 행과 같은 내용(runner preflight), FK·UNIQUE, catalog table 수.
- 기본 bundle은 core 3종만 만들고 extended는 5종; 허용되지 않는 variant 조합 거부.
- fixture mapping: 두 변형의 catalog 대조 통과, 잘못된 database ID 거부.
- oracle smoke: IR runner와 직접 SQL runner 모두 extended 200/200, IR cross-schema consistent 40/40.
- config: 5 variant 허용, 중복·6개 거부; bundle에 없는 variant 요구 시 실패.
- consistency: 5 attempt 계산, 설정보다 적은 attempt는 `INSUFFICIENT_VARIANTS`.
- CLI: extended label·run ID·요약 필드, oracle transport에서 IR·SQL 200, 잘못된 suite·반복은 설정 오류, core 기본값 불변.
- 일관성 지표 함수: spread·all-variant correct 계산, 불일치 report 거부.

## 기록

결과 문서 `docs/phase5-23-schema-variation-results-2026-09-29.md`: 두 모델 × 두 arm × 5 variant 정답, spread, all-variant correct, paired count, 5-22와 core 3종 비교, 한계(합성 fixture 5종 모두 개발자 작성, 같은 seed, 개발 세트 40건은 prompt 탐색에 사용, 1회 실행). 수치는 L1/L3 전 잠정치다.
