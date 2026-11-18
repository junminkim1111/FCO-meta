# 하네스 개선 — ORCA 운영 규약

오케스트레이터와 모든 작업자가 읽는 작업 지시다. ORCA 설정 스키마가 아니며, 비용 상한이나 접근 통제를 기술적으로 강제하지 않는다.

흐름: **설계 두 개 → 상호 반박 → 사용자 선택 → 구현 하나 → 테스트 → 사용자 승인 → 머지**. 진 설계는 버리지 않고 예비안으로 둔다.

## 역할

| 역할 | agent | model |
|---|---|---|
| 오케스트레이터 | claude | claude-sonnet-5-5 |
| 설계자 A | codex | gpt-6-astra |
| 설계자 B | claude | claude-opus-5-5 |
| 구현자 (A 설계) | codex | gpt-6-sol |
| 구현자 (B 설계, 수렴안) | claude | claude-sonnet-5-5 (오케스트레이터와 다른 세션) |
| 교훈 작성 | opencode | opencode/glm-5.3-flash-free |

- Codex·Claude는 사용자의 구독 로그인만 쓴다. API 종량제 전환이나 다른 모델로의 대체는 하지 않는다. 인증·모델이 맞지 않으면 멈추고 보고한다.
- GLM은 무료 모델만 쓴다. 한도가 차면 교훈 초안은 오케스트레이터가 쓴다.
- 역할마다 새 세션을 띄운다. 추론 강도는 사용자 설정을 그대로 둔다.

## ORCA 사용

```bash
ORCA=/Applications/Orca.app/Contents/Resources/bin/orca
"$ORCA" skills get orchestration --full
```

- CLI 지침은 세션 시작 때 한 번 읽고, 이 문서와 다르면 CLI를 따른다.
- worker 시작 후 polling하지 않고 턴을 끝내 ORCA 알림을 기다린다. 실패한 start는 receipt의 `failedStage`와 `next`를 보고 판단하며, 맹목적으로 재실행하지 않는다.
- Task 제목에 단계 번호를 붙인다: `[1] 설계 A`, `[4] 구현 B`. 진행 보드가 이 번호로 단계를 표시한다.
- 실제 Run을 만든 뒤 `reports/orca/active.json`에 `{"runId":"<Run ID>"}`를 저장한다. 결과 파일은 `reports/orca/<Run ID>/`에 둔다.

## 실행 규칙 (모든 작업자)

- MAIN은 `/Users/junmin/fco-meta`, Python은 `/opt/anaconda3/envs/fco-meta/bin/python`이다. 기본 셸의 `python`에는 pytest가 없다.
- 각 worktree 루트에서 반드시 `<Python> -m ...`으로 실행한다. `fco_meta`가 MAIN에 editable 설치돼 있어서, `-m` 없이 실행하면 MAIN의 코드를 테스트하게 된다.
- `eval/`과 `fco_meta/chatbot/golden.py`는 수정하지 않는다. 채점 기준이 바뀌면 비교가 무효가 된다.
- golden은 항상 MAIN의 DB와 케이스를 읽는다: `--db $MAIN/data/fco_meta.sqlite --cases $MAIN/eval/golden.json`.
- worktree에는 `.env`가 없다. 실제 API 테스트는 오케스트레이터가 MAIN의 `.env`를 불러와서 실행한다.
- `--oracle` 없는 golden(실제 API)은 3단계에서 사용자가 승인한 범위만 실행한다.

## 단계

### 0. 정리 (오케스트레이터)

사용자 요청에서 목적, 범위, 완료 기준을 정리한다. 꼭 필요한 정보가 빠졌을 때만 질문한다. 요청이 없으면 작업을 만들지 않고 기다린다.

- 기준선 커밋 SHA를 기록한다.
- 커밋 안 된 변경이 있으면 시작 전에 사용자에게 알린다. 몰래 포함하거나 버리지 않는다.
- 관련 golden 케이스가 있으면 번호를 찾는다. 없고 모델 동작에 관한 문제라면, 추가할 케이스 초안을 3단계 보고에 넣는다(직접 추가하지 않는다).

### 1. 설계 (설계자 A·B 병렬, `--worktree current`, 읽기 전용)

두 설계자에게 같은 문제 설명과 `eval/LESSONS.md`를 준다. 서로의 설계는 주지 않는다. 각자 제출할 것:

- 관찰한 사실과 코드 위치
- 원인 가설, 그리고 그 가설이 틀렸다면 무엇이 보일지
- 수정 방안: 바꿀 파일과 내용, 추가할 테스트
- 영향받을 수 있는 기존 기능과 golden 케이스 번호

코드는 쓰지 않는다.

### 2. 상호 반박 (설계자 A·B, 1회)

각 설계자가 상대 설계를 받아 반박과 자기 입장을 제출한다. 반박에는 근거를 붙인다: 코드 위치, 깨질 케이스, 재현 방법. 근거 없는 선호는 "의견"으로 따로 표시한다. 상대가 옳다고 판단하면 그렇게 말해도 된다.

### 3. 설계 보고 (오케스트레이터 → 사용자, 멈추고 기다린다)

보여줄 것:

- 두 설계 요약, 반박의 요지(근거 있는 것과 의견을 나눠서)
- 추천 하나: A, B, 또는 수렴(반박이 상호 보완일 때만)
- 테스트 계획: pytest, 실제 API로 돌릴 golden 케이스 번호, 기준선 + 구현본 각 1회, 총 호출 수와 답할 모델(`--mode`)

사용자의 선택이 곧 그 테스트 계획에 대한 승인이다. 침묵이나 이전 승인은 새 승인이 아니다. 사용자가 다른 방향을 주면 설계자 B가 그 방향으로 설계를 다시 쓰고 3단계를 반복한다.

### 4. 구현 (선택된 설계의 구현자, `--worktree new-child`)

- A 설계는 sol, B 설계와 수렴안은 Sonnet(새 세션)이 구현한다.
- 구현자에게 줄 것: 선택된 설계, 반영할 반박 내용, 허용 파일, "`eval/` 수정 금지, 실제 API 호출 금지", 완료 조건(커밋, pytest 통과, 남은 불확실성 보고).
- 설계가 틀렸다고 판단되면 범위를 몰래 넓히지 말고 보고하고 멈춘다.
- `tests/`는 대부분 gitignore 대상이다. 새로 만들거나 고친 테스트는 `git add -f`로 커밋해 diff에 포함한다.

### 5. 테스트 (오케스트레이터)

구현 worktree 루트에서 실행한다.

```bash
/opt/anaconda3/envs/fco-meta/bin/python -m pytest -q
```

```bash
/opt/anaconda3/envs/fco-meta/bin/python -m fco_meta.chatbot.golden --oracle --db $MAIN/data/fco_meta.sqlite --cases $MAIN/eval/golden.json --out $MAIN/reports/orca/<Run ID>/impl.oracle.md --json-out $MAIN/reports/orca/<Run ID>/impl.oracle.golden.json
```

실제 API 테스트는 승인된 케이스만, 기준선(MAIN)과 구현본에서 각 1회 실행한다.

```bash
set -a; source $MAIN/.env; set +a; /opt/anaconda3/envs/fco-meta/bin/python -m fco_meta.chatbot.golden --only <승인된 번호> --db $MAIN/data/fco_meta.sqlite --cases $MAIN/eval/golden.json --out $MAIN/reports/orca/<Run ID>/<base|impl>.md --json-out $MAIN/reports/orca/<Run ID>/<base|impl>.golden.json
```

- oracle 종료 코드: 0은 검사한 도구 조건 통과, 1은 실패, 2는 검증 불가(DB 없음 등). oracle은 모델의 도구 선택과 답변을 보지 않는다.
- pytest가 실패하면 실제 API 테스트를 돌리지 않고 구현자에게 돌려보낸다(1회까지).

### 6. 결과 보고 (오케스트레이터 → 사용자, 멈추고 기다린다)

보여줄 것: 커밋 SHA, diff 요약, pytest 결과, 케이스별 기준선/구현본 통과·실패 표, 실패한 조건, 남은 불확실성. 1회 실행 결과는 확률적이라는 점을 함께 적는다.

사용자의 선택:
- **승인** → 7단계. 보고한 SHA만 머지한다.
- **예비안** → 진 설계로 4단계부터. 실제 API 테스트 범위가 같으면 3단계 승인을 그대로 쓴다.
- **수정 방향 제시** → 설계자 B가 반영해 설계를 고치고 4단계부터.

### 7. 머지와 교훈

1. 오케스트레이터가 승인된 SHA를 머지하고 푸시한다. 머지 직전 브랜치 끝이 승인된 SHA와 다르면 멈추고 보고한다.
2. GLM에게 문제, 선택된 설계, 반박 요지, 테스트 표를 주고 `eval/LESSONS.md` 맨 아래에 1~3줄을 쓰게 한다. 형식: `- YYYY-MM-DD <실패 조건> → <수정 원칙> → <방어하는 테스트>`. 오케스트레이터가 내용을 확인해 별도 커밋으로 넣는다.
3. worker는 ORCA의 receipt와 recovery 지침대로 정리한다. worktree 삭제는 사용자가 지시할 때만 한다. 사용자 소유 세션이나 실행 중인지 불확실한 프로세스는 종료하지 않는다.
