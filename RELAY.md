# ACDC: Claude와 Codex 대화 연결

ACDC는 stdio MCP와 로컬 소켓으로 실행 중인 에이전트 대화에 맥락을 전달합니다. 기본 Codex 실행은 송신 전용이며, 수신은 명시적으로 연결한 Codex app-server 세션에서만 사용합니다.

```text
Codex → ACDC → Claude channel → Claude 대화
Claude → ACDC → codex queue → 지정한 Codex 대화
```

ACDC에는 수신함, 메시지 DB, 폴링 소비자, 재전송 큐가 없습니다. Codex의 네이티브 queue가 응답 중 도착한 메시지를 처리합니다. MCP 등록만으로 기존 Codex 클라이언트가 자동 수신 모드로 바뀌지는 않습니다.

## 도구

- `register_peer(name, thread_id?)`: 표시 이름과 현재 Codex 세션 UUID를 등록합니다. Claude는 thread_id를 받지 않습니다.
- `list_peers()`: 수신 준비를 선언한 실행 중 피어를 찾습니다. 이름은 중복될 수 있습니다.
- `send_message(to, text)`: 정확한 피어 ID에 전달합니다. 송신 전용 Codex는 회신 주소가 없습니다.

메시지는 에이전트 의견이며 사용자 명령이나 승인이 아닙니다. 전달 문구에 이 구분을 포함합니다. 자동 확인 응답 루프를 만들지 않습니다.

## 실행

기존처럼 Codex에서 Claude로만 보낼 때는 일반 Codex 실행을 유지합니다. Python 3.11 이상과 uv가 필요합니다.

개발 소스를 사용자 MCP로 등록하는 경우:

```bash
codex mcp add acdc-relay -- uv run --locked --script /path/to/acdc/plugins/codex/mcp/server.py --agent codex
claude mcp add --scope user acdc-relay -- uv run --locked --script /path/to/acdc/plugins/claude/mcp/server.py --agent claude
```

Claude는 채널을 활성화해 실행합니다:

```bash
claude --resume KABA --dangerously-load-development-channels server:acdc-relay
```

이 옵션은 신뢰하는 로컬 개발본에만 사용합니다. 플러그인 MCP를 직접 쓰는 경우 채널 지정은 `plugin:acdc@acdc`입니다. 사용자 MCP와 플러그인 MCP를 중복 등록하지 않습니다. 이미 실행 중인 MCP 프로세스는 파일 수정만으로 갱신되지 않으므로 도구 구성이 달라졌다면 다시 연결해야 합니다.

### 양방향 Codex 실행

현재 Codex 대화를 종료한 뒤 해당 프로젝트 디렉터리에서 실행합니다. 같은 세션을 두 런타임에서 동시에 계속하지 않습니다.

```bash
python3 /path/to/acdc/scripts/channel_runtime.py SESSION_UUID
```

UUID를 생략하면 새 대화를 엽니다. 실행기는 localhost:4500의 app-server를 시작하거나 자신이 시작한 서버를 재사용하고 터미널을 연결합니다. 처음 제공하는 프롬프트가 Codex에 현재 세션 등록과 1회 연결 확인을 요청합니다. 다른 프로젝트에서도 같은 실행기를 사용할 수 있습니다.

`--yolo` 같은 권한 옵션을 remote resume에 붙이지 않습니다. 권한은 app-server 설정과 재개하는 세션의 정책을 따릅니다. 실행기는 이를 임의로 완화하지 않습니다.

서버만 준비하려면 `--start-only`를 사용합니다. PID와 로그는 `${XDG_STATE_HOME:-~/.local/state}/acdc/`에 저장됩니다. 재부팅 자동 시작은 설치하지 않습니다. 다른 서버가 4500 포트를 사용하면 덮어쓰지 않고 실패합니다.

직접 구성할 때는 app-server에서 사용하는 Codex MCP 환경에 `ACDC_CODEX_REMOTE=ws://127.0.0.1:4500`을 명시합니다. Codex는 `register_peer`에 자신의 현재 세션 UUID를 등록해야 합니다. `CODEX_THREAD_ID`가 MCP 환경에 제공되는 경우 자동으로 읽습니다. 실행기는 이 endpoint 설정을 app-server에만 적용하며 전역 Codex 설정은 변경하지 않습니다.

## 전달 및 자원 제한

- ACDC 수신함, DB, 메시지 기록, 오프라인 보관, 자동 재전송은 없습니다.
- 메시지는 최대 16 KiB, 수신 연결은 동시에 최대 8개입니다. 초과 연결은 대기열 없이 거부합니다.
- 입력 대기는 3초, 전체 수신 처리는 30초입니다. 피어 탐색은 최신 소켓 최대 200개로 제한합니다.
- 정상 종료 시 Claude 소켓과 연결 작업을 정리합니다. 강제 종료 후 남은 소켓은 탐색에서 무시합니다.
- `ready=true`는 Claude의 MCP transport 초기화 또는 Codex의 endpoint·세션 ID 설정 상태입니다. 실제 연결·읽기 완료 보장이 아니며 끊어진 런타임의 전달은 실패합니다.
- `transport_written`은 Claude transport 기록, `queued`는 Codex queue 수락을 뜻합니다. 모델의 읽기·응답 완료는 별도로 확인합니다.
- 상대가 종료되었으면 전송 실패입니다. 전송 도중 연결이 끊기면 결과가 불확실할 수 있으며 자동 재시도하지 않습니다.

같은 OS 사용자의 0700 디렉터리와 0600 Unix 소켓을 사용합니다. 기본 위치는 임시 디렉터리의 `acdc-relay-<uid>`이며 `ACDC_RELAY_DIR`로 변경할 수 있습니다. 같은 사용자 프로세스는 신뢰 범위에 속하며 발신 메타데이터는 암호학적 신원 인증이 아닙니다. Linux/macOS 로컬 통신을 대상으로 합니다.

## 검증

```bash
python3 -m unittest discover -s tests -q
npm ci --ignore-scripts --prefix tests/mcp
npm test --prefix tests/mcp
```

공식 JS MCP 클라이언트로 양방향 도구 계약, 송신 전용 기본값, 종료 후 전달 실패를 검사합니다. 실제 Codex의 대기 중·응답 중 queue 동작은 별도 로컬 모델 프로브로 확인합니다:

```bash
uv run --script tests/native_queue_probe.py
```

이 프로브는 임시 CODEX_HOME과 로컬 모의 모델을 사용하며 유료 모델 API를 호출하지 않습니다. 메모리 회귀 검사는 2,000회 소켓 전달 후 보유 할당 증가를 확인하며 모든 환경의 무누수를 보증하지 않습니다.

[Claude Channels reference](https://code.claude.com/docs/en/channels-reference) · [Codex App Server](https://learn.chatgpt.com/docs/app-server)

2026-09-19 로컬 검증에서 실행 중인 KABA Claude가 격리된 실제 Codex 세션에 `ACDC_LIVE_PING`을 전송하였고, 해당 세션의 `agentMessage`에서 `ACDC_LIVE_ACK`와 정상 턴 완료를 확인했습니다. 기존 사용자 세션의 전환은 별도로 실행해야 합니다.
