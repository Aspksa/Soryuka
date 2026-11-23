/**
 * Browser/GPU smoke test for the real local Soryuka server.
 *
 * Runs headless Chromium on CI, with software WebGL, never Cloud.ru.
 * No package is installed in the user's production runtime by this script.
 */
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { randomBytes } from 'node:crypto';
import assert from 'node:assert/strict';

const root=resolve(import.meta.dirname,'..');
const artifactDir=join(root,'artifacts');
const temp=await mkdtemp(join(tmpdir(),'soryuka-webgl-'));
const os=process.platform==='win32'?'windows':'linux';
const processes=[];
const errors=[];
let browser;
async function freePort(){
  return await new Promise((resolvePort,reject)=>{
    const server=createServer();
    server.once('error',reject);
    server.listen(0,'127.0.0.1',()=>{
      const port=server.address().port;
      server.close(()=>resolvePort(port));
    });
  });
}
async function waitFor(url,limitMs=20000){
  const start=Date.now();
  while(Date.now()-start<limitMs){
    try{const r=await fetch(url,{signal:AbortSignal.timeout(1000)});if(r.ok)return;}
    catch{}
    await new Promise(r=>setTimeout(r,200));
  }
  throw Error('Local Soryuka server failed to start: '+url);
}
function start(command,args,env){
  const child=spawn(command,args,{cwd:root,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
  child.stdout?.on('data',buf=>errors.push(String(buf).slice(-1000)));
  child.stderr?.on('data',buf=>errors.push(String(buf).slice(-1000)));
  processes.push(child);
  return child;
}
try{
  const apiPort=await freePort(),uiPort=await freePort();
  if(apiPort===uiPort)throw Error('Port collision');
  const token=randomBytes(24).toString('hex');
  const env={...process.env,SORYUKA_API_PORT:String(apiPort),
    SORYUKA_UI_PORT:String(uiPort),SORYUKA_API_TOKEN:token,
    SORYUKA_DB_PATH:join(temp,'sample.sqlite3'),
    CLOUD_RU_API_KEY:'',CLOUD_RU_MODEL:'',SORYUKA_NO_BROWSER:'1'};
  start(process.env.PYTHON|| (process.platform==='win32'?'python':'python3'),
    ['-m','core.server'],env);
  start(process.execPath,['ui/server.mjs'],env);
  const origin='http://127.0.0.1:'+uiPort;
  await waitFor(origin+'/api/health');
  browser=await chromium.launch({
    headless:true,
    args:['--enable-webgl','--enable-unsafe-swiftshader',
      '--use-gl=angle','--use-angle=swiftshader',
      '--disable-gpu-sandbox','--disable-dev-shm-usage']
  });
  const page=await browser.newPage({viewport:{width:1440,height:900},deviceScaleFactor:1});
  const uncaught=[];
  page.on('pageerror',error=>uncaught.push(error.message));
  page.on('console',message=>{
    if(message.type()==='error')uncaught.push('Console: '+message.text());
  });
  await page.goto(origin,{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>window.__soryukaDiagnostics?.().totalNodes>0,
    {timeout:30000});
  const diagnostics=await page.evaluate(()=>window.__soryukaDiagnostics());
  console.log('WebGL initial diagnostics:',JSON.stringify(diagnostics),
    'browser errors:',JSON.stringify(uncaught));
  assert.equal(diagnostics.webgl,true);
  assert.ok(diagnostics.totalNodes>=7);
  assert.ok(diagnostics.visibleNodes>=7);
  assert.ok(diagnostics.coreScreen);
  assert.ok(await page.locator('#canvas-container canvas').count());
  assert.ok(await page.locator('#soryuka-engine-error').isHidden());
  const gl=await page.locator('#canvas-container canvas').evaluate(canvas=>
    canvas.getContext('webgl2')||canvas.getContext('webgl'));
  assert.ok(gl,'A real WebGL context must exist');
  // Real raycast opens the card for one of the nodes near the central core.
  const center=diagnostics.coreScreen;
  await page.mouse.click(center.x,center.y);
  await page.locator('#node-card').waitFor({state:'visible',timeout:7000});
  assert.ok((await page.locator('#node-card-heading').textContent())?.trim());
  await page.locator('#node-card-close').click();
  assert.ok(await page.locator('#node-card').isHidden());

  await page.locator('#graphics-toggle').click();
  await page.locator('#graphics-fog').evaluate(input=>{
    input.value='0.00120';
    input.dispatchEvent(new Event('input',{bubbles:true}));
  });
  await page.waitForFunction(async()=>{
    const result=await fetch('/api/mirror/settings').then(r=>r.json());
    return Math.abs(result.settings?.fog-0.0012)<1e-8;
  },{timeout:10000});
  assert.ok(Math.abs((await page.evaluate(()=>window.__soryukaDiagnostics())).fogDensity-0.0012)<1e-8);
  await page.reload({waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>window.__soryukaDiagnostics?.().totalNodes>0,
    {timeout:30000});
  await page.waitForFunction(()=>Math.abs(window.__soryukaDiagnostics?.().fogDensity-0.0012)<1e-8,
    {timeout:10000});
  await page.locator('#graphics-toggle').click();
  await page.locator('#graphics-reset').click();
  await page.waitForFunction(async()=>{
    const result=await fetch('/api/mirror/settings').then(r=>r.json());
    return Math.abs(result.settings?.fog-0.0008)<1e-8;
  },{timeout:10000});
  await mkdir(artifactDir,{recursive:true});
  await page.screenshot({path:join(artifactDir,'soryuka-webgl-'+os+'.png'),fullPage:true});
  assert.deepEqual(uncaught,[],'No uncaught browser errors');

  // Force WebGL API absence and verify that users can still reach the chat.
  const fallback=await browser.newPage({viewport:{width:1440,height:900}});
  await fallback.addInitScript(()=>{
    const previous=HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext=function(type,...args){
      if(type==='webgl'||type==='webgl2'||type==='experimental-webgl')return null;
      return previous.call(this,type,...args);
    };
  });
  await fallback.goto(origin,{waitUntil:'domcontentloaded'});
  await fallback.locator('#soryuka-engine-error').waitFor({state:'visible',timeout:12000});
  assert.ok(await fallback.locator('#soryuka-engine-error a[href="/console.html"]').isVisible());
  await fallback.screenshot({path:join(artifactDir,'soryuka-webgl-fallback-'+os+'.png')});
  await fallback.close();
  await page.close();
  console.log('Soryuka browser smoke passed:',os,diagnostics.totalNodes,'real nodes');
}catch(error){
  console.error(error);
  console.error('Child process log tail:',errors.slice(-12).join('\n'));
  process.exitCode=1;
}finally{
  try{await browser?.close();}catch{}
  for(const child of processes){if(child.exitCode===null)child.kill();}
  await new Promise(resolve=>setTimeout(resolve,500));
  await rm(temp,{recursive:true,force:true});
}
