#!/usr/bin/env python3
"""Start the local ACDC-enabled Codex runtime and attach a terminal client."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

ENDPOINT = 'ws://127.0.0.1:4500'
ROOT = Path(__file__).resolve().parents[1]
PROMPT = ('ACDC 연결 모드로 재개했습니다. 현재 CODEX_THREAD_ID를 확인하고 acdc-relay의 '
          'register_peer에 이 세션의 thread_id와 알아보기 쉬운 이름을 등록하세요. '
          '같은 프로젝트의 Claude 피어가 있으면 연결 확인 메시지를 한 번 보내고 '
          '그 피어에 회신을 한 번만 요청하세요. 자동 응답 루프는 만들지 마세요. '
          '이후 기존 사용자 작업 맥락을 이어가세요.')


def server_command():
    config = {
        'mcp_servers.acdc-relay.command': 'uv',
        'mcp_servers.acdc-relay.args': ['run', '--locked', '--script',
                                      str(ROOT / 'plugins/codex/mcp/server.py'), '--agent', 'codex'],
        'mcp_servers.acdc-relay.env.ACDC_CODEX_REMOTE': ENDPOINT,
    }
    command = ['codex', 'app-server', '--listen', ENDPOINT]
    for key, value in config.items():
        command += ['-c', key + '=' + json.dumps(value)]
    return command


def client_command(thread):
    if thread:
        return ['codex', 'resume', '--remote', ENDPOINT, thread, PROMPT]
    return ['codex', '--remote', ENDPOINT, PROMPT]


def healthy():
    try:
        with urllib.request.urlopen('http://127.0.0.1:4500/readyz', timeout=1) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def ensure_runtime():
    state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'acdc'
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = state / 'runtime.json'
    command = server_command()
    with (state / 'startup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if healthy():
            saved = json.loads(marker.read_text()) if marker.exists() else {}
            if saved.get('command') != command:
                raise SystemExit('Port 4500 is already in use by another runtime; refusing to reuse it.')
            try:
                os.kill(saved['pid'], 0)
            except (OSError, KeyError):
                raise SystemExit('Runtime ownership cannot be verified. Inspect port 4500 before restarting.')
            return saved['pid']
        with (state / 'runtime.log').open('w') as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=log, start_new_session=True)
        for _ in range(100):
            if process.poll() is not None:
                raise SystemExit(f'Runtime failed to start. Read {state / "runtime.log"}')
            if healthy():
                marker.write_text(json.dumps({'pid': process.pid, 'command': command}))
                marker.chmod(0o600)
                return process.pid
            time.sleep(.1)
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
        raise SystemExit(f'Runtime startup timed out. Read {state / "runtime.log"}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('thread', nargs='?', help='Existing session UUID or name; omit for a new session')
    parser.add_argument('--start-only', action='store_true', help='Prepare the runtime without opening a terminal client')
    args = parser.parse_args()
    pid = ensure_runtime()
    print(f'ACDC runtime ready: {ENDPOINT} (PID {pid})', flush=True)
    if not args.start_only:
        os.execvp('codex', client_command(args.thread))


if __name__ == '__main__':
    main()
