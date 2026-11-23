// Soryuka frontend server: Node.js built-ins only. Proxy secrets never reach the browser.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root=path.dirname(fileURLToPath(import.meta.url));
const uiPort=Number(process.env.SORYUKA_UI_PORT||53555);
const apiPort=Number(process.env.SORYUKA_API_PORT||53554);
const token=process.env.SORYUKA_API_TOKEN;
if(!token){ console.error('SORYUKA_API_TOKEN missing: run launch.py'); process.exit(1); }
const selfOrigin='http://127.0.0.1:'+uiPort;
const staticFiles=new Map([
  ['/','index.html'],['/index.html','index.html'],['/style.css','style.css'],
  ['/app.js','app.js']
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
server.listen(uiPort,'127.0.0.1',()=>console.log('Soryuka UI ready: '+selfOrigin));
process.on('SIGTERM',()=>server.close(()=>process.exit(0)));
