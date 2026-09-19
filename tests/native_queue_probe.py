# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets==15.0.1"]
# ///
"""Opt-in native Codex queue probe; temporary CODEX_HOME and local model only."""
import asyncio,json,tempfile,pathlib,subprocess,threading,http.server,time,os
import websockets,socket
requests=[]
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length']))); requests.append(body)
  if len(requests)==1:time.sleep(4)
  item={'id':'msg_probe','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'ACDC_PROBE_ACK','annotations':[]}]}
  response={'id':'resp_probe','object':'response','created_at':int(time.time()),'status':'completed','model':'acdc-probe','output':[item],'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}
  events=[{'type':'response.created','response':{**response,'status':'in_progress','output':[]}},
   {'type':'response.output_item.added','output_index':0,'item':{**item,'status':'in_progress','content':[]}},
   {'type':'response.content_part.added','item_id':'msg_probe','output_index':0,'content_index':0,'part':{'type':'output_text','text':'','annotations':[]}},
   {'type':'response.output_text.delta','item_id':'msg_probe','output_index':0,'content_index':0,'delta':'ACDC_PROBE_ACK'},
   {'type':'response.output_item.done','output_index':0,'item':item}, {'type':'response.completed','response':response}]
  data=''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
  self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
async def main():
 with tempfile.TemporaryDirectory(prefix='acdc-probe-') as tmp:
  root=pathlib.Path(tmp)
  with socket.socket() as free:
   free.bind(('127.0.0.1',0)); port=free.getsockname()[1]
  endpoint=f'ws://127.0.0.1:{port}'
  model_server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=model_server.serve_forever,daemon=True).start()
  provider='{name="ACDC local probe",base_url="http://127.0.0.1:'+str(model_server.server_port)+'/v1",wire_api="responses",request_max_retries=0,stream_max_retries=0}'
  log=open(root/'stderr','w+')
  cmd=['codex','app-server','--listen',endpoint,'-c','model_provider="acdc_probe"','-c','model="acdc-probe"','-c','model_providers.acdc_probe='+provider]
  env={**os.environ,'CODEX_HOME':str(root/'codex-home')}
  (root/'codex-home').mkdir()
  proc=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=log,env=env)
  try:
   await asyncio.sleep(.7)
   async with websockets.connect(endpoint,max_size=8*1024*1024) as ws:
    seq=0
    async def rpc(method,params):
     nonlocal seq; seq+=1;n=seq
     await ws.send(json.dumps({'id':n,'method':method,'params':params}))
     while True:
      event=json.loads(await asyncio.wait_for(ws.recv(),10))
      if event.get('id')==n:
       if 'error' in event:raise RuntimeError(event['error'])
       return event['result']
    await rpc('initialize',{'clientInfo':{'name':'acdc_probe','version':'1'},'capabilities':{'experimentalApi':True}})
    await ws.send(json.dumps({'method':'initialized'}))
    thread=await rpc('thread/start',{'cwd':tmp,'ephemeral':False,'model':'acdc-probe','modelProvider':'acdc_probe','approvalPolicy':'never','sandbox':'read-only'})
    tid=thread['thread']['id']
    queue=await asyncio.create_subprocess_exec('codex','queue','--remote',endpoint,'--thread',tid,'--message','ACDC_PROBE_PING: reply ACDC_PROBE_ACK only. Do not use tools.',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env)
    stdout,stderr=await asyncio.wait_for(queue.communicate(),20)
    print('queue_exit',queue.returncode,flush=True)
    if queue.returncode:raise RuntimeError('Queue rejected test message')
    for _ in range(100):
     if requests:break
     await asyncio.sleep(.02)
    second=await asyncio.create_subprocess_exec('codex','queue','--remote',endpoint,'--thread',tid,'--message','ACDC_BUSY_PING: this is the second message sent during the first response. Reply ACDC_PROBE_ACK only.',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env)
    second_out,second_err=await asyncio.wait_for(second.communicate(),20)
    assert len(requests)==1 and second.returncode==0
    print('queued_while_first_running',True,'second_queue_exit',second.returncode,flush=True)
    completed=0
    for _ in range(200):
     event=json.loads(await asyncio.wait_for(ws.recv(),20))
     if event.get('method')=='turn/completed':
      completed+=1; print('turn_status',event['params']['turn']['status'],flush=True)
      if completed==2:break
    assert completed==2 and len(requests)==2 and 'ACDC_BUSY_PING' in json.dumps(requests[1])
    print('local_model_requests',len(requests),'marker_received',any('ACDC_PROBE_PING' in json.dumps(r) for r in requests),'turns_completed',completed,'second_marker_received',any('ACDC_BUSY_PING' in json.dumps(r) for r in requests))
  finally:
   proc.terminate()
   try:proc.wait(timeout=5)
   except subprocess.TimeoutExpired:proc.kill();proc.wait()
   model_server.shutdown();log.close()
asyncio.run(main())
