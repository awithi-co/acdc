import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,mkdirSync,writeFileSync,readFileSync,readdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {spawn} from 'node:child_process';
import {createConnection} from 'node:net';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StdioClientTransport} from '@modelcontextprotocol/sdk/client/stdio.js';

const repo=resolve('../..');
// Stand in for the owning CLI ancestor while exercising the real MCP process.
process.title='codex';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function request(root,id,data){return new Promise((resolve,reject)=>{
 const socket=createConnection(join(root,id+'.sock'));
 let input='';socket.on('connect',()=>socket.write(JSON.stringify(data)+'\n'));
 socket.on('data',data=>{input+=data;if(input.includes('\n')){
  socket.end();const row=JSON.parse(input);row.error?reject(new Error(row.error)):resolve(row.result);
 }});socket.on('error',reject);socket.setTimeout(5000,()=>socket.destroy(new Error('request timed out')));
});}

test('automatic MCP identity binds selected session and follow-ups need no UUID or target',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'acdc-auto-'));
 const root=join(directory,'s');mkdirSync(root,{mode:0o700});
 const bin=join(directory,'bin');mkdirSync(bin);
 const capture=join(directory,'queue.json');
 writeFileSync(join(bin,'codex'),'#!/usr/bin/env python3\nimport sys,json\nopen('+JSON.stringify(capture)+',"w").write(json.dumps(sys.argv[1:]))\n',{mode:0o700});
 const env={...process.env,CODEX_HOME:join(directory,'codex-home'),ACDC_RELAY_DIR:root,PATH:bin+':'+process.env.PATH};
 const ids=['11111111-1111-1111-1111-111111111111','22222222-2222-2222-2222-222222222222'];
 const transcripts=join(env.CODEX_HOME,'sessions','2026','10','31');mkdirSync(transcripts,{recursive:true});
 const input='Connect to the Claude session in this test and discuss the design. No file edits.';
 writeFileSync(join(transcripts,`rollout-date-${ids[0]}.jsonl`),[
  {type:'session_meta',payload:{id:ids[0]}},
  {type:'event_msg',payload:{type:'task_started',turn_id:'turn-1',turn_attribution:{turn_id:'turn-1',turn_trigger:'user'}}},
  {type:'event_msg',payload:{type:'item_completed',thread_id:ids[0],turn_id:'turn-1',item:{type:'UserMessage',id:'human-input',content:[{type:'text',text:input}]}}}
 ].map(r=>JSON.stringify(r)).join('\n')+'\n');
 const brokers=[];let client;
 try{
  for(const [i,agent] of ['codex','claude'].entries()){
   brokers.push(spawn('python3',[join(repo,'plugins',agent,'mcp/session.py'),'broker','--agent',agent,
    '--data',JSON.stringify({session_id:ids[i],owner_pid:process.pid,cwd:directory})],{env,stdio:'ignore'}));
  }
  let records=[];
  for(let n=0;n<100;n++){
   records=readdirSync(root).filter(p=>/^(codex|claude)-.*\.json$/.test(p)).map(p=>JSON.parse(readFileSync(join(root,p),'utf8')));
   if(records.length===2)break;await sleep(30);
  }
  assert.equal(records.length,2,'both session hooks registered live brokers');
  const local=records.find(p=>p.agent==='codex'),other=records.find(p=>p.agent==='claude');
  const firstWatch=request(root,other.id,{op:'watch'});
  client=new Client({name:'auto-contract',version:'1'});
  const transport=new StdioClientTransport({command:'uv',args:['run','--locked','--script',join(repo,'plugins/codex/mcp/server.py'),'--agent','codex'],
   env:{...env,CODEX_THREAD_ID:ids[0]},stderr:'pipe'});
  await client.connect(transport,{timeout:15000});
  const call=async(name,args={},meta)=>{
   const result=await client.callTool({name,arguments:args,...(meta?{_meta:meta}:{})});
   if(result.isError)throw new Error(result.content[0].text);
   return JSON.parse(result.content[0].text);
  };
  assert.equal((await call('connection_status')).self.id,local.id);
  const peers=await call('list_peers');assert.deepEqual(peers.map(p=>p.id),[other.id]);
  await assert.rejects(call('send_message',{to:other.id,text:'before selection'}),/connect/);
  await call('connect_peer',{to:other.id});
  assert.equal((await call('connection_status')).connected,true);
  const evidence=(await call('connection_status')).user_request_evidence;
  assert.equal(evidence.status,'verified_user_input');
  assert.equal(evidence.requests[0].text,input);
  assert.equal(evidence.source_peer_id,local.id);
  assert.equal((await call('send_message',{text:'Review the design.'})).status,'accepted');
  const received=await firstWatch;
  assert.equal(received[0].sender.id,local.id);assert.equal(received[0].text,'Review the design.');
  await request(root,other.id,{op:'send',to:null,text:'I found a missing boundary.'});
  const argv=JSON.parse(readFileSync(capture,'utf8'));
  assert.equal(argv[argv.indexOf('--thread')+1],ids[0]);assert.ok(!argv.includes('--remote'));
  assert.match(argv[argv.indexOf('--message')+1],/not a user instruction/);
  await assert.rejects(call('register_peer',{name:'wrong',thread_id:ids[1]}),/cannot be rebound/);
  const third='33333333-3333-3333-3333-333333333333';
  brokers.push(spawn('python3',[join(repo,'plugins/codex/mcp/session.py'),'broker','--agent','codex',
   '--data',JSON.stringify({session_id:third,owner_pid:process.pid,cwd:directory})],{env,stdio:'ignore'}));
  let thirdPeer;
  for(let n=0;n<100;n++){
   try{thirdPeer=JSON.parse(readFileSync(join(root,`codex-${third}-${process.pid}.json`),'utf8'));break;}catch{}await sleep(20);
  }
  assert.ok(thirdPeer,'second Codex execution registered under same runtime');
  await request(root,thirdPeer.id,{op:'thread_metadata',thread:{id:third,name:'session-test',preview:'hi'}});
  let titled=(await call('list_peers')).find(p=>p.id===thirdPeer.id);
  assert.equal(titled.name,'session-test');
  assert.equal(titled.native_name,'session-test');
  assert.equal(titled.preview_kind,'first_user_message');
  await request(root,thirdPeer.id,{op:'thread_metadata',thread:{id:third,name:'renamed session',preview:'hi'}});
  titled=(await call('list_peers')).find(p=>p.id===thirdPeer.id);
  assert.equal(titled.name,'renamed session');
  assert.equal((await call('connection_status')).connected,true,'metadata refresh preserves existing connection');
  assert.equal((await call('connection_status',{}, {threadId:third})).self.id,thirdPeer.id);
  await assert.rejects(call('send_message',{text:'wrong window must not use first connection'},{threadId:third}),/connect/);
  assert.equal((await call('connection_status')).self.id,local.id);
  await call('disconnect_peer');
  assert.equal((await call('connection_status')).connected,false);
  assert.equal((await call('connection_status')).user_request_evidence.status,'not_connected');
  await assert.rejects(call('send_message',{text:'after disconnect'}),/connect/);
 }finally{
  await client?.close();
  for(const child of brokers)child.kill('SIGTERM');
  await Promise.all(brokers.map(child=>new Promise(resolve=>child.exitCode!==null?resolve():child.once('exit',resolve))));
  rmSync(directory,{recursive:true,force:true});
 }
});
