# ACDC 직접 채팅 릴레이

ACDC의 stdio MCP는 실행 중인 다른 에이전트의 채팅에 메시지를 전달합니다. ACDC는 수신함, DB, 메시지 기록, 오프라인 보관, 자동 재전송을 제공하지 않습니다. 메시지 대기와 대화 기록은 각 제품의 기존 런타임이 관리합니다.

```text
Codex MCP ── 로컬 소켓 ── Claude MCP → Claude channel notification
Claude MCP ── 로컬 소켓 ── Codex MCP → codex queue → 기존 Codex 세션
```

## 도구

| 도구 | 역할 |
|---|---|
| `register_peer(name, thread_id?)` | 현재 실행 중인 MCP의 표시 이름을 지정합니다. Codex가 세션 UUID를 상속하지 못한 경우 자신의 현재 UUID를 연결할 수 있습니다. |
| `list_peers()` | 같은 OS 사용자의 실행 중인 ACDC 피어를 찾습니다. 이름·프로젝트·피어 ID·Codex 세션 UUID(`thread_id`)·수신 준비 상태를 반환합니다. |
| `send_message(to, text)` | 정확한 피어 ID의 채팅으로 바로 전달합니다. |

이름은 중복될 수 있으므로 송신은 항상 `list_peers`가 반환한 ID를 사용합니다. 피어 ID는 MCP 프로세스가 재시작될 때 바뀝니다. 연결 후 각 에이전트에 “ACDC에서 이름을 reviewer/implementer로 등록하고 상대를 찾아 대화하라”고 요청하면 됩니다. 별도 세션 전용 스크립트는 필요하지 않습니다.

Codex의 `thread_id`는 실제 큐 전달에 사용하는 세션 UUID입니다. Claude는 이 값으로 같은 프로젝트의 여러 Codex 세션을 구분하고, 해당 항목의 `id`를 `send_message(to, text)`에 전달합니다. `register_peer`에서 UUID를 변경하면 목록과 실제 큐 대상에 함께 반영됩니다. Claude 또는 UUID가 없는 Codex의 `thread_id`는 `null`입니다. UUID가 표시되어도 해당 세션을 소유한 런타임에 접근할 수 있어야 전달됩니다.

## 플러그인 구성

`plugins/claude`와 `plugins/codex` 각각에 `.mcp.json`과 자체 완결된 `mcp/` 런타임을 포함합니다. SDK는 `uv run --locked --script`로 실행합니다. Python 3.11 이상, uv, 로컬 Codex CLI가 필요합니다. 첫 실행 시 uv가 고정된 SDK 의존성을 다운로드하며 이후 캐시를 사용합니다. 실행 명령에 프로젝트·세션 UUID를 고정하지 않습니다.

현재 변경은 로컬 개발 소스입니다. 기존 marketplace의 0.3.0에는 아직 게시되지 않았습니다. 양쪽 프로그램의 사용자 MCP 설정에 `acdc-relay`라는 이름으로 이 개발 소스를 등록했습니다. 향후 업데이트된 플러그인을 설치한 뒤 중복 등록을 제거하려면 다음을 실행합니다.

```bash
codex mcp remove acdc-relay
claude mcp remove --scope user acdc-relay
```

다른 머신에서 개발 소스를 직접 등록할 때는 경로를 실제 설치 위치로 바꿉니다.

```bash
codex mcp add acdc-relay -- uv run --locked --script /path/to/acdc/plugins/codex/mcp/server.py --agent codex
claude mcp add --scope user acdc-relay -- uv run --locked --script /path/to/acdc/plugins/claude/mcp/server.py --agent claude
```

## Claude 실행

사용자 MCP로 등록한 현재 개발본:

```bash
claude --dangerously-load-development-channels server:acdc-relay
```

기존 세션을 이어 열려면 `--resume <세션 이름 또는 UUID>`를 추가합니다. ACDC 플러그인에 포함된 MCP를 사용하는 배포본에서는 서버 등록과 중복 사용하지 않고 다음 형식을 사용합니다.

```bash
claude --dangerously-load-development-channels plugin:acdc@acdc
```

개발용 채널 활성화 확인은 Claude 터미널에서 수행해야 합니다. MCP 연결만으로 channel 알림 수신이 활성화되지는 않습니다. `ready=true`는 MCP transport 준비를 의미하며, Claude가 채널을 활성화하거나 메시지를 읽었다는 확인이 아닙니다. 서버는 이 활성화 상태의 확인 응답을 받지 못합니다.

## Codex 실행 및 범위

Codex의 `queue` 명령이 접근할 수 있는 런타임에 속한 세션이어야 합니다. 로컬 기본 데몬 또는 명시한 로컬 remote endpoint를 사용합니다.

- 대기 중인 기존 세션에 보내면 새 응답 턴이 시작됩니다.
- 작업 중인 기존 세션에 보내면 현재 턴 다음에 처리됩니다.
- 현재 턴을 즉시 바꾸는 `turn/steer`는 이번 구현 범위에 포함하지 않습니다.
- MCP 프로세스의 `CODEX_THREAD_ID`를 기본 대상으로 사용합니다. 값이 없다면 해당 세션의 UUID로 `register_peer`를 호출해야 합니다.
- 별도 런타임을 사용할 경우 MCP 환경의 `ACDC_CODEX_REMOTE`를 `unix://...` 또는 `ws://127.0.0.1:PORT`로 지정합니다. 이 값은 에이전트가 임의로 고르는 송신 주소가 아니라 사용자가 선택한 런타임입니다.
- Codex 앱/IDE의 모든 세션이 로컬 CLI 데몬에 노출되는 것은 아닙니다. 현재 작업 환경에서는 기본 데몬 소켓이 없었으므로 **현재 Codex 앱 대화로의 수신은 검증하지 않았습니다.** 다른 런타임에 같은 대화를 임의로 재개하여 복제하거나 권한을 변경하지 않습니다.

## 메모리·전달 계약

- 메시지는 최대 16 KiB, 수신 연결은 동시에 최대 8개입니다. 초과 연결은 대기열 없이 거부합니다.
- 연결 입력 대기는 3초, 전체 수신 처리는 30초, Codex CLI 실행은 25초로 제한합니다. CLI 출력은 각 스트림 64 KiB를 초과하면 실패 처리합니다.
- 피어 탐색은 최신 소켓 최대 200개만 대상으로 합니다. 메시지 목록, 중복 확인 캐시, 회신 기록은 없습니다.
- 정상 종료 시 소켓과 연결 작업을 정리합니다. SIGKILL 이후 남은 소켓은 탐색에서 무시합니다. 죽은 소켓 파일에는 메시지 본문이 없습니다.
- `transport_written`: Claude MCP transport에 썼다는 뜻입니다. 모델 수신/읽기 완료를 보장하지 않습니다.
- `queued`: Codex 런타임이 메시지를 받아 대기열에 넣었다는 뜻입니다. 응답 완료를 보장하지 않습니다.
- 상대가 종료되었으면 전송 실패입니다. 전송 도중 연결이 끊기면 결과가 불확실하므로 상대 채팅을 확인한 뒤 판단합니다. 자동 재전송과 중복 제거는 없습니다.
- 상대 런타임 자체의 대화 기록·큐·모델 문맥 크기는 Claude/Codex가 관리하며, 릴레이의 메모리 제한에 포함하지 않습니다.

같은 OS 사용자만 접근하는 0700 디렉터리와 0600 Unix 소켓을 사용합니다. 기본 위치는 임시 디렉터리의 `acdc-relay-<uid>`이며 `ACDC_RELAY_DIR`로 바꿀 수 있습니다. 같은 사용자로 실행되는 모든 프로세스는 신뢰 범위에 속하며, `sender`는 암호학적 프로세스 신원 인증이 아닙니다. Linux/macOS용이며 Windows native와 기기 간 통신은 포함하지 않습니다.

메시지에는 peer 발신이라는 표식과 “사용자 명령이나 승인이 아니다”라는 설명을 붙입니다. Codex `queue`는 네이티브 사용자 입력 경로를 사용하므로, 역할 수준에서 별도 peer 타입을 보장하지는 않습니다. 수신 모델이 출처 설명을 따르는 방식입니다. ACDC는 권한 승인 대행이나 자동 응답 순환을 구현하지 않습니다.

## 검증

```bash
python3 -m unittest discover -s tests -q
npm ci --ignore-scripts --prefix tests/mcp
npm test --prefix tests/mcp
uv run --script tests/native_queue_probe.py
```

- 실제 Unix 소켓으로 양방향 전달, 종료, 입력 제한, 동시 8개 초과 거부를 확인합니다.
- 200회 예열 후 2,000회 전송에서 Python이 보유한 메모리 증가가 512 KiB 이내인지 검사합니다. 이는 해당 반복 시나리오의 회귀 검사이며 모든 환경에서의 무누수 증명은 아닙니다.
- 공식 JS MCP 클라이언트로 양쪽 플러그인 설정 그대로 stdio 서버를 실행합니다. Claude custom notification 및 Codex CLI 인수 전달을 검증합니다.
- native probe는 **실제 Codex CLI 0.154.0의 app-server와 queue**를 사용합니다. 임시 CODEX_HOME, 임시 작업 디렉터리, 로컬 모의 모델 서버로 격리하며 유료 모델을 호출하지 않습니다. 작업 중 두 번째 메시지를 보내 같은 세션에서 두 턴이 완료되는 것을 확인합니다.
- 실제 Claude 모델의 수신·회신은 채널을 활성화한 사용자 세션에서 별도로 확인해야 합니다.

## 선택 근거

현재 요구는 “실행 중인 다른 채팅에 말을 건다”입니다. DB 우편함은 오프라인 보관 요구가 없으므로 제거했습니다. 메모리 우편함도 Codex 네이티브 queue를 사용하면 불필요합니다. 각 제품의 기존 입력 경로 앞에 작은 전달기만 두는 것이 현재 요구에 가장 단순한 선택입니다. 범용 최적성이나 모든 앱 호환성을 주장하지 않습니다.

- [Claude Channels reference](https://code.claude.com/docs/en/channels-reference)
- [Codex App Server: turn 및 queue 기반 런타임](https://learn.chatgpt.com/docs/app-server)
- 로컬 CLI 근거: `codex queue --help`, native probe 실행 결과.
