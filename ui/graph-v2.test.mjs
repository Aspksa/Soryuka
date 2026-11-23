import test from 'node:test';
import assert from 'node:assert/strict';
import {
 graphFingerprint,seedStablePositions,relatedNodes,verifiedEventTargets,
 qualityFromFps,collisionRadius,visibleLabel
} from './graph-v2.js';

const old=[
 {id:'core:soryuka',x:0,y:0,z:0,vx:0.2,type:'core',size:17},
 {id:'group:memory',x:100,y:0,z:10,type:'category',size:9},
 {id:'mem:abc',x:110,y:9,z:12,type:'file',size:4}
];
const link=(source,target,type='contains')=>({source,target,type});

test('topology signature ignores label changes but detects actual new node',()=>{
 const graph={nodes:[{id:'core:soryuka',label:'Сорюка'}],edges:[]};
 const first=graphFingerprint(graph);
 graph.nodes[0].label='Другое имя';
 assert.equal(graphFingerprint(graph),first);
 graph.nodes.push({id:'mem:actual',label:'Реальная запись'});
 assert.notEqual(graphFingerprint(graph),first);
});
test('stable graph nodes retain 3D placement and velocity on updates',()=>{
 const next=old.map(n=>({id:n.id,type:n.type,size:n.size,label:n.id}));
 const born=seedStablePositions(old,next,[link('core:soryuka','group:memory')]);
 assert.deepEqual(born,[]);
 assert.deepEqual(next.map(n=>[n.x,n.y,n.z]),old.map(n=>[n.x,n.y,n.z]));
 assert.equal(next[0].vx,0.2);
});
test('only actual new IDs receive growth birth placement near their real cluster',()=>{
 const fresh=[...old.map(n=>({id:n.id,type:n.type,size:n.size})),
   {id:'mem:confirmed',type:'file',size:5}];
 const edges=[link('core:soryuka','group:memory'),
              link('group:memory','mem:confirmed')];
 const added=seedStablePositions(old,fresh,edges);
 assert.deepEqual(added,['mem:confirmed']);
 const child=fresh[3];
 assert.ok(Math.abs(child.x-100)<=18.01);
 assert.ok(Math.abs(child.y)<=18.01);
 assert.ok(Math.abs(child.z-10)<=10);
 const second=fresh.map(n=>({...n}));
 seedStablePositions(old,second,edges);
 assert.deepEqual([child.x,child.y,child.z],[second[3].x,second[3].y,second[3].z]);
});
test('memory card only contains bounded existing links and known text',()=>{
 const nodes=Object.fromEntries(old.map(n=>[n.id,{label:n.id}]));
 const edges=[link('group:memory','mem:abc'),link('mem:abc','mem:missing')];
 assert.deepEqual(relatedNodes('mem:abc',edges,nodes),[
   {id:'group:memory',name:'group:memory',relation:'contains'}
 ]);
 assert.deepEqual(relatedNodes('mem:unknown',edges,nodes),[]);
});
test('route excludes fabricated or missing source IDs',()=>{
 const known=new Set(['mem:abc','mem:xyz']);
 const authentic={source:'sqlite_audit',targets:['mem:abc','mem:missing','mem:abc']};
 assert.deepEqual(verifiedEventTargets(authentic,known),['mem:abc']);
 assert.deepEqual(verifiedEventTargets({...authentic,source:'simulation'},known),[]);
 assert.deepEqual(verifiedEventTargets({targets:['mem:abc']},known),[]);
 assert.deepEqual(verifiedEventTargets(null,known),[]);
});
test('FPS tiers are bounded with hysteresis to avoid flicker',()=>{
 assert.equal(qualityFromFps(20,'high'),'medium');
 assert.equal(qualityFromFps(14,'medium'),'low');
 assert.equal(qualityFromFps(22,'low'),'low');
 assert.equal(qualityFromFps(40,'low'),'medium');
 assert.equal(qualityFromFps(55,'medium'),'high');
 assert.equal(qualityFromFps(0,'high'),'high');
});
test('large core and category spheres use safe collision padding',()=>{
 const small=collisionRadius({type:'file',size:4});
 const category=collisionRadius({type:'category',size:9});
 const core=collisionRadius({type:'core',size:17});
 assert.ok(core>category && category>small);
 assert.ok(core>=18);
});
test('LOD treats core and category labels as stable reference points',()=>{
 assert.equal(visibleLabel('core','low',4000),true);
 assert.equal(visibleLabel('category','medium',4000),true);
 assert.equal(visibleLabel('file','low',20),false);
 assert.equal(visibleLabel('file','high',100),true);
});
