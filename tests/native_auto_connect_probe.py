"""Isolated interactive CLI probes using local fake models; no paid model calls.

Run with python3 tests/native_auto_connect_probe.py claude|codex.
Temporary homes/settings isolate the user's open sessions. The probe exercises
the real CLIs, not a mocked queue or hook runner.
"""
import argparse
from contextlib import nullcontext
import fcntl
import http.server
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time


class ModelServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"data":[]}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if 'count_tokens' in self.path:
            data = b'{"input_tokens":100}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.server.requests.append(body)
        n = len(self.server.requests)
        # Leave time to inject a follow-up while a turn is running.
        if n == 2:
            time.sleep(2)
        text = f'ACDC_LOCAL_ACK_{n}'
        tool_call = None
        if getattr(self.server,'paired',False):
            history=json.dumps(body.get('messages',body.get('input',[])))
            rules = [('PAIR_FOLLOWUP','PAIR_DONE'),('PAIR_QUESTION','PAIR_REVIEW_REPLY')] if self.server.agent=='claude' else [('PAIR_REVIEW_REPLY','PAIR_FOLLOWUP')]
            for marker,reply in rules:
                if marker in history and marker not in self.server.sent:
                    for tool in body.get('tools',[]):
                        if tool.get('type')=='namespace' and ('acdc' in tool.get('name','') or tool.get('name')=='mcp__relay'):
                            for member in tool.get('tools',[]):
                                if member.get('name')=='send_message':
                                    tool_call={'name':'send_message','namespace':tool['name'],'arguments':json.dumps({'text':reply})}
                        elif 'acdc' in tool.get('name','') and tool['name'].endswith('send_message'):
                            tool_call={'name':tool['name'],'arguments':json.dumps({'text':reply})}
                    if not tool_call and any(t.get('name')=='ToolSearch' for t in body.get('tools',[])):
                        tool_call={'name':'ToolSearch','arguments':json.dumps({'query':'+acdc send_message','max_results':3})}
                    elif tool_call:
                        self.server.sent.add(marker)
                    break
        if self.server.agent == 'claude':
            message = {'id':f'msg_{n}', 'type':'message', 'role':'assistant',
                       'model':body.get('model', 'claude-sonnet-4-6'),
                       'content':[], 'stop_reason':None, 'stop_sequence':None,
                       'usage':{'input_tokens':100, 'output_tokens':1}}
            events = [
                {'type':'message_start', 'message':message},
                {'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}},
                {'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':text}},
                {'type':'content_block_stop','index':0},
                {'type':'message_delta','delta':{'stop_reason':'end_turn','stop_sequence':None},'usage':{'output_tokens':1}},
                {'type':'message_stop'},
            ]
            if tool_call:
                events=[{'type':'message_start','message':message},
                        {'type':'content_block_start','index':0,'content_block':{'type':'tool_use','id':f'call_{n}','name':tool_call['name'],'input':{}}},
                        {'type':'content_block_delta','index':0,'delta':{'type':'input_json_delta','partial_json':tool_call['arguments']}},
                        {'type':'content_block_stop','index':0},
                        {'type':'message_delta','delta':{'stop_reason':'tool_use','stop_sequence':None},'usage':{'output_tokens':1}},
                        {'type':'message_stop'}]
        else:
            item = {'id':f'msg_{n}','type':'message','role':'assistant','status':'completed',
                    'content':[{'type':'output_text','text':text,'annotations':[]}]}
            response = {'id':f'resp_{n}','object':'response','created_at':int(time.time()),
                        'status':'completed','model':'acdc-probe','output':[item],
                        'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}
            events = [
                {'type':'response.created','response':{**response,'status':'in_progress','output':[]}},
                {'type':'response.output_item.added','output_index':0,'item':{**item,'status':'in_progress','content':[]}},
                {'type':'response.content_part.added','item_id':item['id'],'output_index':0,'content_index':0,'part':{'type':'output_text','text':'','annotations':[]}},
                {'type':'response.output_text.delta','item_id':item['id'],'output_index':0,'content_index':0,'delta':text},
                {'type':'response.output_item.done','output_index':0,'item':item},
                {'type':'response.completed','response':response},
            ]
            if tool_call:
                item={'type':'function_call','id':f'fc_{n}','call_id':f'call_{n}','status':'completed',**tool_call}
                response['output']=[item]
                events=[{'type':'response.created','response':{**response,'status':'in_progress','output':[]}},
                        {'type':'response.output_item.added','output_index':0,'item':{**item,'status':'in_progress','arguments':''}},
                        {'type':'response.function_call_arguments.delta','item_id':item['id'],'output_index':0,'delta':item['arguments']},
                        {'type':'response.output_item.done','output_index':0,'item':item},
                        {'type':'response.completed','response':response}]
        data = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
        self.send_response(200)
        self.send_header('Content-Type','text/event-stream')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Terminal:
    def __init__(self, command, env, cwd):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH',40,140,0,0))
        self.process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                                        env=env, cwd=cwd, start_new_session=True)
        os.close(slave)
        self.output = b''

    def pump(self):
        if self.master is None:return
        if select.select([self.master],[],[],.05)[0]:
            try:
                chunk = os.read(self.master,65536)
                self.output += chunk
                # Reply to cursor-position queries used during terminal setup.
                if b'\x1b[6n' in chunk:
                    os.write(self.master,b'\x1b[1;1R')
            except OSError:
                pass

    def wait(self, predicate, timeout=45):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            self.pump()
            if predicate():
                return
            if self.process.poll() is not None:
                break
        raise AssertionError(self.output.decode(errors='replace')[-12000:])

    def close(self):
        if self.master is None:return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait()
        os.close(self.master)
        self.master=None


def probe(agent, automatic=False, paired=False, blank=False, check_multi=False, reload_plugin=False):
    workspace = nullcontext(tempfile.mkdtemp(prefix=f'acdc-native-{agent}-')) if os.environ.get('ACDC_KEEP_PROBE') else tempfile.TemporaryDirectory(prefix=f'acdc-native-{agent}-')
    with workspace as tmp:
        if os.environ.get('ACDC_KEEP_PROBE'):print('probe_artifacts',tmp,flush=True)
        root = Path(tmp)
        home = root/'home'
        home.mkdir()
        server = ModelServer(('127.0.0.1',0), Handler)
        server.agent, server.requests = agent, []
        server.paired,server.sent=paired,set()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        isolated_home=root/'user-home'
        isolated_home.mkdir()
        env = {key:os.environ[key] for key in ('PATH','LANG','LC_ALL','SHELL','USER','LOGNAME','SSL_CERT_FILE') if key in os.environ}
        env.update(HOME=str(isolated_home),XDG_CONFIG_HOME=str(root/'config'),
                   XDG_STATE_HOME=str(root/'state'),XDG_CACHE_HOME=str(root/'cache'),
                   UV_CACHE_DIR=str(Path.home()/'.cache/uv'),
                   TERM='xterm-256color',NO_COLOR='1',NO_PROXY='127.0.0.1,localhost,::1')
        if automatic:
            env['ACDC_RELAY_DIR']=os.environ.get('ACDC_PROBE_RELAY',str(root/'relay'))
        env.pop('CLAUDECODE',None)
        if agent == 'claude':
            env.update(CLAUDE_CONFIG_DIR=str(home), ANTHROPIC_BASE_URL=base,
                       ANTHROPIC_API_KEY='local-probe-not-a-key',
                       CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
            env.pop('ANTHROPIC_AUTH_TOKEN',None)
            (home/'.claude.json').write_text(json.dumps({'hasCompletedOnboarding':True,
                'theme':'dark','bypassPermissionsModeAccepted':True,
                'projects':{tmp:{'hasTrustDialogAccepted':True}}}))
            hook = root/'wake.py'
            hook.write_text('import pathlib,time,sys,json\n'
                            'data=json.load(sys.stdin)\n'
                            f'pathlib.Path({str(root/"session.json")!r}).write_text(json.dumps(data))\n'
                            f'p=pathlib.Path({str(root/"wake")!r})\n'
                            'deadline=time.monotonic()+45\n'
                            'while not p.exists() and time.monotonic()<deadline: time.sleep(.05)\n'
                            'if p.exists():\n p.unlink()\n print("ACDC_HOOK_PING",file=sys.stderr)\n sys.exit(2)\n')
            settings = {'hooks':{event:[{'hooks':[{'type':'command','command':f'{sys.executable} {hook}',
                         'asyncRewake':True,'timeout':50}]}] for event in ('SessionStart','Stop')}}
            (root/'settings.json').write_text(json.dumps(settings))
            command = ['claude','--settings',str(root/'settings.json'),'--model','sonnet',
                       '--dangerously-skip-permissions','Reply READY only. Do not use tools.']
            if automatic and not reload_plugin:
                command = ['claude','--plugin-dir',str(Path(__file__).resolve().parents[1]/'plugins/claude'),
                           '--model','sonnet','--dangerously-skip-permissions',
                           'Reply READY only. Do not use tools.']
        else:
            env['CODEX_HOME'] = str(home)
            provider = f'{{name="Local probe",base_url="{base}/v1",wire_api="responses",request_max_retries=0,stream_max_retries=0}}'
            config = 'model="acdc-probe"\nmodel_provider="acdc_probe"\nmodel_providers.acdc_probe='+provider+'\n'
            config += '[projects.'+json.dumps(tmp)+']\ntrust_level="trusted"\n'
            (home/'config.toml').write_text(config)
            command = ['codex','-C',tmp,'-a','never','-s','read-only','--no-alt-screen',
                       'Reply READY only. Do not use tools.']
            if automatic:
                repo = Path(__file__).resolve().parents[1]
                for install in (['codex','plugin','marketplace','add',str(repo),'--json'],
                                ['codex','plugin','add','acdc@acdc','--json']):
                    installed = subprocess.run(install,env=env,cwd=tmp,capture_output=True,text=True,timeout=30)
                    assert installed.returncode==0, installed.stderr+installed.stdout
                with (home/'config.toml').open('a') as trusted:
                    trusted.write('\n[plugins."acdc@acdc".mcp_servers.relay]\ndefault_tools_approval_mode="approve"\n')
        if blank:
            command=[part for part in command if part!='Reply READY only. Do not use tools.']
        terminal = Terminal(command,env,tmp)
        try:
            # Only accept onboarding/trust dialogs inside this disposable home.
            handled = set()
            for _ in range(12):
                for _ in range(20):
                    terminal.pump()
                if server.requests:
                    break
                if blank and automatic and any(Path(env['ACDC_RELAY_DIR']).glob(f'{agent}-*.json')):
                    break
                output = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',terminal.output.decode(errors='replace'))
                compact = ''.join(output.split())
                if 'DetectedacustomAPIkey' in compact and 'api-key' not in handled:
                    handled.add('api-key')
                    os.write(terminal.master,b'\x1b[A\r')
                elif 'Yes,Itrustthisfolder' in compact and 'trust' not in handled:
                    handled.add('trust')
                    os.write(terminal.master,b'\x1b[B\r')
                elif 'Yes,Iaccept' in compact and 'bypass' not in handled:
                    handled.add('bypass')
                    os.write(terminal.master,b'\x1b[B\r')
                elif 'Choose' in output and 'theme' not in handled:
                    handled.add('theme')
                    os.write(terminal.master,b'\r')
            if blank:
                assert not server.requests,'Opening/discovery must not call the model'
                print(agent,'opened_without_prompt_no_model_calls',flush=True)
            else:
                terminal.wait(lambda: b'ACDC_LOCAL_ACK_1' in terminal.output)
                print(agent, 'initial_turn_ok', flush=True)
            if reload_plugin:
                assert agent=='claude' and automatic and not blank
                assert not list(Path(env['ACDC_RELAY_DIR']).glob('claude-*.json')),'Plugin was already registered before reload'
                repo=Path(__file__).resolve().parents[1]
                for install in (['claude','plugin','marketplace','add',str(repo)],
                                ['claude','plugin','install','acdc@acdc','--scope','user']):
                    result=subprocess.run(install,env=env,cwd=tmp,capture_output=True,text=True,timeout=40)
                    assert result.returncode==0,result.stdout[-1000:]+result.stderr[-1000:]
                os.write(terminal.master,b'/reload-plugins\r')
                for _ in range(80):terminal.pump()
                os.write(terminal.master,b'Reply READY only. Do not use tools.\r')
                terminal.wait(lambda: len(server.requests)>=2)
                print(agent,'plugin_enabled_mid_session_reload_ok',flush=True)
            if automatic:
                sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/claude/mcp'))
                from session import SessionRelay
                from relay import Relay
                async def exchange():
                    sockets=Path(env['ACDC_RELAY_DIR'])
                    client=Relay(sockets,'codex',tmp,None)
                    target=None
                    for _ in range(600):
                        peers=await client.peers()
                        target=next((p for p in peers if p.get('automatic') and p.get('ready') and p['agent']==agent),None)
                        if target:break
                        await asyncio.sleep(.05)
                    assert target, 'Plugin did not automatically register a session'
                    if agent=='codex':
                        # Exercise the native rename -> discovery -> relay listing path
                        # before selecting a counterpart, inside the disposable home.
                        from websockets.asyncio.client import unix_connect
                        status=json.loads(subprocess.run(['codex','app-server','daemon','version'],
                            env=env,cwd=tmp,capture_output=True,text=True,check=True,timeout=10).stdout)
                        async with unix_connect(status['socketPath'],open_timeout=5) as ws:
                            sequence=0
                            async def native_rpc(method,params):
                                nonlocal sequence
                                sequence+=1
                                await ws.send(json.dumps({'id':sequence,'method':method,'params':params}))
                                async with asyncio.timeout(10):
                                    while True:
                                        response=json.loads(await ws.recv())
                                        if response.get('id')==sequence:
                                            assert 'error' not in response,response
                                            return response.get('result')
                            await native_rpc('initialize',{'clientInfo':{'name':'acdc_title_probe','version':'1'},
                                'capabilities':{'experimentalApi':True}})
                            await ws.send('{"method":"initialized"}')
                            for title in ('session-test','session-test-renamed'):
                                await native_rpc('thread/name/set',{'threadId':target['thread_id'],'name':title})
                                for _ in range(200):
                                    terminal.pump()
                                    current=await client.request(target['id'],{'op':'describe'})
                                    if current.get('name')==title and current.get('native_name')==title:break
                                    await asyncio.sleep(.05)
                                else:raise AssertionError('Native title did not reach ACDC: '+title)
                        print(agent,'native_title_discovery_ok rename_refresh_same_peer_ok',flush=True)
                    if paired:
                        counterpart=None
                        for _ in range(600):
                            terminal.pump()
                            peers=await client.peers()
                            counterpart=next((p for p in peers if p.get('automatic') and p.get('ready') and p['agent']!=agent),None)
                            if counterpart:break
                            await asyncio.sleep(.05)
                        assert counterpart, 'Other native CLI was not discovered'
                        print(agent,'selected_pair',target['id'],counterpart['id'],flush=True)
                        if agent=='codex':
                            await client.request(target['id'],{'op':'connect','to':counterpart['id']})
                            await client.request(target['id'],{'op':'send','to':None,'text':'PAIR_QUESTION: review this test design and reply using send_message.'})
                        expected='PAIR_FOLLOWUP' if agent=='claude' else 'PAIR_DONE'
                        deadline=time.monotonic()+60
                        while time.monotonic()<deadline:
                            terminal.pump()
                            if any(expected in json.dumps(r.get('messages',r.get('input'))) for r in server.requests):
                                break
                            await asyncio.sleep(.03)
                        else:
                            raise AssertionError('Native pair failed; model tools: '+str([t.get('name') for t in server.requests[-1].get('tools',[])])+'\n'+terminal.output.decode(errors='replace')[-8000:])
                        if agent=='codex' and check_multi:
                            second=Terminal([p for p in command if p!='Reply READY only. Do not use tools.'],env,tmp)
                            try:
                                second_id=None
                                for _ in range(600):
                                    second.pump()
                                    peers=await client.peers()
                                    second_id=next((p['id'] for p in peers if p.get('automatic') and p['agent']=='codex' and p['id']!=target['id']),None)
                                    if second_id:break
                                    await asyncio.sleep(.03)
                                assert second_id,'Second native Codex session was not discovered'
                                terminal.close()
                                for _ in range(60):
                                    second.pump()
                                    await asyncio.sleep(.03)
                                assert any(p['id']==second_id for p in await client.peers()),'Closing one CLI invalidated another session identity'
                                print(agent,'two_native_sessions_ok counterpart_close_preserves_identity_ok',flush=True)
                            finally:
                                second.close()
                        (sockets/f'finished-{agent}').touch()
                        for _ in range(600):
                            terminal.pump()
                            if (sockets/f'finished-{counterpart["agent"]}').exists():break
                            await asyncio.sleep(.05)
                        await client.request(target['id'],{'op':'disconnect'})
                        print(agent,'native_mcp_reply_ok followup_ok',flush=True)
                        return
                    sender=SessionRelay(sockets,'claude',tmp,'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',os.getpid())
                    await sender.start()
                    try:
                        await sender.connect(target['id'])
                        for n in (2,3):
                            await sender.send(target['id'],f'ACDC_AUTO_PING_{n}')
                            # Pump the interactive CLI while awaiting model receipt.
                            deadline=time.monotonic()+45
                            while time.monotonic()<deadline:
                                terminal.pump()
                                if any(f'ACDC_AUTO_PING_{n}' in json.dumps(r.get('messages',r.get('input'))) for r in server.requests):break
                                await asyncio.sleep(.02)
                            else:raise AssertionError(terminal.output.decode(errors='replace')[-8000:])
                        await sender.disconnect()
                    finally:
                        await sender.close()
                import asyncio
                asyncio.run(exchange())
                print(agent,'plugin_auto_registration_ok target_selection_ok repeated_delivery_ok',flush=True)
            elif agent == 'claude':
                (root/'wake').touch()
                terminal.wait(lambda: len(server.requests)>=2)
                # Send the second wake during the delayed second model response.
                (root/'wake').touch()
                terminal.wait(lambda: len(server.requests)>=3)
                terminal.wait(lambda: b'ACDC_LOCAL_ACK_3' in terminal.output)
                assert 'ACDC_HOOK_PING' in json.dumps(server.requests[1])
                assert 'ACDC_HOOK_PING' in json.dumps(server.requests[2])
                print(agent,'idle_wake_ok consecutive_wake_ok',flush=True)
            else:
                files = list(home.glob('sessions/**/rollout-*.jsonl'))
                assert files, 'No isolated Codex transcript'
                tid = json.loads(files[0].read_text().splitlines()[0])['payload']['id']
                for n in (2,3):
                    result = subprocess.run(['codex','queue','--thread',tid,'--message',f'ACDC_QUEUE_PING_{n}'],
                                            env=env,capture_output=True,text=True,timeout=15)
                    assert result.returncode==0, result.stderr
                    terminal.wait(lambda: any(f'ACDC_QUEUE_PING_{n}' in json.dumps(r.get('input')) for r in server.requests))
                terminal.wait(lambda: f'ACDC_LOCAL_ACK_{len(server.requests)}'.encode() in terminal.output)
                assert any('ACDC_QUEUE_PING_2' in json.dumps(r.get('input')) for r in server.requests)
                assert any('ACDC_QUEUE_PING_3' in json.dumps(r.get('input')) for r in server.requests)
                print(agent,'local_queue_idle_ok busy_queue_ok',flush=True)
        finally:
            terminal.close()
            if agent == 'codex':
                subprocess.run(['codex','app-server','daemon','stop'],env=env,capture_output=True,timeout=10)
            # Let owned brokers observe CLI/daemon death before deleting homes.
            for _ in range(40):
                if not any(Path(env.get('ACDC_RELAY_DIR',str(root/'relay'))).glob('*.sock')):break
                time.sleep(.05)
            # The CLI's package updater outlives `daemon stop`; it must not be
            # left running for an already deleted disposable CODEX_HOME.
            if agent=='codex' and Path('/proc').is_dir():
                for proc in Path('/proc').glob('[0-9]*'):
                    try:
                        args=(proc/'cmdline').read_bytes().split(b'\0')
                        if args and args[0].startswith(str(home).encode()+b'/') and b'pid-update-loop' in args:
                            os.kill(int(proc.name),signal.SIGTERM)
                    except (OSError,ValueError):pass
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('agent',choices=['claude','codex','pair'])
    parser.add_argument('--automatic',action='store_true',help='Exercise the actual installed plugin hooks')
    parser.add_argument('--blank',action='store_true',help='Open without even an initial user prompt')
    parser.add_argument('--check-multi',action='store_true',help='Verify a second Codex session survives closing the first')
    parser.add_argument('--paired',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--reload-plugin',action='store_true',help='Enable Claude plugin mid-session and exercise /reload-plugins')
    args=parser.parse_args()
    if args.agent=='pair':
        with tempfile.TemporaryDirectory(prefix='acdc-pair-') as shared:
            children=[subprocess.Popen([sys.executable,__file__,agent,'--automatic','--paired']+(['--blank'] if args.blank else [])+(['--check-multi'] if args.check_multi else []),
                       env={**os.environ,'ACDC_PROBE_RELAY':shared},text=True)
                      for agent in ('claude','codex')]
            failures=[]
            for child in children:
                child.wait(timeout=120)
                failures.append(child.returncode)
            assert failures==[0,0],failures
    else:
        probe(args.agent,args.automatic,args.paired,args.blank,args.check_multi,args.reload_plugin)
