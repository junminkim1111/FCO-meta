# 하네스 진행 보드

ORCA의 fco-meta 전용 읽기 전용 패널. 모델·네트워크 서비스 없이 로컬 CLI의 task-list/worker-list/gate-list와 golden JSON을 읽는다. 메일함 check는 호출하지 않는다.

## 실행 연결

오케스트레이터가 실제 Run을 만든 뒤 `reports/orca/active.json`에 `{"runId":"run_실제ID"}`를 원자적으로 저장한다. 연결하지 않으면 보드는 실행 없음을 표시하며 임의로 다른 프로젝트 Run을 고르지 않는다.

- Task 제목은 `[0] 평가 설계`, `[1] 진단`, `[2] 후보 A`처럼 docs/WORKFLOW.md의 단계 번호를 붙인다. 재시도 Task도 모두 표시한다.
- golden은 `--json-out reports/orca/<실제 Run ID>/cand-a.oracle.golden.json`처럼 저장한다. 기준선·후보·반복마다 고유 이름을 사용한다.
- 단계 상태는 ORCA 원본 상태, 검증은 저장된 JSON 집계다. 완료와 검증 통과를 혼동하지 않는다.
- 연결이 없을 때 표시하는 `reports/orca-setup-oracle.json`은 설정 검증이며 후보 평가가 아니다.
- 승인 기록은 승인 권한이나 비용 상한의 보장이 아니다. 보드에서 승인하거나 작업을 실행할 수 없다.

## 운영

`orca plugin dev load ./orca-progress`, `orca plugin open local.orca-progress`.
개발 폴더로 등록하므로 소스 수정 시 ORCA가 재로드한다. 별도 빌드·서버·패키지 설치 없음.

trusted-worker는 ORCA 구조상 사용자 계정 권한으로 실행된다. 이 구현은 고정 프로젝트 경로 읽기와 고정된 조회 명령만 사용하며 쓰기·모델 호출 코드는 없다. OS 수준의 읽기 전용 샌드박스는 아니다.
