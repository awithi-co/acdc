# ACDC

> [AWITHI](https://github.com/awithi-co) 패밀리 — *AI with AI*. AC ↔ DC, 컨텍스트는 양방향으로 흐릅니다.

**다른 언어로 읽기:** [English](README.md) · 한국어

Claude Code와 Codex 사이에서 로컬 코딩 에이전트 세션을 이어가세요. 한 에이전트에서 막혀서 다른 에이전트가 작업을 이어받게 하고 싶을 때, ACDC는 이전 로컬 세션 파일과 현재 레포 상태를 읽은 다음, 구조화된 요약을 새 에이전트에게 넘겨서 이어갈 수 있게 해줍니다 — 마지막 몇 개의 메시지를 그대로 되풀이하는 게 아니라요.

## 지원 방향

- **Claude Code**가 **Codex** 세션을 이어받기
- **Codex**가 **Claude Code** 세션을 이어받기

## 요구사항

PATH에 Python 3가 있어야 합니다. macOS 12.3+와 대부분의 Linux 배포판은 기본 탑재되어 있고, Windows는 python.org에서 설치하시면 됩니다. skill은 `uv run python` → `python3` → `python` 순서로 시도하며, 셋 다 없으면 명확한 메시지와 함께 중단합니다.

## 설치 (Claude Code)

```
/plugin marketplace add https://github.com/awithi-co/acdc
/plugin install acdc
```

그런 다음 세션 이름 또는 id로 skill을 호출합니다:

```
/resume-codex-session <session-name>
/resume-codex-session <session-id>
```

예시:

```
/resume-codex-session api-refactor       # /rename으로 이름을 설정했다면 그 이름
/resume-codex-session 019dc08e           # UUID (앞자리만 넣어도 됨)
```

## 설치 (Codex)

```
/plugin marketplace add https://github.com/awithi-co/acdc
/plugin install acdc
```

그런 다음 세션 이름 또는 id로 skill을 호출합니다:

```
/resume-claude-session <session-name>
/resume-claude-session <session-id>
```

예시:

```
/resume-claude-session api-refactor      # /rename으로 이름을 설정했다면 그 이름
/resume-claude-session a3b5c9f2          # UUID (앞자리만 넣어도 됨)
```

## 동작 원리

각 방향은 두 개의 Python 헬퍼를 포함한 skill을 제공합니다:

- 이름, cwd 힌트, 최신성을 기준으로 로컬 세션 후보에 점수를 매기는 세션 파인더
- 트랜스크립트나 rollout JSONL에서 타임라인을 재구성하는, 범위가 제한된 요약기

그런 다음 skill은 재구성된 상태를 현재 `git status`, 브랜치, 워크트리와 교차 검증한 후 인수인계 요약을 만듭니다. 새 에이전트는 원본 트랜스크립트 내용이 아닌 이 인수인계를 기반으로 동작합니다.

Python 3 표준 라이브러리만 사용합니다. 네트워크 접근 없음. 세션 저장소에 대해 읽기 전용입니다.

## 세션 저장 구조

각 에이전트가 데이터를 어디에 보관하는지 알면 인수인계 과정이 추적 가능해집니다. ACDC는 이 경로들을 읽기만 하며, 쓰기는 하지 않습니다.

### Claude Code — `~/.claude/`

```
projects/<escaped-cwd>/<session-uuid>.jsonl   transcript (전체 이벤트 스트림)
sessions/<pid>.json                           live 세션 메타데이터 (name, cwd, status)
tasks/<session-uuid>/N.json                   구조화된 todos
history.jsonl                                 프롬프트 단위 로그
```

- 세션은 UUID로 식별됩니다. transcript 파일 이름이 `<uuid>.jsonl`이며, 상위 디렉토리는 원본 작업 디렉토리를 인코딩합니다 (슬래시가 대시로 치환됨).
- 세션 rename 이벤트는 transcript 내부에 `custom-title` 레코드로 들어갑니다. 실행 중인 세션은 `~/.claude/sessions/<pid>.json`을 통해 현재 이름을 노출합니다.
- 태스크는 별도 JSON 파일로 저장되며(`{id, subject, description, activeForm, status}`), todo 하나당 파일 하나입니다.

### Codex — `~/.codex/`

```
sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl   rollout (이벤트 스트림, 진실의 원천)
session_index.jsonl                                    {id, thread_name, updated_at} 포인터 파일
shell_snapshots/<...>                                  세션별 shell 상태
history.jsonl                                          프롬프트 단위 로그
```

- 세션은 UUID로 식별됩니다. rollout 파일은 cwd가 아닌 날짜로 파티셔닝됩니다.
- `session_index.jsonl`은 이름 lookup을 빠르게 하기 위한 가속기입니다. 디스크의 모든 rollout이 들어있다고 보장되지 않으므로, rollout 트리가 진실의 원천(source of truth)입니다.
- Todo와 plan은 별도 파일이 아닙니다. rollout 안에 `function_call` 이벤트(`update_plan`, `TodoWrite`)로 인라인으로 들어갑니다.

## 프라이버시 모델

ACDC는 현재 에이전트를 위한 인수인계를 만들기 위해 여러분 머신의 로컬 Claude Code 및 Codex 세션 파일을 읽습니다. 외부 서비스로 어떤 것도 업로드하지 않으며, 트랜스크립트 내용을 정제(sanitize)하거나 마스킹(redact)하지도 않습니다. 하나의 로컬 코딩 에이전트가 다른 에이전트의 작업을 이어받기를 의도적으로 원할 때 사용하세요.

## 제한사항

- 새 에이전트가 다른 에이전트의 세션 저장소에 로컬 파일시스템 접근 권한을 가지고 있다고 가정합니다.
- 세션 저장소 형식은 각 제품이 소유하므로, 큰 버전 변경이 있을 경우 파서 업데이트가 필요할 수 있습니다.
- 같은 이름에 여러 후보가 일치할 때는 세션 피커가 필요하며, skill이 점수화된 후보를 보여주고 물어봅니다.
- 원본 세션의 작업 디렉터리가 이동되거나 삭제된 경우, 레포 상태 검증은 파일시스템 체크로 폴백합니다.

## 스킬

ACDC는 플러그인마다 3개의 스킬을 제공한다 (인계 1 + 자가 회상 2).

| 스킬 | 용도 | 발동 예시 |
|------|------|-----------|
| `resume-codex-session` (Claude 측) / `resume-claude-session` (Codex 측) | 인계: 상대 에이전트의 세션을 이름/UUID로 받아 이어받음 | "api-refactor라는 codex 세션 이어받아줘" |
| `recall-session` | 자가 회상: 자기 과거 세션을 이름/UUID로 찾아 요약 | "어제 한 api-refactor 세션 보여줘" |
| `recall-context` | 자가 회상: 자기 현재/최근 세션에서 자유 텍스트로 내용 검색 | "ACDC 네이밍 어디서 결정했지" |

두 recall 스킬은 resume과 같은 finder/summarizer 코드 + 새 헬퍼 `grep_recall.py`를 공유한다. 각 플러그인은 self-contained — finder/summarizer는 심볼릭 링크가 아니라 사본이다 (플러그인 설치 시 디렉토리 트리 그대로 배포되니까).

> 컨트리뷰터 노트: `find_*_session.py` 또는 `summarize_*.py`를 수정하면 `recall-session/scripts/` 안의 사본도 같이 갱신해야 한다. 향후 `_vendor/` 같은 공유 위치로 추출 예정.

## 라이선스

MIT
