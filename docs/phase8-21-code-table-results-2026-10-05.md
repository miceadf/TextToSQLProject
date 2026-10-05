# Phase 8-21 결과 (2): 공통코드 table 형식 A (2026-10-05)

지침: [phase8-21-postgres-format-variety.md](prompts/phase8-21-postgres-format-variety.md). 형식 D 결과는 [phase8-21-wide-table-results-2026-10-02.md](phase8-21-wide-table-results-2026-10-02.md).

## 1. 만든 DB

- 합성 DB의 `code_school` schema. 8-17 `other_school`과 내용은 같고 모양만 바꿨다.
  - 이수구분·요일·학기 이름은 공통코드 table `gongtong_code(code_group, code, code_nm)` 하나에 모았다. 그룹은 `ISU`·`YOIL`·`HAKGI`다.
  - 과목·강좌·시간표 table에는 코드(`isu_cd='21'`, `yoil_cd='1'`, `hakgi_cd='20'`)만 있고, 공통코드 table로 가는 FK가 없다.
- 고정 질문: q01~q12와 정답(`other_school.gold`)을 그대로 썼다. 첫 실행 전에 두 질문을 더 고정했다.
  - a01 "소웨 전공 과목 보여줘": 소프트웨어학부이고 이수구분이 11 또는 12.
  - a02 "교양 과목 보여줘": 이수구분이 21 또는 22.
- 다시 만드는 SQL(합성 DB에서만 실행):

```sql
CREATE SCHEMA code_school;
CREATE TABLE code_school.gongtong_code (code_group varchar(10), code varchar(4), code_nm varchar(20) NOT NULL,
  PRIMARY KEY (code_group, code));
INSERT INTO code_school.gongtong_code SELECT 'ISU', isu_cd, isu_nm FROM other_school.isu_gubun;
INSERT INTO code_school.gongtong_code VALUES ('YOIL','1','월요일'),('YOIL','2','화요일'),('YOIL','3','수요일'),
  ('YOIL','4','목요일'),('YOIL','5','금요일'),('HAKGI','10','1학기'),('HAKGI','20','2학기');
CREATE TABLE code_school.hakgwa AS SELECT hg_cd, hg_nm, dh_nm FROM other_school.hakgwa;
ALTER TABLE code_school.hakgwa ADD PRIMARY KEY (hg_cd);
CREATE TABLE code_school.gyosu AS SELECT gs_no, gs_nm, hg_cd FROM other_school.gyosu;
ALTER TABLE code_school.gyosu ADD PRIMARY KEY (gs_no), ADD FOREIGN KEY (hg_cd) REFERENCES code_school.hakgwa (hg_cd);
CREATE TABLE code_school.gyogwamok AS SELECT gm_cd, gm_nm, hg_cd, isu_cd::varchar(2) AS isu_cd, hakjeom,
  suggang_hangnyeon FROM other_school.gyogwamok;
ALTER TABLE code_school.gyogwamok ADD PRIMARY KEY (gm_cd), ADD FOREIGN KEY (hg_cd) REFERENCES code_school.hakgwa (hg_cd);
CREATE TABLE code_school.gangjwa AS SELECT ks_id, gm_cd, gs_no, nyeondo, hakgi_cd::varchar(2) AS hakgi_cd, ban_no,
  jeongwon, sugang_inwon FROM other_school.gangjwa;
ALTER TABLE code_school.gangjwa ADD PRIMARY KEY (ks_id), ADD FOREIGN KEY (gm_cd) REFERENCES code_school.gyogwamok (gm_cd),
  ADD FOREIGN KEY (gs_no) REFERENCES code_school.gyosu (gs_no);
CREATE TABLE code_school.siganpyo AS SELECT sp_id, ks_id,
  (CASE yoil WHEN 'MON' THEN '1' WHEN 'TUE' THEN '2' WHEN 'WED' THEN '3' WHEN 'THU' THEN '4' ELSE '5' END)::varchar(1) AS yoil_cd,
  si_st, si_ed FROM other_school.siganpyo;
ALTER TABLE code_school.siganpyo ADD PRIMARY KEY (sp_id), ADD FOREIGN KEY (ks_id) REFERENCES code_school.gangjwa (ks_id);
```

## 2. 첫 실행 (코드 수정 전)

화면 API로 등록·검토·게시했다. 관리자는 코드 column을 연결하고, 공통코드의 뜻을 값 사전에 직접 입력했다. 입력한 것은 이수구분 5개, 요일 `1`~`5`, 학기 `10`·`20`, 학과 약어다.

- q01~q12 **12/12**. 정확한 이름("전공필수", "월요일", "2학기")은 값 사전으로 코드에 연결되었다. 결과 카드에도 코드 대신 이름이 보인다(8-06부터 있던 기능).
- a01·a02 실패:
  - a02 "교양 과목" → 이수 구분에 "교양" 포함 → 코드 column에서 `LIKE '%교양%'` → 0건. 0건 진단(8-20)이 그 조건을 짚었다.
  - a01 "소웨 전공 과목" → 모델이 "전공"을 아예 빼서 소프트웨어학부 과목 전체가 나왔다. 값 힌트가 "전공"을 몰랐다(값 사전은 이름 전체만 안다).
- AI mapping 첫 후보는 **16개 중 9개**만 맞았다. 코드 column(이수구분·학기)과 코드형 key(과목 코드·학과 코드)는 후보가 없었다. 학과명은 단과대학명을, 대상 학년은 학과 코드를 골랐다. 관리자 검토로 바로잡았다.

## 3. 고친 것

- **이름 일부로 코드 찾기:** 값 사전이 있는 항목의 "포함" 조건은, 그 말이 이름(또는 별칭)에 들어 있는 저장 값도 함께 찾는다.
  - SQL은 `(column LIKE '%교양%' OR column IN ('21', '22'))`이다.
  - 이름이 저장 값과 다른 항목(코드)만 대상이다. 별칭은 일부로 찾지 않는다.
  - compiler와 SQL policy가 같은 함수로 이 모양을 정한다. policy는 그 조건에만, 같은 column과 정확한 개수의 placeholder로만 이 모양을 허용한다.
  - 이름 전체("교양필수")는 지금처럼 코드 하나에 정확히 연결된다.
  - 이름을 저장한 DB(레거시)는 이름과 저장 값이 같아 이 목록이 생기지 않는다. SQL이 이전과 똑같다.
- **공유 앞부분 힌트:** 한 항목의 한 단어 코드 이름 2개 이상이 같은 앞부분으로 시작하면, 그 앞부분을 값 힌트로 준다. 예: "전공" → 전공필수·전공선택.

## 4. 고친 뒤 결과

- `code_school` 고정 질문 **14/14**. a01은 7개, a02는 9개로 정답과 같다.
- 레거시 개발자 profile: 컴융 전공 82건, 컴융 3학년 전공 33건으로 이전과 같다. SQL에 `IN` 목록이 없다.

## 5. 리뷰

`gpt-6-luna` high review-only 지적 1건을 재현하고 고쳤다.

- **이름을 저장한 DB에서 결과가 달라졌다.** 레거시 profile에서 "전공핵"으로 찾으면, 별칭 `전공핵심`을 통해 `전공(핵심)`이 `IN` 목록에 들어갔다. 이전에는 `LIKE '%전공핵%'`가 아무것도 찾지 않았다.
- 고친 내용: 이름이 저장 값과 다른 항목(코드)만 대상으로 하고, 별칭은 일부로 찾지 않는다. 레거시는 이름과 저장 값이 같아 목록이 생기지 않는다.
- 앞에서 레거시 기대값을 바꿨던 기존 테스트 2개를 원래대로 되돌렸고, 그대로 통과했다. 회귀 테스트를 추가했다.
- 고친 뒤 `code_school` 14/14와 레거시 82·33건을 다시 확인했다.

## 6. 남은 것

- 공통코드 table에서 값 사전을 자동으로 가져오는 기능은 만들지 않았다. 관리자가 코드마다 입력한다. 코드 수가 많은 학교에서는 불편할 수 있다(후속 후보).
- 코드형 column에 대한 AI mapping 제안이 약하다(9/16). 관리자 검토가 필수다.
- 형식 B(교시)·C(묶인 시간표)가 남았다.
