import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const script=fs.readFileSync(new URL('./mirror-bootstrap.js',import.meta.url),'utf8');
function run({webgl=true}={}){
  const events=new Map();
  const nodes={
    'soryuka-engine-error':{hidden:true},
    'soryuka-engine-message':{textContent:''},
    'render-status':{textContent:''}
  };
  const callbacks=[];
  const gl=webgl?{getExtension:()=>null}:null;
  const window={addEventListener:(type,fn)=>events.set(type,fn)};
  const document={
    getElementById:key=>nodes[key],
    createElement:tag=>{
      assert.equal(tag,'canvas');
      return {getContext:()=>gl};
    }
  };
  vm.runInNewContext(script,{window,document,setTimeout:(fn,ms)=>callbacks.push({fn,ms})});
  return {nodes,events,callbacks};
}

test('no WebGL displays actionable diagnostic rather than empty screen',()=>{
  const x=run({webgl:false});
  assert.equal(x.nodes['soryuka-engine-error'].hidden,false);
  assert.match(x.nodes['soryuka-engine-message'].textContent,/WebGL/);
});
test('failed module import surfaces a loading diagnostic before timeout',()=>{
  const x=run();
  x.events.get('error')({message:'Failed to fetch dynamically imported module'});
  assert.equal(x.nodes['soryuka-engine-error'].hidden,false);
  assert.match(x.nodes['soryuka-engine-message'].textContent,/Ошибка инициализации/);
});
test('startup watchdog reveals missing renderer after deadline',()=>{
  const x=run();
  assert.equal(x.nodes['soryuka-engine-error'].hidden,true);
  assert.equal(x.callbacks.length,1);
  x.callbacks[0].fn();
  assert.equal(x.nodes['soryuka-engine-error'].hidden,false);
  assert.match(x.nodes['soryuka-engine-message'].textContent,/не загрузилась/);
});
test('renderer-ready clears the previous error and watchdog stays quiet',()=>{
  const x=run({webgl:false});
  x.events.get('soryuka:renderer-ready')();
  assert.equal(x.nodes['soryuka-engine-error'].hidden,true);
  x.callbacks[0].fn();
  assert.equal(x.nodes['soryuka-engine-error'].hidden,true);
});
test('GPU context loss after successful startup remains visible to owner',()=>{
  const x=run();
  x.events.get('soryuka:renderer-ready')();
  x.events.get('soryuka:renderer-failed')({detail:'Контекст WebGL потерян'});
  assert.equal(x.nodes['soryuka-engine-error'].hidden,false);
  assert.match(x.nodes['soryuka-engine-message'].textContent,/Контекст WebGL/);
});
