// Soryuka frontend server: Node.js built-ins only. Proxy secrets never reach the browser.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WebSocketServer } from 'ws';

const root=path.dirname(fileURLToPath(import.meta.url));
const uiPort=Number(process.env.SORYUKA_UI_PORT||53555);
const apiPort=Number(process.env.SORYUKA_API_PORT||53554);
const token=process.env.SORYUKA_API_TOKEN;
if(!token){ console.error('SORYUKA_API_TOKEN missing: run launch.py'); process.exit(1); }
const selfOrigin='http://127.0.0.1:'+uiPort;
const staticFiles=new Map([
  ['/','index.html'],['/index.html','index.html'],
  ['/mirror.css','mirror.css'],['/mirror.js','mirror.js'],
  ['/console','console.html'],['/console.html','console.html'],
  ['/style.css','style.css'],['/app.js','app.js'],
  ['/mirror-original.js','mirror-original.js'],
  ['/graph-v2.js','graph-v2.js'],
  ['/mirror-bootstrap.js','mirror-bootstrap.js'],
  ['/graphics-settings.js','graphics-settings.js'],
  ['/vendor/three.module.js','node_modules/three/build/three.module.js'],
  ['/vendor/three-addons/controls/OrbitControls.js','node_modules/three/examples/jsm/controls/OrbitControls.js'],
  ['/vendor/d3-force-3d.min.js','node_modules/d3-force-3d/dist/d3-force-3d.min.js']
]);
const contentType={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8'};

function send(res,code,payload,type='application/json; charset=utf-8'){
  const body=Buffer.isBuffer(payload)?payload:Buffer.from(typeof payload==='string'?payload:JSON.stringify(payload));
  res.writeHead(code,{'Content-Type':type,'Content-Length':body.length,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});
  res.end(body);
}
function proxy(req,res){
  if(!['GET','POST'].includes(req.method)){send(res,405,{error:'Method not allowed'});return;}
  const requestOrigin=req.headers.origin;
  if(requestOrigin&&requestOrigin!==selfOrigin){send(res,403,{error:'Cross-site request denied'});return;}
  const length=Number(req.headers['content-length']||0);
  if(req.method==='POST'&&(req.headers['content-type']||'').split(';')[0].trim()!=='application/json'){
    send(res,415,{error:'JSON required'});return;
  }
  if(!Number.isSafeInteger(length)||length<0||length>32768){
    send(res,413,{error:'Request too large'});return;
  }
  let chunks=[],size=0;
  req.on('data',part=>{
    size+=part.length;
    if(size>32768){req.destroy();return;}
    chunks.push(part);
  });
  req.on('end',()=>{
    const body=Buffer.concat(chunks);
    const uri=new URL(req.url||'/api/health',selfOrigin);
    const opt={hostname:'127.0.0.1',port:apiPort,method:req.method,
      path:uri.pathname+uri.search,timeout:65000,
      headers:{'X-Soryuka-Token':token,'Content-Type':'application/json',
               'Content-Length':body.length}};
    const downstream=http.request(opt,response=>{
      let data=[],total=0;
      response.on('data',chunk=>{
        total+=chunk.length;
        if(total>2_000_000){downstream.destroy();return;}
        data.push(chunk);
      });
      response.on('end',()=>send(res,response.statusCode||502,Buffer.concat(data)));
    });
    downstream.on('error',()=>{if(!res.headersSent)send(res,502,{error:'Python API unavailable'});});
    downstream.on('timeout',()=>downstream.destroy());
    downstream.end(body);
  });
}
const server=http.createServer((req,res)=>{
  const origin=req.headers.origin;
  const url=new URL(req.url||'/',selfOrigin);
  if(url.pathname.startsWith('/api/'))return proxy(req,res);
  if(req.method!=='GET'&&req.method!=='HEAD')return send(res,405,'Method not allowed','text/plain');
  if(origin&&origin!==selfOrigin)return send(res,403,'Forbidden','text/plain');
  const target=staticFiles.get(url.pathname);
  if(!target)return send(res,404,'Not found','text/plain');
  fs.readFile(path.join(root,target),(err,bytes)=>{
    if(err)return send(res,404,'Not found','text/plain');
    const type=contentType[path.extname(target)]||'application/octet-stream';
    if(req.method==='HEAD'){res.writeHead(200,{'Content-Type':type,'Content-Length':bytes.length});res.end();}
    else send(res,200,bytes,type);
  });
});

// Soryuka 3D graph uses a live WebSocket. This relay ONLY reads from
// Python's token-authenticated SQLite API, keeping the Python process the sole API.
const mirrorWss=new WebSocketServer({noServer:true,maxPayload:32_768,perMessageDeflate:false});
const MAX_SOCKETS=8;
server.on('upgrade',(req,socket,head)=>{
  let pathname;
  try{pathname=new URL(req.url||'/',selfOrigin).pathname;}catch{socket.destroy();return;}
  const origin=req.headers.origin;
  if(pathname!=='/mirror-ws'||mirrorWss.clients.size>=MAX_SOCKETS||
     (origin&&origin!==selfOrigin)||!['127.0.0.1','localhost'].includes((req.headers.host||'').split(':')[0])){
    socket.write('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
    socket.destroy();return;
  }
  mirrorWss.handleUpgrade(req,socket,head,ws=>mirrorWss.emit('connection',ws,req));
});
function getLocal(pathname){
  return new Promise((resolve,reject)=>{
    const request=http.get({hostname:'127.0.0.1',port:apiPort,path:pathname,
      timeout:9000,headers:{'X-Soryuka-Token':token}},response=>{
      if(response.statusCode!==200){response.resume();reject(new Error('API '+response.statusCode));return;}
      const chunks=[];let length=0;
      response.on('data',part=>{
        length+=part.length;
        if(length>2_000_000){request.destroy(new Error('Graph too large'));return;}
        chunks.push(part);
      });
      response.on('end',()=>{try{resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));}catch(err){reject(err);}});
    });
    request.on('error',reject);
    request.on('timeout',()=>request.destroy(new Error('Timeout')));
  });
}
function wsSend(ws,doc){
  if(ws.readyState!==1)return;
  // Avoid unbounded memory when tab is suspended or slow.
  if(ws.bufferedAmount>1_000_000){ws.close(1009,'Slow graph client');return;}
  ws.send(JSON.stringify(doc));
}
function eventForOriginal(item){
  const map={read:'read',write:'write',delete:'delete',link:'link',learn:'learn',growth:'growth'};
  const type=map[item.kind]||'read',targets=Array.isArray(item.targets)?item.targets.slice(0,10):[];
  return {
    type,origin_kind:item.kind,
    path:targets[0]||'group:'+(item.category||'memory'),
    timestamp:item.at,
    query:'', // Never put private chat text into feed.
    results:['search','grep','learn','growth','link','delete'].includes(type)?targets.map(path=>({path,score:1})):[],
    title:item.title,
    targets,
    source:item.source,
    phase:item.phase||null,
    context_kind:item.context_kind||null
  };
}
mirrorWss.on('connection',ws=>{
  const state={audit:0,message:0,busy:false,initialized:false,ended:false};
  let interval;
  const sync=async()=>{
    if(state.busy||ws.readyState!==1)return;
    state.busy=true;
    try{
      if(!state.initialized){
        const graph=await getLocal('/api/mirror/graph');
        wsSend(ws,{type:'reload',data:graph});
        state.audit=Math.max(0,Number(graph.cursor?.audit||0)-22);
        state.message=Math.max(0,Number(graph.cursor?.message||0)-24);
        state.initialized=true;
      }
      const result=await getLocal('/api/mirror/activity?audit_after='+state.audit+
                                  '&message_after='+state.message);
      const items=Array.isArray(result.items)?result.items:[];
      state.audit=Number(result.cursor?.audit||state.audit);
      state.message=Number(result.cursor?.message||state.message);
      if(items.length){
        const graph=await getLocal('/api/mirror/graph');
        wsSend(ws,{type:'reload',data:graph});
        for(const item of items.slice(-60))wsSend(ws,{type:'event',data:eventForOriginal(item)});
      }
    }catch(error){
      // Keep existing frontend/SQLite usable while Python is restarting.
      if(ws.readyState===1)wsSend(ws,{type:'error',error:'Локальный Python API недоступен'});
    }finally{state.busy=false;}
  };
  void sync();
  interval=setInterval(()=>void sync(),1800);
  ws.on('close',()=>clearInterval(interval));
  ws.on('error',()=>clearInterval(interval));
});

server.listen(uiPort,'127.0.0.1',()=>console.log('Soryuka UI ready: '+selfOrigin));
process.on('SIGTERM',()=>{mirrorWss.close();server.close(()=>process.exit(0));});
