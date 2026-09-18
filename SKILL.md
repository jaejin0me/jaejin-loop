---
name: issue-loop
description: >-
  이슈 하나를 설계·구현·검토 루프로 진행한다. "이슈 루프 돌려줘",
  "#123 구현 루프 시작해", "123번 설계부터 해줘", "루프 이어서",
  "루프 재개", "루프 해제" 같은 요청에 사용한다. 별도 git worktree를 만들고
  Herdr pane에서 설계 에이전트와 구현 에이전트를 번갈아 실행한다.
  이슈 내용만 확인하는 요청은 이 스킬이 아니라 issue-checker를 사용한다.
---

# issue-loop

이 스킬 폴더의 `run.sh`가 실행기다. 아래에서 `<스킬>`은 이 SKILL.md가 있는 폴더를 뜻한다.
루프의 동작 원리와 모든 옵션은 같은 폴더의 `README.md`에 있다. 판단이 필요하면 그 문서를 먼저 읽는다.

## 이 스킬이 하는 일

사용자 대신 실행 준비를 끝내고 `run.sh`를 호출한다. 준비란 이슈 번호 확인, worktree 생성,
경로와 옵션 조립, 사전 점검 네 가지다.

설계와 구현은 하지 않는다. 그 일은 `run.sh`가 띄우는 별도 에이전트가 수행한다.
이 스킬은 실행기를 올바르게 호출하고 결과를 사용자에게 전달하는 데까지만 관여한다.

## 사전 점검

실행 전에 한 번에 확인한다. 하나라도 실패하면 실행하지 말고 사용자에게 알린다.

```sh
echo "HERDR_ENV=${HERDR_ENV:-unset}"
command -v herdr claude codex
git -C <대상 저장소> remote get-url origin
```

- `HERDR_ENV`가 `1`이 아니면 Herdr pane 밖이다. 실행기가 거부하므로 사용자에게 Herdr 안에서 열어 달라고 요청한다.
- `origin` 호스트가 github.com이면 `gh auth status`, 그 외 호스트면 `glab auth status`로 이슈 조회 인증을 확인한다.
  설계 에이전트가 이 인증으로 이슈를 읽는다.
- `--design-kind`와 `--implement-kind`로 지정할 CLI만 있으면 된다. 기본값은 설계 `claude`, 구현 `codex`다.

## 실행 절차

### 1. 이슈 확정

번호만 받으면 그대로 넘긴다. 설계 에이전트가 worktree의 `origin` 호스트를 보고 `gh` 또는 `glab`로 조회한다.
다른 저장소의 이슈면 전체 URL을 받는다.

이슈 제목을 미리 알아야 worktree 이름을 정할 수 있는 것은 아니다. 번호만으로 충분하므로 여기서 조회하지 않는다.

### 2. worktree 준비

주 저장소에서는 실행할 수 없다. 실행기가 별도 worktree만 허용한다.
이미 해당 이슈의 worktree가 있으면 재사용하고, 없으면 만든다.

```sh
git -C <주 저장소> worktree list
git -C <주 저장소> worktree add ../<저장소 이름>-issue-<번호> -b loop/issue-<번호>
```

브랜치가 이미 있으면 `-b` 없이 붙인다. 기존 작업이 있는 브랜치를 쓸 때는 먼저
`git -C <worktree> status`로 상태를 확인하고 사용자에게 보여준다.

작업 문서 폴더는 worktree 안의 `tmp/issue-<번호>`를 기본으로 쓴다.
대상 프로젝트가 `tmp/`를 무시하지 않는다면 커밋 대상에 섞이므로, 사용자에게 알리고
`git -C <worktree> check-ignore -q tmp/` 결과를 근거로 제시한다.

### 3. 옵션 조립

`--max-calls`는 반복 횟수에 맞춰 계산한다. 기본값 20을 그대로 쓰면 반복 도중 멈춘다.

| 상황 | 필요한 최소 호출 수 |
|---|---|
| 새 작업, `--stop-after review`로 N회 | `2N + 1` |
| 새 작업, `--stop-after implement`로 N회 | `2N` |
| 설계가 끝난 작업을 이어서 N회 | `2N` |

계산한 값과 20 중 큰 쪽을 `--max-calls`에 넣는다. 이 값은 상한일 뿐이라 여유가 있어도 손해가 없다.

### 4. 실행

```sh
<스킬>/run.sh '<이슈 번호 또는 URL>' \
  --repo <worktree 절대 경로> \
  --work tmp/issue-<번호> \
  --design-kind claude \
  --implement-kind codex \
  --iterations <N> \
  --max-calls <계산값>
```

호출 하나가 기본 1,800초까지 걸린다. 반드시 백그라운드로 실행하고, 실행 명령 전문을 사용자에게 먼저 보여준다.
포그라운드로 돌리면 도구 제한 시간에 걸려 루프가 강제 종료된다.

실행이 끝나면 종료 메시지를 그대로 전달한다. 지정한 단계에서 멈춘 것은 오류가 아니다.

## 중단과 재개

승인 대기, 타임아웃, Ctrl+C로 멈추면 실행기는 다음 에이전트를 부르지 않고 pane을 남긴다.
타임아웃은 에이전트가 끝났다는 뜻이 아니다. 해당 pane에서 작업이 계속되는지 사용자가 직접 확인해야 한다.

```sh
cat <주 저장소>/.git/worktrees/<worktree 이름>/herdr-issue-loop/state.json
tail -5 <주 저장소>/.git/worktrees/<worktree 이름>/herdr-issue-loop/log.jsonl
```

`state.json`의 `pane`으로 어느 터미널인지 알려주고, 사용자가 상태를 확인한 뒤 그 내용을 받아 재개한다.
구현 pane은 사용자가 닫고, 설계 pane은 입력 대기 상태로 만든 뒤 재개하는 것이 원칙이다.

```sh
<원래 실행 명령> --resume --feedback '<사용자가 확인한 상태와 결정>'
```

`--feedback`은 사용자가 직접 확인한 내용으로 채운다. 추측해서 쓰지 않는다.

같은 worktree에 미완료 작업이 있으면 다른 작업을 시작할 수 없다. 앞선 작업을 포기할 때만 해제한다.

```sh
<스킬>/run.sh --repo <worktree 절대 경로> --release
```

## 하지 말 것

- 주 저장소를 `--repo`로 넘기지 않는다. 무인 변경을 막기 위한 제약이다.
- 승인 창을 대신 통과시키지 않는다. 에이전트의 권한 설정을 그대로 쓴다.
- 커밋, 푸시, 이슈 댓글, PR·MR 생성은 하지 않는다. 루프가 끝난 뒤 사용자가 결정한다.
- 설계 세션이 살아 있는 동안 `--design-kind`를 바꾸지 않는다.
- 사용자가 만들지 않은 pane을 닫지 않는다.
