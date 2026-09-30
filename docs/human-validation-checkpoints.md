# 사람·사용자 검증 체크포인트

> 작성: 2026-09-14 · 상태: 팀 공유용 초안 · 기준: `schema-adaptive-text-to-sql-plan.md`

## 목적과 현재 경계

자동 테스트는 코드가 **정의된 계약대로** 작동하는지 검사한다. 질문의 실제 뜻, 대학별 컬럼·코드값의 업무 의미, 개인정보 사용 권한, 사용자 신뢰도, 논문 주장까지 승인하지는 못한다. AI가 구현과 테스트를 함께 작성하면 같은 가정 오류가 양쪽에 들어갈 수도 있다. 사람 검토는 개발 중 모든 단계의 필수 중단점이 아니라, 해당 결과를 사용·공유·주장하기 전의 승인 관문이다.

2026-09-28 상태: SQLite 합성 fixture 3개는 같은 6개 개설강좌의 구조 변형이다. 공통 질문 40개는 전부 `NEEDS_HUMAN_REVIEW`다. `FakeModel` oracle smoke는 실제 모델 성능이 아니다. 합성 clarification 화면과 session 범위 DB 변경(flat·legacy-schema의 동일 canonical 목록·질의)은 실제 Chrome browser smoke를 통과해 내부 팀 논의에 쓸 수 있지만 L4 사람 검토는 미수행이다. 파일 DB의 private quarantine 등록, loopback upload, mapping 검토·명시적 publish, strict activation 뒤 registry/catalog/clarification 편입은 합성 자동 테스트로 검증했으나 이 전체 흐름의 실제 browser smoke는 미수행이다. 이 합성 publish는 실제 학교 DB의 의미 검토나 L2 승인이 아니다. PostgreSQL inspector·QueryIR compiler·AST policy·read-only executor, server-owned backend registry/catalog/clarification routing, Git 비추적 private startup profile과 별도 DSN 환경변수의 CLI 주입, frontend strict registry 선택은 자동 검증했다. 확인된 clarification constraint로 기존 Ollama adapter의 semantic context를 제한하는 provider와, loopback CLI option으로만 이를 켜는 server-owned startup 설정(proxy·redirect 차단 transport)도 synthetic transport와 로컬 임시 server로 자동 검증했다. Phase 7-09에서 로컬 Ollama에 완전 합성 질문 4건을 prototype 전체 경로로 보내 호출 예산·응답 shape·redaction 경계를 확인했으나, 모델 출력은 4건 모두 reflection guard에서 거부되었다. 이 실행은 모델 내장 CNU SYSTEM이 주입된 조건이었고, Phase 7-10에서 요청 형식을 명시해 재실행한 결과 2건이 성공 실행 경로를 live로 통과했다(0행, 불필요한 filter 경향 잔존). 이는 모델 성능 검증이 아니며 L1/L3 전 수치로 쓰지 않는다. 직접 SQL 비교군과 QueryIR의 paired 비교 계약은 oracle 자기 일관성(120/120)까지 자동 검증했으며, Phase 5-19 비교는 모델 내장 CNU SYSTEM이 주입된 조건이라 정정했고, 이를 제거한 Phase 5-20 잠정 비교(IR 정답 93/120, 직접 SQL 72/120)도 gold 40건 L1 검토와 L3 결과 검토 전에는 잠정치이며, 이 수치를 논문에 쓰기 전 L1·L3가 필요하다. Phase 5-21 base instruct 실행(SQL 90/120, IR 0/120)은 arm 간 code fence 추출 규칙 비대칭 때문에 방식 비교로 쓰지 않고, 대칭 규칙을 적용한 Phase 5-22 재실행(fine-tune IR 93·SQL 72, base IR 78·SQL 90)을 기준으로 하되 이 역시 L1/L3 전 잠정치다. Phase 5-23에서 모델 비교 주장을 schema 변화에 대한 정확도 일관성으로 한정했고, 같은 seed의 합성 변형 2종(coded·hybrid)을 더한 5종에서 IR arm은 두 모델 모두 variant별 정답 수가 같고 직접 SQL arm은 JOIN이 필요한 schema에서 떨어졌다. 5종 모두 개발자가 만든 합성 schema이고 fixture mapping도 개발자 작성이므로 L2(mapping 검토)·E4/L6(독립 unseen schema)·L1/L3를 대신하지 않는다. Phase 8-01에서 합성 private SQLite의 mapping 버전 이력·이전 버전 재활성화를 자동 검증했다. 재활성화도 실행 권한 변경이므로 실제 학교 DB에서는 publish와 같이 L2 뒤에만 쓰며, 관리자 화면의 사용성은 L4 대상이다. Phase 8-02의 등록 간 schema 비교는 구조 metadata만 대조하는 읽기 전용 기능이며, 이름이 같아도 의미가 바뀐 column이나 코드값 변화는 잡지 못하므로 실제 DB의 승계 판단은 L2 검토를 대신하지 않는다. Phase 8-03의 승계는 구조가 같은 field를 사유와 함께 REMAP할 뿐이므로, 실제 학교 DB에서는 승계 field도 L2에서 의미를 다시 확인해야 한다. 승계로 줄어드는 관리자 작업량은 합성 조건의 관찰이며 사용자 평가(L4) 전 결론이 아니다. Phase 8-04의 test query 미리보기는 게시·활성 mapping의 실제 행 값을 관리자 화면에 최대 5행 보여주므로, 실제 학교 DB에서는 연결 전 L2, 실데이터 시연·공유 전 L5를 그대로 적용한다. Phase 8-05의 verified 기준선은 관리자가 결과를 확인했다는 기록일 뿐 결과의 의미가 맞다는 승인(L2)이나 gold 검토(L1)가 아니며, 회귀 `MATCH`는 기준선과 같다는 뜻이지 정답이라는 뜻이 아니다. 실제 PostgreSQL profile을 띄운 browser end-to-end smoke, Ollama 실통합, 실제 학교 DB, 독립 unseen school은 아직 검증되지 않았다. 루트 `pytest`의 legacy `backend/test_correction.py` import 오류는 별도 기준선 결함으로 남아 있다. 각 수치는 해당 시점 기록이지 향후 통과 보증이 아니다.

사용자 확인(2026-09-29): legacy 서비스의 PostgreSQL 강좌 DB는 충남대 수강신청 테이블을 그대로 가져온 것이 아니라 팀이 유사한 스키마와 데이터로 직접 구성한 것이다. 사용자는 legacy와 스키마 적응 prototype을 함께 실행하는 팀 내부 공유(`npm run dev:all`)에서 실제 데이터 노출을 고려하지 않아도 된다고 밝혔다. 이 확인은 legacy 강좌 DB에 대한 사실 기록이며, 기존 CNU 학습 데이터셋(5,385 SQL)·가명 artifact의 E3와 외부 시연 L5 판단을 AI가 대신 승인한 것이 아니다.

Phase 7-12(2026-09-29): legacy 챗봇의 대상 view를 QueryIR 경로에 부분 mapping으로 등록했다. mapping은 12개 field이고, 미지원 3개와 학점·시간 변환을 포함한다. legacy DB에는 읽기 전용으로 연결해 schema inspection, profile 작성, 고정 query smoke를 실행했고, 구조와 집계만 기록했다. 이 mapping은 개발자가 작성했다. legacy 강좌 DB가 팀 구성 데이터라는 사용자 확인에 근거한 개발용 등록이며, 실제 학교 DB의 L2 mapping 검토를 대신하지 않는다. 변환 목록(`SLASH_FIRST_NUMERIC`, `TIME_TO_HHMM`)이 실제 학교 DB의 값 형식에 맞는지는 L2에서 다시 확인한다.

Phase 7-13(2026-09-29): legacy view 대상 QueryIR 챗봇 endpoint를 추가하고, 로컬 Ollama와 legacy DB로 8개 질문을 live 실행했다(상태·행 수만 기록). 이 실행은 동작 확인이며 L1/L3 전의 모델 성능 근거가 아니다. 모델이 문자열 field에 구절 전체를 `equals`로 넣는 경향을 server 규칙으로 완화할지는 7-15 비교 뒤 사용자가 결정한다.

Phase 7-14(2026-09-29): `npm run dev:all`이 legacy backend의 `backend/.env` DB·Ollama 설정을 재사용해 QueryIR 챗봇 경로를 켠다. 화면의 `DB 변경`에서 `Legacy DB (QueryIR)`를 고르면 AI Course Assistant가 QueryIR 경로로 답한다. 팀 내부 비교용 개발 경로이며, 외부 시연(L5)이나 사용성 평가(L4)를 대신하지 않는다.

Phase 7-15(2026-09-29): legacy 챗봇과 QueryIR 경로를 개발자 작성 30문항으로 비교했다. 기준 SQL과 한국어 의미 해석(예: `오전`=12시 이전)은 개발자 판단이며 gold가 아니다. 결과 문서의 수치를 팀 공유 외 용도(논문·성능 주장)로 쓰기 전에 L1 문항·기준 검토와 L3 결과 검토가 필요하다. 후속 서버 정책(limit, 문자열 `contains` 완화, 코드값 정규화, field 별칭, 학년 등 ontology 확장)은 사용자·팀 결정 대기다.

Phase 7-16(2026-09-29): 사용자 결정으로 챗봇 서버 정책 3종(limit 200, 이름형 문자열 `contains`, 요일 정규화)을 적용하고 같은 30문항으로 재비교했다. ONTOLOGY 범위 수용 가능 문항은 QueryIR 14, legacy 17이다. 이 수치도 개발자 작성 문항·기준의 잠정치이며 L1·L3 전이다.

Phase 7-17(2026-09-29): legacy DB 기본 table에 기준 table 보존 LEFT JOIN으로 매핑한 `legacy-base`를 추가했다. view 경로와 전체 관계·30문항 답이 같음을 개발자 검증으로 확인했다. 이 mapping도 팀이 구성한 legacy DB에 대한 개발자 작성이며 L2 검토가 아니다.

Phase 8-06(2026-09-30): legacy 두 profile에 이수구분 값 사전(실제 값 9개와 괄호 없는 표기)을 개발자가 선언했다. 실제 학교 DB의 코드값 의미는 L2에서 검토해야 하며, 이 사전은 그 승인을 대신하지 않는다.

Phase 8-08(2026-09-30): 합성 변형 benchmark(12개 변형, 정답 mapping, gold QueryIR 20개)는 개발자 작성이다. AI mapping 제안 비교 결과를 논문이나 성능 주장에 쓰기 전에 L1(질문·기준)과 L3(결과) 검토가 필요하다.

## 바로 또는 최대한 빠르게 확인할 것

정식 40건 승인을 지금 끝낼 필요는 없다. 아래는 **설계가 잘못되면 후속 개발을 크게 되돌릴 수 있는** 짧은 확인이다.

| ID | 담당·시점 | 확인할 것 | 남길 증거 / 통과 기준 |
| --- | --- | --- | --- |
| E1 | 프로젝트 담당자 + 별도 팀원; 다음 큰 기능 확장 전 | `Course`와 `CourseOffering`의 행 단위, 학기·분반·교수·시간의 뜻, 현재 ontology와 승인 mapping의 대표 대응을 서로 설명해 본다. | 의미가 다른 사례와 미결정 코드를 목록화한다. 자동 제안을 업무상 확정 mapping으로 간주하지 않는다. |
| E2 | gold 작성자와 다른 팀원; 가까운 팀 미팅 | 질문 5~10개를 골라 문장만 보고 의도·예상 결과를 적은 뒤 gold Query IR·offering key와 대조한다. 0건, 여러 분반, 다단계 JOIN, 숫자 경계, 모호한 표현을 포함한다. | 불일치·애매함은 case ID별로 기록한다. 샘플 통과를 40건 전체 승인으로 표시하지 않는다. |
| E3 | 데이터 사용 책임자/지도교수 등 적절한 승인 주체; 실제 CNU 자료의 추가 활용·외부 공유 전 | 기존 데이터셋·DB·가명 artifact의 사용 목적, 접근 권한, 보관·반출·공개 가능 범위를 확인한다. | 허용 범위를 문서로 남긴다. 불명확하면 실제 원본과 private artifact를 공유·커밋하지 않는다. 기술적 가명화 결과만으로 공개를 승인하지 않는다. |
| E4 | 프로젝트 담당자 + 팀원; 일반화 실험 설계 전 | 독립 unseen school의 DDL·관계·코드 의미·안전한 샘플·정답을 구할 수 있는지 확인한다. 개발자가 만든 세 fixture와 분리 가능한지도 확인한다. | 출처·사용 조건·독립성·확보 가능 시점을 기록한다. 확보 불가면 논문 주장을 ‘합성 스키마 간 적응’으로 좁힐 계획을 세운다. |
| E5 | 개발 담당자(AI 보조 가능) + 실행 확인자; 데스크톱 이전 전 | 깨끗한 clone에서 테스트 명령, Python/Node 버전, 의존성, Docker 설정을 재현한다. 루트 `pytest` 수집 오류와 비어 있는 Ollama/DB 연결 설정을 별도 이슈로 남긴다. | 실행 명령·버전·성공/실패 로그 요약을 남긴다. 통과하지 않은 명령을 ‘전체 테스트 통과’로 보고하지 않는다. |

E1~E4의 **가능성 확인**은 지금 권장하지만, 미완료라고 합성 데이터 기반 `RecordedModel` 코어 개발을 중단할 필요는 없다. 단, 해당 데이터 사용·실제 DB 연결·성능 주장 직전에는 아래 관문을 반드시 적용한다.

## 기능이 완성된 뒤, 사용·공유·논문 제출 전에 확인할 것

| ID | 담당·관문 | 확인할 것 | 통과 기준 |
| --- | --- | --- | --- |
| L1 | 독립 팀원/도메인 검토자; **실제 모델 점수를 확정하기 전** | 40개 질문 각각의 한국어 의미, gold IR, typed 결과와 중복 행, 빈 결과를 seed만 보고 검토한다. | case별 승인·수정·보류 기록. 수정 시 benchmark version과 평가 수치를 재검토한다. 승인 전 `NEEDS_HUMAN_REVIEW` 유지. |
| L2 | 실제 DB 담당자 + 도메인 검토자; **학교 DB mapping publish·질의 전** | 물리 컬럼 뜻, 코드값, FK 없는 암묵적 관계, 중복/NULL, view, 학기·분반 기준, 민감 컬럼을 대조한다. | 근거가 있는 mapping과 관계만 승인한다. 불확실한 JOIN은 임의 추측하지 않는다. 실제 DB 계정은 필요한 읽기 권한만 갖는다. |
| L3 | 개발 담당자 + 별도 결과 검토자; **모델 성능 보고 전** | 기존 모델과 새 모델을 동일한 승인 질문·환경에서 비교한다. 정답·오답·거절·실패를 모두 분모에 넣고 일부 SQL/DB 결과를 독립적으로 수작업 확인한다. | 모델/프롬프트/DB/benchmark 버전, 지연·메모리와 실패 사례를 기록한다. oracle smoke와 실제 모델 수치를 혼합하지 않는다. |
| L4 | 개발에 참여하지 않은 최종 사용자; **화면 공개 전** | 실제 질문 흐름, 용어 이해, 모호한 질문의 확인 요청, 결과의 출처·한계 표시, 오류와 빈 결과를 사용해 본다. | 주요 오해·위험 행동을 수정하거나 미해결 제한으로 명시한다. 합성·허가된 데이터 환경에서 먼저 진행한다. |
| L5 | 데이터 책임자 + 보안 검토자; **실제 데이터 배포·외부 시연 전** | 로그·report·캐시·recording·백업에 질문/SQL/식별값이 남는지, 접근 권한·비밀키·보관 기간·삭제 절차를 점검한다. | 발견된 노출 경로를 차단한다. 가명화의 ‘알려진 값 잔존 0’은 익명성 또는 사용 허가의 증거로 쓰지 않는다. |
| L6 | 실험 설계 담당자 + 독립 검토자; **일반화 주장 전** | 학습·개발에 쓰지 않은 스키마, 질문 template, 식별값을 분리하고 schema evolution·관리자 교정량을 측정한다. | 데이터 누수·선택 편향을 기록한다. 독립 스키마가 없다면 학교 간 일반화 성공을 주장하지 않는다. |
| L7 | 팀원 한 명; **최종 공유·논문 재현성 확인 전** | 새 환경에서 백엔드 전체 테스트, 프론트 build·브라우저 흐름, PostgreSQL·Ollama 통합, 안전한 읽기 전용 실행을 재현한다. | 실패·건너뜀·환경 의존성을 그대로 보고하고 재현 절차·버전을 남긴다. |

## 개발 중 요청할 수 있는 짧은 중간 점검

사용자나 팀원은 어느 단계에서든 “현재 구현 근거, 통과한 테스트, 미검증 가정, 내가 직접 확인할 1~3개 항목”을 요청할 수 있다. AI는 완료된 기능과 미실행 환경을 구분하고, 새 기능이 ontology·mapping·gold·개인정보·논문 결론에 영향을 줄 때 검토 대상을 먼저 알려야 한다. 기계적 회귀 검사는 AI가 수행할 수 있지만, 사람 승인 상태를 AI가 대신 변경하지 않는다.

## 검토 기록 최소 양식

`ID / 대상 버전·fingerprint / 검토자(팀 역할) / 검토일 / 확인 방법 / 승인·수정·보류 / 이슈 ID 또는 수정 커밋 / 재검토 필요 여부`

기록에는 실제 질문·SQL·학생 정보·비밀키를 불필요하게 복사하지 않는다. 검토 전후에 gold·mapping·정책이 바뀌면 이전 승인과 측정 수치를 자동 승계하지 않는다. 미완료 항목은 완료로 체크하지 않고 다음 관문에 남긴다.
