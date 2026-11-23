import test from 'node:test';
import assert from 'node:assert/strict';
import {
  GRAPHICS_DEFAULTS,GRAPHICS_LIMITS,GRAPHICS_STORAGE_KEY,
  normalizeGraphics,readGraphics,saveGraphics,resetGraphics
} from './graphics-settings.js';

function mockStorage(){
  const records=new Map();
  return {
    getItem:key=>records.has(key)?records.get(key):null,
    setItem:(key,value)=>records.set(key,String(value)),
    removeItem:key=>records.delete(key),
    raw:records
  };
}
test('defaults match the original Three.js scene',()=>{
  assert.deepEqual(GRAPHICS_DEFAULTS,{fog:0.0008,light:1,speed:1});
  assert.deepEqual(readGraphics(mockStorage()),GRAPHICS_DEFAULTS);
});
test('all sliders persist and restore on reload',()=>{
  const storage=mockStorage();
  const expected={fog:0.002,light:1.5,speed:0.5};
  const saved=saveGraphics(storage,expected);
  assert.equal(saved.saved,true);
  assert.deepEqual(saved.settings,expected);
  assert.deepEqual(readGraphics(storage),expected);
  assert.equal(JSON.parse(storage.raw.get(GRAPHICS_STORAGE_KEY)).version,1);
});
test('limits prevent invalid fog, light and speed settings',()=>{
  assert.deepEqual(normalizeGraphics({fog:-1,light:999,speed:0}),
    {fog:0,light:2,speed:0.25});
  assert.deepEqual(normalizeGraphics({fog:999,light:-999,speed:999}),
    {fog:0.003,light:0.2,speed:2});
  assert.deepEqual(normalizeGraphics({fog:NaN,light:Infinity,speed:-Infinity}),
    GRAPHICS_DEFAULTS);
  assert.deepEqual(normalizeGraphics({fog:'0.002',light:'5',speed:'2'}),
    GRAPHICS_DEFAULTS);
  assert.deepEqual(normalizeGraphics({fog:0.00083,light:1.57,speed:0.43}),
    {fog:0.00085,light:1.6,speed:0.45});
  assert.ok(GRAPHICS_LIMITS.fog.step<0.0001);
});
test('corrupt local data falls back to original values',()=>{
  const storage=mockStorage();
  storage.setItem(GRAPHICS_STORAGE_KEY,'{broken');
  assert.deepEqual(readGraphics(storage),GRAPHICS_DEFAULTS);
  storage.setItem(GRAPHICS_STORAGE_KEY,JSON.stringify({version:999,settings:{fog:0}}));
  assert.deepEqual(readGraphics(storage),GRAPHICS_DEFAULTS);
  storage.setItem(GRAPHICS_STORAGE_KEY,JSON.stringify({version:1,settings:{fog:0.002}}));
  assert.deepEqual(readGraphics(storage),{fog:0.002,light:1,speed:1});
});
test('private mode and blocked storage do not crash the graph',()=>{
  const storage={
    getItem(){throw Error('SecurityError');},
    setItem(){throw Error('QuotaExceededError');},
    removeItem(){throw Error('SecurityError');}
  };
  assert.deepEqual(readGraphics(storage),GRAPHICS_DEFAULTS);
  const result=saveGraphics(storage,{fog:0.001,light:2,speed:0.7});
  assert.equal(result.saved,false);
  assert.deepEqual(result.settings,{fog:0.001,light:2,speed:0.7});
  assert.equal(resetGraphics(storage).saved,false);
});
test('reset returns and persists original behavior',()=>{
  const storage=mockStorage();
  saveGraphics(storage,{fog:0.002,light:0.5,speed:1.8});
  const result=resetGraphics(storage);
  assert.equal(result.saved,true);
  assert.deepEqual(result.settings,GRAPHICS_DEFAULTS);
  assert.equal(storage.raw.has(GRAPHICS_STORAGE_KEY),false);
  assert.deepEqual(readGraphics(storage),GRAPHICS_DEFAULTS);
});
test('saving does not mutate the input object',()=>{
  const storage=mockStorage(),input=Object.freeze({fog:0.001,light:1.5,speed:1.5});
  assert.deepEqual(saveGraphics(storage,input).settings,input);
  assert.deepEqual(input,{fog:0.001,light:1.5,speed:1.5});
});
