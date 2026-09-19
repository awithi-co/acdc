# ACDC: Codex → Claude 메시지 전달

ACDC는 실행 중인 Codex가 Claude 대화에 검토 의견과 맥락을 전달하는 stdio MCP입니다. Claude → Codex 채널 수신은 제공하지 않습니다. Claude가 Codex에 검토·분석을 위임할 때는 별도로 설치한 Codex 도구를 사용합니다. 해당 위임 도구의 연결 및 결과 처리는 ACDC 범위 밖입니다.

```text
Codex ── ACDC MCP / 로컬 소켓 ── Claude channel
Claude ── 별도 Codex 도구 ── 검토·분석 위임 및 결과 수령
```

Codex는 사이드카 검토자로 활용합니다. ACDC 메시지는 에이전트 의견이며 사용자 명령·승인이 아니라는 설명을 포함합니다. 실제 구현을 담당하는 Claude가 최신 맥락과 대조하여 판단합니다. 자동 회신 루프는 만들지 않습니다.

## 도구

| 실행 측 | 도구 | 역할 |
|---|---|---|
| 양쪽 | `register_peer(name)` | 식별하기 쉬운 표시 이름을 지정합니다. |
| Codex | `list_peers()` | 실행 중인 Claude의 이름·프로젝트 경로·피어 ID·수신 준비 상태를 조회합니다. |
| Codex | `send_message(to, text)` | 선택한 Claude의 정확한 피어 ID로 전달합니다. |

이름은 중복될 수 있습니다. 전송에는 `list_peers`의 `id`를 사용합니다. 피어 ID는 프로세스 재시작 시 변경됩니다. 메시지의 `sender_id`는 발신 식별자이며 회신 주소가 아닙니다. Codex는 수신 소켓을 열지 않습니다.

## 실행

양쪽 플러그인은 자체 `.mcp.json`과 `mcp/` 런타임을 포함합니다. Python 3.11 이상과 uv가 필요합니다. SDK 버전은 lock 파일로 고정합니다. 기존 세션 조회 스킬과 독립적으로 동작합니다.

개발 소스를 사용자 MCP로 직접 등록하는 경우:

```bash
codex mcp add acdc-relay -- uv run --locked --script /path/to/acdc/plugins/codex/mcp/server.py --agent codex
claude mcp add --scope user acdc-relay -- uv run --locked --script /path/to/acdc/plugins/claude/mcp/server.py --agent claude
```

Codex는 평소대로 실행합니다. 별도 app-server, `--remote`, 큐 수신 서버, 세션 UUID 바인딩은 필요하지 않습니다.

사용자 MCP로 등록한 개발본의 Claude 실행:

```bash
claude --dangerously-load-development-channels server:acdc-relay
```

기존 대화를 재개하는 경우:

```bash
claude --resume KABA --dangerously-load-development-channels server:acdc-relay
```

개발 채널 경고는 로컬 개발본을 실행한다는 확인입니다. 이 옵션은 신뢰하는 개발 코드에만 사용합니다. PR 병합이 Claude의 승인 채널 등록을 의미하지는 않습니다.

플러그인에 포함된 MCP를 사용하는 경우 개발 채널 지정은 `plugin:acdc@acdc`입니다. 사용자 MCP와 플러그인 MCP를 중복 등록하지 않습니다. 배포본으로 전환한 뒤 중복 사용자 등록은 `codex mcp remove acdc-relay`, `claude mcp remove --scope user acdc-relay`로 제거할 수 있습니다.

## 전달 및 자원 제한

- ACDC 수신함, DB, 메시지 기록, 오프라인 보관, 자동 재전송은 없습니다.
- 메시지는 최대 16 KiB, 수신 연결은 동시에 최대 8개입니다. 초과 연결은 대기열 없이 거부합니다.
- 입력 대기는 3초, 전체 수신 처리는 30초입니다. 피어 탐색은 최신 소켓 최대 200개로 제한합니다.
- 정상 종료 시 Claude 소켓과 연결 작업을 정리합니다. 강제 종료 후 남은 소켓은 탐색에서 무시합니다.
- `ready=true`는 Claude MCP transport 준비 상태입니다. 채널 활성화·모델 읽기 완료를 확인하는 상태가 아닙니다.
- `transport_written`은 Claude MCP transport에 알림을 썼다는 뜻입니다. 모델의 읽기·응답 완료는 별도로 확인합니다.
- 상대가 종료되었으면 전송 실패입니다. 전송 도중 연결이 끊기면 결과가 불확실할 수 있으며 자동 재시도하지 않습니다.

같은 OS 사용자의 0700 디렉터리와 0600 Unix 소켓을 사용합니다. 기본 위치는 임시 디렉터리의 `acdc-relay-<uid>`이며 `ACDC_RELAY_DIR`로 변경할 수 있습니다. 같은 사용자 프로세스는 신뢰 범위에 속하며 발신 메타데이터는 암호학적 신원 인증이 아닙니다. Linux/macOS 로컬 통신을 대상으로 합니다.

## 검증

```bash
python3 -m unittest discover -s tests -q
npm ci --ignore-scripts --prefix tests/mcp
npm test --prefix tests/mcp
```

공식 JS MCP 클라이언트로 실제 플러그인 stdio 설정을 실행하여 Codex → Claude 알림 전달, Claude 전용 수신 목록, 역방향 도구 호출 차단, 종료 후 전송 실패를 검사합니다. 전송 계층 테스트는 입력 제한, 연결 제한 및 2,000회 반복 전송 후 보유 메모리 증가를 검사합니다. 이 회귀 검사가 모든 환경에서의 무누수를 증명하지는 않습니다.

실제 KABA 대화에서 ACDC 채널 메시지 도착 및 Claude의 수신 확인 응답을 확인했습니다. Claude → 기존 Codex 대화 자동 수신은 이번 구현·검증 범위에서 제외했습니다.

[Claude Channels reference](https://code.claude.com/docs/en/channels-reference)
