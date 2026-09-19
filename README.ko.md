# ACDC

> [AWITHI](https://github.com/awithi-co) 패밀리 — *AI with AI*. ACDC = **A**gent **C**ontext **D**elivery **C**hannel — AC ↔ DC, 컨텍스트는 양방향으로 흐릅니다.

**다른 언어로 읽기:** [English](README.md) · 한국어

코딩 에이전트와의 세션에서 가장 값진 것은 컨텍스트입니다 — 무엇을 결정했고, 무엇을 이미 시도해 봤고, 어디서 끊겼는지. 그런데 가장 잘 사라지는 것이기도 합니다: rate limit이 흐름을 끊고, compact 이후 에이전트가 살짝 바보가 되고, 며칠만 지나도 어디서 다뤘는지 흐릿해집니다.

ACDC는 로컬 Claude Code와 Codex 세션 저장소를 읽어서, 필요한 곳에 컨텍스트를 다시 갖다 놓습니다.

## ACDC가 하는 일

| 스킬 | 기능 | 발동 예시 |
|------|------|-----------|
| `resume-codex-session` (Claude 측) / `resume-claude-session` (Codex 측) | 에이전트 간 인계: 이전 세션과 현재 레포 상태를 읽고, 구조화된 요약을 새 에이전트에게 넘겨서 마지막 몇 메시지를 그대로 되풀이하는 게 아니라 실제로 작업을 이어가게 합니다. | "api-refactor라는 codex 세션 이어받아줘" |
| `recall-session` | 자가 회상: 본인의 과거 세션을 이름이나 UUID로 조회. 무엇을 결정했고, 무엇이 바뀌었고, 어디서 끊겼는지 타임라인 요약. | "어제 한 api-refactor 세션 보여줘" |
| `recall-context` | 토픽 검색: 세션 간에서 어디서 그 얘기 했었는지 찾아냅니다 — 현재 세션의 compact 이전까지 포함해서. | "ACDC 네이밍 어디서 결정했지" |

에이전트당 5개 스킬, 각 플러그인은 자급자족(self-contained).

## 지식: OKF

회상(recall)은 세션이 살아 있는 동안의 연속성입니다. 하지만 세션이 만들어낸 것 중 일부는 세션보다 오래 남아야 합니다. 무엇을 택했고 무엇을 버렸는지, 어떤 사고가 어떤 규칙을 남겼는지, 작업이 정의한 용어가 무엇인지. 두 개의 스킬이 그것을 [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog)(OKF) v0.2 번들에 기록합니다. 번들은 마크다운 디렉토리이고, frontmatter가 메타데이터, 본문의 `.md` 링크가 그래프 엣지입니다.

| 스킬 | 기능 | 발동 예시 |
|------|------|-----------|
| `okf-write` | 문서 하나를 OKF 규약대로 작성·수정하고 번들을 검사합니다. | "재시도 정책 문서를 지식 베이스에 추가해줘" |
| `okf-distill` | 끝난 Claude Code·Codex 세션을 읽고, 세션보다 오래 남아야 할 것을 노드로 씁니다. | "api-refactor 세션을 지식 베이스로 정제해줘" |

**노드 유형 — 세 가지, 네 번째는 없습니다.**

| 유형 | 기록하는 것 | 반드시 담아야 할 것 |
|------|-------------|---------------------|
| `Decision` | 작업이 내린 선택 | 무엇을 결정했는지, 왜, 어떤 대안을 기각했는지 |
| `Lesson` | 작업이 얻어낸 규칙 | 규칙, 그리고 그 규칙을 낳은 사건 |
| `Concept` | 작업이 정의한 사물·용어 | 정의, 그리고 그것이 왜 중요한지 |

"현재 상태" 노드는 의도적으로 없습니다. 어디까지 진행됐는지를 적은 기록은 며칠이면 거짓이 되지만, 그 상태를 만든 결정은 계속 참입니다. `okf-distill`은 그런 후보를 그 뒤의 결정으로 고쳐 쓰거나, 버립니다.

**출처.** 각 노드는 어디서 왔는지를 OKF v0.2 §5 가 정한 필드로 기록합니다. `sources` 는 `resource` URI 를 반드시 가진 항목의 목록이고(세션은 `claude-session://<이름-또는-id>` 또는 `codex-session://<이름-또는-id>` 가 됩니다), `generated` 은 `{ by, at }` 를 담습니다. `okf_node.py` 가 두 필드를 모두 써 주므로, 노드는 언제나 출처 세션까지 거슬러 갈 수 있습니다.

**이름.** 세 가지 역할은 고정이지만 그 이름은 번들을 따릅니다. 이미 교훈을 `Finding` 이라 부르는 지식 베이스라면 그대로 씁니다. 스킬이 기존 문서를 먼저 읽고, 비어 있는 번들에만 기본값 `Decision` / `Lesson` / `Concept` 을 씁니다. 링크는 파일 상대경로로 씁니다. OKF 는 두 형식을 모두 풀지만 Obsidian 그래프 뷰는 이 형식만 풀기 때문입니다.

**번들 위치.** 레포 루트의 `.acdc/okf.json`에 키 하나로 둡니다.

```json
{"bundle": "docs/knowledge"}
```

두 스킬 모두 이 파일을 읽고, 없으면 한 번 물어본 뒤 만들어 둡니다.

**검사.** `okf-write/scripts/okf_check.sh <bundle> [--strict] [--graph]` 는 [okf-conformance](https://github.com/Sudhakaran88/okf-conformance) 검사기 사본(MIT © WitsCode, `scripts/vendor/`)을 실행합니다. 내려받는 것도, 머신 밖으로 나가는 것도 없습니다. 이 스크립트만 `node`가 필요하고 나머지 ACDC는 필요하지 않습니다. 번들이 레포 안에 있으면 검사 대상 파일 목록을 git에서 받으므로 무시된 파일·로컬 전용 파일은 세지 않습니다. `--graph`는 `<bundle>/okf-graph.html`(단일 파일 인터랙티브 그래프)을 생성합니다.

`okf-distill`이 읽는 트랜스크립트는 정제되지 않은 원본입니다. 스킬은 노드를 쓰기 전에 호스트명·주소·사람 이름·사내 URL·자격증명을 걷어내고, 그것 없이는 쓸 수 없는 노드는 버립니다. 다만 공개할 번들이라면 사람이 한 번 읽어보는 편이 좋습니다.

## 설치

**Claude Code** — CLI 안에서 슬래시 명령:

```
/plugin marketplace add https://github.com/awithi-co/acdc
/plugin install acdc
```

**Codex** — 쉘 명령 + enable:

```
codex plugin marketplace add awithi-co/acdc
```

`marketplace add`는 ACDC를 *사용 가능* 상태로 등록만 합니다. 자연어 디스패치로 스킬이 발견되려면 활성화가 필요합니다 — `codex` 인터랙티브 모드의 플러그인 피커에서 ACDC를 선택하거나, `~/.codex/config.toml`에 다음을 추가하세요:

```toml
[plugins."acdc@acdc"]
enabled = true
```

설치 후에는 자연어로 발동하면 됩니다 — 위 표의 발동 예시가 그대로 동작합니다. 플러그인의 manifest가 각 에이전트의 올바른 스킬로 요청을 라우팅합니다.

## 동작 원리

각 스킬은 두 개의 Python 헬퍼를 포함합니다:

- 이름, cwd 힌트, 최신성으로 로컬 세션 후보에 점수를 매기는 **finder**
- 트랜스크립트나 rollout JSONL에서 타임라인을 재구성하는 **범위 제한 summarizer**

**인계** 시에는 재구성된 상태를 현재 `git status`, 브랜치, 워크트리와 교차 검증한 후 요약을 만듭니다. 새 에이전트는 원본 트랜스크립트 내용이 아닌 이 인수인계를 기반으로 동작합니다.

**식별자 회상** 시에는 동일한 finder/summarizer 페어를 쓰되 레포 교차 검증은 생략합니다 (에이전트가 이미 자기 컨텍스트를 가지고 있으므로).

**토픽 검색** 시에는 `grep_recall.py` 헬퍼가 JSONL 파일(현재 세션 + 최근 파티션)을 스캔하여 매치된 segment를 주변 이벤트 컨텍스트와 함께 반환합니다.

Python 3 표준 라이브러리만 사용. 네트워크 접근 없음. 세션 저장소에 대해 읽기 전용.

## 세션 저장 구조

ACDC는 다음 경로들을 읽기만 하며, 쓰기는 하지 않습니다.

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

## 요구사항

PATH에 Python 3가 있어야 합니다. macOS 12.3+와 대부분의 Linux 배포판은 기본 탑재되어 있고, Windows는 python.org에서 설치하시면 됩니다. 스킬은 `uv run python` → `python3` → `python` 순서로 시도하며, 셋 다 없으면 명확한 메시지와 함께 중단합니다.

OKF 번들 검사기는 추가로 PATH에 [Node.js](https://nodejs.org)가 필요합니다. `okf_check.sh` 하나만 쓰며, 회상·인계 스킬은 필요하지 않습니다.

## 프라이버시 & 제한사항

- ACDC는 인수인계나 회상 요약을 만들기 위해 머신의 로컬 세션 파일을 읽습니다. 외부 서비스로 어떤 것도 업로드하지 **않으며**, 트랜스크립트 내용을 정제(sanitize)하거나 마스킹(redact)하지도 **않습니다**.
- 각 플러그인은 자기 세션 저장소에 대한 로컬 파일시스템 접근 권한을 필요로 합니다 (인계 시에는 상대 에이전트 저장소까지).
- 세션 저장소 형식은 각 제품이 소유하므로, 큰 버전 변경이 있을 경우 파서 업데이트가 필요할 수 있습니다.
- 같은 이름에 여러 세션이 일치할 때는 스킬이 점수화된 후보를 보여주고 묻습니다.
- 원본 세션의 작업 디렉토리가 이동되거나 삭제된 경우, 레포 상태 검증은 파일시스템 체크로 폴백합니다.

## 컨트리뷰터

각 플러그인은 자급자족(self-contained) — `finder + summarizer` 스크립트는 심볼릭 링크가 아니라 사본 (플러그인 설치 시 디렉토리 트리 그대로 배포되니까).

> `find_*_session.py` 또는 `summarize_*.py`를 수정하면 `recall-session/scripts/` 안의 사본도 같이 갱신해야 합니다. 향후 `_vendor/` 같은 공유 위치로 추출 예정.

## 라이선스

MIT

## 에이전트 간 직접 메시지 (실험적)

[ACDC stdio 릴레이](RELAY.md)는 Codex에서 실행 중인 Claude를 찾아 채널 메시지를 전달합니다. Claude의 Codex 위임은 별도 Codex 도구를 사용하며, 역방향 채널이나 remote 런타임은 요구하지 않습니다. ACDC 수신함·DB·대화 기록은 없습니다. MCP 런타임에는 uv/Python 3.11 이상이 필요하며 기존 읽기 전용 스킬과 독립적으로 동작합니다.
