"""Direct same-user socket relay. Messages belong to the receiving chat, not ACDC."""
import asyncio
import json
import heapq
import os
from pathlib import Path
import re
import stat
import uuid

PEER_ID = re.compile(r'(claude|codex)-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')

class Relay:
    def __init__(self, root, agent, cwd, deliver):
        if agent not in ('claude','codex'): raise ValueError('Unsupported agent')
        self.root, self.agent, self.cwd = Path(root), agent, str(Path(cwd).resolve())
        self.id = f'{agent}-{uuid.uuid4()}'
        self.name = self.id[:14]
        self.path = self.root / f'{self.id}.sock'
        self.deliver = deliver
        self.server = None
        self.connections = set()
        self.ready = False
        self.detail = 'MCP initialization pending'

    async def start(self):
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Relay directory must be owned by this user with mode 0700')
        if len(os.fsencode(self.path)) > 103:
            raise ValueError('Socket path too long; set a shorter ACDC_RELAY_DIR')
        self.server = await asyncio.start_unix_server(self.handle,path=self.path,limit=65536)
        self.path.chmod(0o600)

    def describe(self):
        return {'id':self.id,'agent':self.agent,'name':self.name,'cwd':self.cwd,
                'ready':self.ready,'detail':self.detail}

    async def request(self,peer_id,data,timeout=35):
        if not isinstance(peer_id,str) or not PEER_ID.fullmatch(peer_id):
            raise ValueError('Select an exact peer ID from list_peers')
        writer = None
        try:
            async with asyncio.timeout(timeout):
                path = self.root / f'{peer_id}.sock'
                info = path.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    raise ValueError('Peer endpoint is not an owned socket')
                reader,writer = await asyncio.open_unix_connection(path,limit=65536)
                writer.write(json.dumps(data,ensure_ascii=False).encode()+b'\n')
                await writer.drain()
                result = json.loads(await reader.readline())
                if result.get('error'): raise ValueError(result['error'])
                return result['result']
        except (OSError,TimeoutError,json.JSONDecodeError,KeyError) as error:
            if writer is None: raise ConnectionError('Peer is offline; no message sent') from error
            raise ConnectionError('Delivery outcome unknown; inspect the receiving chat before retrying') from error
        finally:
            if writer:
                writer.close()
                try: await writer.wait_closed()
                except OSError: pass

    async def peers(self):
        async def inspect(path):
            try: return await self.request(path.stem,{'op':'describe'},timeout=1)
            except (ValueError,ConnectionError): return None
        # Bound discovery work. Stale sockets are skipped, never mistaken for live agents.
        def candidates():
            for path in self.root.glob('*.sock'):
                try: yield (path.lstat().st_mtime, path)
                except FileNotFoundError: continue
        paths = [path for _,path in heapq.nlargest(200,candidates())]
        rows = await asyncio.gather(*(inspect(p) for p in paths))
        return [row for row in rows if row is not None]

    async def send(self,to,text):
        if not isinstance(text,str) or not text.strip() or len(text.encode())>16384:
            raise ValueError('text must contain 1–16384 UTF-8 bytes')
        if to == self.id: raise ValueError('Cannot send to yourself')
        return await self.request(to,{'op':'deliver','sender':self.describe(),'text':text})

    async def handle(self,reader,writer):
        task = asyncio.current_task()
        admitted = len(self.connections)<8
        if admitted: self.connections.add(task)
        try:
            async with asyncio.timeout(30):
                if not admitted: raise ValueError('Recipient is busy; no message accepted')
                data = json.loads(await asyncio.wait_for(reader.readline(),3))
                if not isinstance(data,dict): raise ValueError('Expected a JSON object')
                if data == {'op':'describe'}:
                    result = self.describe()
                elif set(data)=={'op','sender','text'} and data['op']=='deliver':
                    sender,text = data['sender'],data['text']
                    if not isinstance(sender,dict) or not isinstance(sender.get('id'),str) or not PEER_ID.fullmatch(sender['id']):
                        raise ValueError('Invalid sender')
                    if not isinstance(text,str) or not text.strip() or len(text.encode())>16384:
                        raise ValueError('Invalid text')
                    result = await self.deliver({'sender':sender,'text':text})
                else: raise ValueError('Unknown operation')
                writer.write(json.dumps({'result':result},ensure_ascii=False).encode()+b'\n')
                await writer.drain()
        except (ValueError,TimeoutError,ConnectionError) as error:
            writer.write(json.dumps({'error':str(error) or 'Delivery timed out; outcome unknown'}).encode()+b'\n')
            try: await writer.drain()
            except OSError: pass
        finally:
            writer.close()
            try: await writer.wait_closed()
            except OSError: pass
            if admitted: self.connections.discard(task)

    async def close(self):
        if self.server:
            self.server.close(); await self.server.wait_closed(); self.server=None
            for task in list(self.connections): task.cancel()
            await asyncio.gather(*list(self.connections),return_exceptions=True)
            self.path.unlink(missing_ok=True)
