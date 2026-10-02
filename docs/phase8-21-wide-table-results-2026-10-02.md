# Phase 8-21 결과 (1): 넓은 단일 table 형식 D (2026-10-02)

지침: [phase8-21-postgres-format-variety.md](prompts/phase8-21-postgres-format-variety.md). 이번 사용량 주기에는 형식 D만 확인했다. A(공통코드)·B(교시)·C(묶인 시간표)는 다음 주기에 한다.

## 1. 만든 DB

- 합성 DB `schema_adaptive_synthetic`의 `wide_school` schema. 8-17 `other_school`의 내용을 join해 table 하나(`sugang_pyeollam`)로 만들었다. 시간표 칸마다 한 행이다(143행, 과목 80개).
- column 이름은 `other_school`과 다르게 지었다(`gwamok_bunho`, `hakgwa_myeong`, `damdang_gyosu`, `sijak` 등). 이수구분·학과명·교수명은 코드가 아니라 이름 그대로 들어 있다.
- 데이터가 같으므로 고정 질문 q01~q12와 정답(`other_school.gold`)을 그대로 썼다. q13·q14(강의실)는 강의실 column이 없어 뺐다.
- 다시 만드는 SQL(합성 DB에서만 실행):

```sql
CREATE SCHEMA wide_school;
CREATE TABLE wide_school.sugang_pyeollam AS
SELECT s.sp_id AS haeng_no, g.gm_cd AS gwamok_bunho, m.gm_nm AS gwamok_myeong, m.hakjeom,
       i.isu_nm AS isu_gubun, m.suggang_hangnyeon AS daesang_hangnyeon,
       h.hg_cd AS hakgwa_cd, h.hg_nm AS hakgwa_myeong, p.gs_nm AS damdang_gyosu,
       g.nyeondo, g.hakgi_cd AS hakgi, g.ban_no AS bunban, g.jeongwon, g.sugang_inwon,
       s.yoil, s.si_st AS sijak, s.si_ed AS jongryo
FROM other_school.siganpyo s
JOIN other_school.gangjwa g ON g.ks_id = s.ks_id
JOIN other_school.gyogwamok m ON m.gm_cd = g.gm_cd
JOIN other_school.isu_gubun i ON i.isu_cd = m.isu_cd
JOIN other_school.hakgwa h ON h.hg_cd = m.hg_cd
JOIN other_school.gyosu p ON p.gs_no = g.gs_no;
ALTER TABLE wide_school.sugang_pyeollam ADD PRIMARY KEY (haeng_no);
-- 지우기: DROP TABLE wide_school.sugang_pyeollam; DROP SCHEMA wide_school;
```

## 2. 확인 방법

화면이 쓰는 API로 테스트 서버(8124)에 등록 → 검토 → 값 사전 → 게시했다. `other_school` 때와 같은 관리자 작업이다.
- 값 사전: 학과 약어 `소웨`·`전전`·`기공`, 학기 `10`·`20`, 요일 `MON`~`FRI`.
- 시간 `0900` 형식의 변환(`HHMM_TEXT_TO_HHMM`)은 서버 규칙이 정했다.

## 3. 결과

- **코드 수정 없이 동작했다.** 등록·검토·게시가 막힘 없이 끝났다(mapping v1).
- AI mapping 첫 후보는 16개 중 14개가 맞았다. 교원명·종료 시간은 후보가 없어 관리자가 지정했다.
- 고정 질문 **11/12**.
  - 틀린 것은 q11 "금요일 오전 수업 보여줘" 하나다. 모델이 시간 조건을 빼서 14개가 나왔다(정답 7개).
  - 같은 질문을 두 번 더 보내자 시간 조건을 뺀 답(14개)과 맞는 답(7개)이 한 번씩 나왔다. "금요일 12시 이전에 시작하는 수업"은 7개로 맞았다.
  - 8-20 가상 학교에서 본 것과 같은 모델 흔들림이다. 넓은 table의 문제가 아니다.
- 넓은 table에서도 시간 변환과 값 사전이 그대로 적용되었다.

## 4. 남은 것

- 형식 A·B·C는 지침 4절 순서로 다음 주기에 한다.
- `wide_school` schema는 합성 DB에 남겨 두었다. 테스트 등록(`wide-school`)은 지웠다.
- 코드를 바꾸지 않았으므로 테스트, Luna 리뷰, 전체 pytest는 하지 않았다.
