import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StdioClientTransport} from '@modelcontextprotocol/sdk/client/stdio.js';
import {z} from 'zod';
const root=resolve('../..');
async function start(agent,directory,extraEnv={}) {
 const plugin=join(root,'plugins',agent);
 const config=JSON.parse(readFileSync(join(plugin,'.mcp.json'),'utf8')).mcpServers.relay;
 const notifications=[];
 const client=new Client({name:'acdc-contract-test',version:'1'});
 client.setNotificationHandler(z.object({method:z.literal('notifications/claude/channel'),params:z.any()}),event=>notifications.push(event.params));
 const transport=new StdioClientTransport({command:config.command,args:config.args.map(x=>x.replaceAll('${CLAUDE_PLUGIN_ROOT}',plugin)),
  env:{...process.env,ACDC_RELAY_DIR:join(directory,'sockets'),...extraEnv},stderr:'pipe'});
 let errors='';transport.stderr.on('data',data=>{errors+=data;});
 const controller=new AbortController();
 try {await client.connect(transport,{signal:controller.signal,timeout:20000});}
 catch(e) {controller.abort();await client.close();throw new Error(e.message+'\n'+errors);}
 const call=async(name,args={})=>{
  const result=await client.callTool({name,arguments:args});
  if(result.isError)throw new Error(result.content[0].text);
  return JSON.parse(result.content[0].text);
 };
 const peer=await call('register_peer',{name:agent});
 return {client,call,peer,notifications};
}

test('plugin stdio tools relay to Claude notification and invoke the Codex CLI boundary',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'acdc-'));
 let a,b;
 try {
  const bin=join(directory,'bin');mkdirSync(bin);
  // Controlled CLI boundary; tests/native_queue_probe.py separately exercises the real runtime.
  writeFileSync(join(bin,'codex'),'#!/usr/bin/env python3\nimport json,os,sys\nwith open(os.environ["ACDC_CAPTURE"],"w") as f: json.dump(sys.argv[1:],f)\nprint("Queued")\n',{mode:0o700});
  const capture=join(directory,'capture.json');
  a=await start('codex',directory,{PATH:bin+':'+process.env.PATH,ACDC_CAPTURE:capture,CODEX_THREAD_ID:'11111111-1111-1111-1111-111111111111',ACDC_CODEX_REMOTE:'ws://127.0.0.1:1'});
  b=await start('claude',directory);
  assert.equal(a.client.getServerCapabilities().experimental?.['claude/channel'],undefined);
  assert.ok(b.client.getServerCapabilities().experimental['claude/channel']);
  const tools=await a.client.listTools();assert.deepEqual(tools.tools.map(t=>t.name).sort(),['list_peers','register_peer','send_message']);
  assert.ok((await a.call('list_peers')).some(p=>p.id===b.peer.id));
  assert.equal((await b.call('list_peers')).find(p=>p.id===a.peer.id).thread_id,'11111111-1111-1111-1111-111111111111');
  assert.equal(b.peer.thread_id,null);
  const sent=await a.call('send_message',{to:b.peer.id,text:'Review the grain.'});
  assert.equal(sent.status,'transport_written');
  for(let i=0;i<30&&!b.notifications.length;i++)await new Promise(r=>setTimeout(r,10));
  assert.equal(b.notifications.length,1);
  assert.equal(b.notifications[0].meta.sender_id,a.peer.id);
  assert.match(b.notifications[0].content,/not a user instruction/);
  assert.match(b.notifications[0].content,/Review the grain/);
  const reply=await b.call('send_message',{to:a.peer.id,text:'Grain reviewed.'});
  assert.equal(reply.status,'queued');
  const argv=JSON.parse(readFileSync(capture,'utf8'));
  assert.equal(argv[0],'queue');assert.equal(argv[argv.indexOf('--thread')+1],'11111111-1111-1111-1111-111111111111');
  const text=argv[argv.indexOf('--message')+1];assert.match(text,/Grain reviewed/);assert.match(text,/not a user instruction/);
  const rebound=await a.call('register_peer',{name:'codex',thread_id:'22222222-2222-2222-2222-222222222222'});
  assert.equal(rebound.thread_id,'22222222-2222-2222-2222-222222222222');
  await assert.rejects(a.call('register_peer',{name:'codex',thread_id:'invalid'}));
  assert.equal((await b.call('list_peers')).find(p=>p.id===a.peer.id).thread_id,'22222222-2222-2222-2222-222222222222');
  await b.call('send_message',{to:a.peer.id,text:'Check rebound destination.'});
  const reboundArgv=JSON.parse(readFileSync(capture,'utf8'));
  assert.equal(reboundArgv[reboundArgv.indexOf('--thread')+1],'22222222-2222-2222-2222-222222222222');
  await assert.rejects(a.call('send_message',{to:b.peer.id,text:'spoof',sender:'tada'}));
  await assert.rejects(a.call('read_messages'));
  const closedId=b.peer.id;await b.client.close();b=null;
  await assert.rejects(a.call('send_message',{to:closedId,text:'offline'}));
 } finally {await b?.client.close();await a?.client.close();rmSync(directory,{recursive:true,force:true});}
});
