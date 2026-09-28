---
name: issue-loop
description: >-
  이슈 하나를 설계·구현·검토 루프로 진행한다. "이슈 루프 돌려줘",
  "#123 구현 루프 시작해", "123번 설계부터 해줘", "루프 이어서",
  "루프 재개", "루프 해제" 같은 요청에 사용한다. 별도 git worktree를 만들고
  Herdr pane에서 설계 에이전트와 구현 에이전트를 번갈아 실행한다.
  이슈 내용만 확인하는 요청은 이 스킬이 아니라 issue-manager를 사용한다.
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

주 저장소의 `CLAUDE.md`가 Git에 추적되지 않는 파일이면 새 worktree에는 따라오지 않는다.
그러면 worktree에서 실행되는 에이전트가 프로젝트 지침 없이 작업한다.
worktree를 만든 직후에 복사한다. worktree에 이미 있는 파일은 덮어쓰지 않는다.

```sh
test -f <주 저장소>/CLAUDE.md \
  && ! git -C <주 저장소> ls-files --error-unmatch CLAUDE.md >/dev/null 2>&1 \
  && cp -n <주 저장소>/CLAUDE.md <worktree>/CLAUDE.md
```

복사한 파일은 worktree에서 미추적 변경으로 보인다. 대상 프로젝트가 `CLAUDE.md`를 무시하지 않으면
검토 호출에 전달되는 변경 파일 수에 1개가 더해진다.

작업 문서 폴더는 worktree 안의 `tmp/issue-<번호>`를 기본으로 쓴다.
대상 프로젝트가 `tmp/`를 무시하지 않는다면 커밋 대상에 섞이므로, 사용자에게 알리고
`git -C <worktree> check-ignore -q tmp/` 결과를 근거로 제시한다.

#### codex 디렉터리 신뢰 등록

`--implement-kind codex`로 새 worktree에서 처음 실행하면 codex가 디렉터리 신뢰를 묻는다.
그 창이 뜨는 동안 codex는 프롬프트를 받지 못해 실행기가 `agent_not_ready`로 멈춘다.
worktree를 만든 직후에 신뢰를 미리 등록해 이 중단을 없앤다.

```python
import pathlib
path = '<worktree 절대 경로>'
config = pathlib.Path.home() / '.codex' / 'config.toml'
text = config.read_text(encoding='utf-8') if config.is_file() else ''
if f'[projects."{path}"]' not in text:
    config.parent.mkdir(exist_ok=True)
    config.write_text(f'{text.rstrip()}\n\n[projects."{path}"]\ntrust_level = "trusted"\n',
                      encoding='utf-8')
```

이건 사용자 설정을 고치는 일이므로 먼저 사용자에게 알리고 동의를 받는다.
주 저장소 경로가 이미 신뢰 목록에 있어도 worktree 경로를 따로 등록한다.
codex가 실행 디렉터리를 기준으로 판단해서, 같은 저장소의 다른 worktree마다 다시 묻는다.

claude는 미리 등록하지 않는다. `~/.claude.json`의 `projects.<경로>.hasTrustDialogAccepted`에
같은 성격의 값이 있으나, worktree에서 신뢰 창이 뜨는 것을 확인한 적이 없다.
이 파일은 세션 상태까지 담고 있어 잘못 쓰면 Claude Code가 깨진다. 근거 없이 건드리지 않는다.
설계 에이전트가 신뢰 창 때문에 멈춘다면 그때 사용자에게 그 pane에서 직접 승인하도록 요청하고,
반복된다면 해당 항목만 읽고 고치는 방식으로 대응한다.

### 3. 옵션 조립

`--max-calls`는 반복 횟수에 맞춰 계산한다. 기본값 20을 그대로 쓰면 반복 도중 멈춘다.

| 상황 | 필요한 최소 호출 수 |
|---|---|
| 새 작업, `--stop-after review`로 N회 | `2N + 1` |
| 새 작업, `--stop-after implement`로 N회 | `2N` |
| 설계가 끝난 작업을 이어서 N회 | `2N` |

계산한 값과 20 중 큰 쪽을 `--max-calls`에 넣는다. 이 값은 상한일 뿐이라 여유가 있어도 손해가 없다.

### 4. 승인 중단 줄이기

구현 에이전트가 명령마다 승인을 물으면 실행기가 그 자리에서 멈추고 사용자 개입이 필요하다.
이 루프에서 가장 자주 멈추는 지점이므로 실행 전에 방침을 정해 둔다.

**검증 분담이 가장 효과가 크다.** 구현자는 `typecheck`와 `lint`까지만 돌리고,
테스트는 검토자가 자기 환경에서 돌려 판정 근거로 삼는 방식이다.
구현자의 보고를 근거로 쓰지 않는다는 검토 원칙과도 맞는다.
codex 샌드박스는 소켓 바인딩을 막아서 HTTP 테스트가 `listen EPERM`으로 실패하고,
권한 확장 실행에 매번 승인이 필요하다. 그 요청 자체를 없애는 셈이다.

**방침은 `--feedback`이 아니라 작업 문서에 적는다.** `--feedback`은 호출 하나에만 전달되고
성공하면 비워진다. 모든 작업에 적용할 방침이라면 설계자에게 `tasks.md`의
`## 검증 환경`에 적으라고 지시한다. 그래야 다음 구현 호출에도 전달된다.

```
--feedback '이번 검토에서 tasks.md의 `## 검증 환경`에 검증 분담을 적어라.
구현자는 npm run typecheck와 npm run lint까지만 실행하고 npm run test는 실행하지 않는다.
npm run test는 검토자가 자기 환경에서 직접 실행해 근거로 삼는다.'
```

**로그 경로에 run_id를 넣지 않는다.** 구현자가 `/tmp/<이슈>-<run_id>-test.log`처럼
호출마다 달라지는 경로를 쓰면, codex의 "don't ask again"이 접두사를 못 맞춰 매번 다시 묻는다.
고정 경로를 쓰게 하면 사용자가 한 번만 허용해도 이후 호출이 조용히 지나간다.

### 5. 실행

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

**구현 pane은 에이전트 종료가 아니라 pane 자체를 닫아야 한다.** 실행기는 `herdr pane list`에
그 pane이 남아 있는지를 본다. codex를 끝내도 셸이 남은 pane은 여전히 재개를 막는다.

**pane 번호만으로는 화면에서 못 찾는다.** 실행기의 중단 메시지가 위치를 함께 알려주지만,
더 필요하면 `herdr pane layout --pane <ID>`로 좌표를 뽑아 사용자에게 설명한다.

```sh
<원래 실행 명령> --resume --feedback '<사용자가 확인한 상태와 결정>'
```

`--feedback`은 사용자가 직접 확인한 내용으로 채운다. 추측해서 쓰지 않는다.

같은 worktree에 미완료 작업이 있으면 다른 작업을 시작할 수 없다. 앞선 작업을 포기할 때만 해제한다.
해제하면 상태 파일이 지워지고 설계 pane도 함께 닫힌다. 작업 문서와 호출 기록은 남는다.

```sh
<스킬>/run.sh --repo <worktree 절대 경로> --release
```

## 끝난 작업을 이어서 확장하기

요구사항이 바뀌어 설계부터 다시 손봐야 하는 경우가 흔하다. 이때 `--release`를 쓰지 않는다.
`--redesign`이 단계를 설계로 되돌리고, 설계자가 기존 문서와 실제 코드를 대조해 설계를 갱신한다.

```sh
<원래 실행 명령> --redesign --stop-after design --iterations 1 \
  --feedback '<바뀐 결정과 그 근거>'
```

`complete` 상태와 `ready` 상태 모두에서 쓸 수 있다. 중단된 호출이 있으면 거부되므로
`--resume`으로 먼저 정리한다. 설계 버전을 올리고 무엇이 바뀌었는지 기록하는 일은 설계자 몫이다.

## 하지 말 것

- 주 저장소를 `--repo`로 넘기지 않는다. 무인 변경을 막기 위한 제약이다.
- 승인 창을 대신 통과시키지 않는다. 에이전트의 권한 설정을 그대로 쓴다.
- 커밋, 푸시, 이슈 댓글, PR·MR 생성은 하지 않는다. 루프가 끝난 뒤 사용자가 결정한다.
- 설계 세션이 살아 있는 동안 `--design-kind`를 바꾸지 않는다.
- 사용자가 만들지 않은 pane을 닫지 않는다.
