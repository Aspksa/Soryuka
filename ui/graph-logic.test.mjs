import test from 'node:test';
import assert from 'node:assert/strict';
import {
  normalizeGraph,graphRoute,neighborsFor,seedNewNodes,linkId,GRAPH_LIMITS
} from './graph-logic.js';
function sample(){
  return {nodes:[
    {id:'core:soryuka',name:'Сорюка',category:'core',kind:'character',size:17},
    {id:'group:memory',name:'Память',category:'memory',kind:'cluster',size:9},
    {id:'mem:a',name:'Реальный факт',category:'memory',kind:'fact',size:5,
      status:'owner_confirmed',detail:'Основано на записи'},
    {id:'mem:b',name:'Новый факт',category:'memory',kind:'fact',size:5},
    {id:'mem:c',name:'Без связи',category:'memory',kind:'fact',size:5}
  ],links:[
    {source:'core:soryuka',target:'group:memory',relation:'category'},
    {source:'group:memory',target:'mem:a',relation:'contains'},
    {source:'group:memory',target:'mem:b',relation:'contains'},
    {source:'mem:a',target:'mem:b',relation:'related'}
  ],categories:[{id:'memory',color:'#55b5f5'}]};
}
test('real SQLite node IDs and confirmed edges survive normalization',()=>{
  const graph=normalizeGraph(sample());
  assert.equal(graph.nodes.length,5);
  assert.equal(graph.edges.length,4);
  assert.equal(graph.nodes[2].description,'Основано на записи');
  assert.equal(graph.nodes[2].status,'owner_confirmed');
  assert.equal(graph.source,'local_sqlite');
  assert.equal(graph.colors.memory,'#55b5f5');
});
test('evidence route follows only actual edges',()=>{
  const graph=normalizeGraph(sample());
  assert.deepEqual(graphRoute('core:soryuka','mem:a',graph.edges),
    ['core:soryuka','group:memory','mem:a']);
  assert.deepEqual(graphRoute('mem:a','mem:b',graph.edges),['mem:a','mem:b']);
  assert.deepEqual(graphRoute('core:soryuka','mem:c',graph.edges),[]);
  assert.deepEqual(graphRoute('unknown','mem:b',graph.edges),[]);
});
test('no invented direct links are created',()=>{
  const edges=normalizeGraph(sample()).edges;
  assert.equal(edges.some(e=>e.source==='core:soryuka'&&e.target==='mem:a'),false);
  assert.deepEqual(neighborsFor('mem:a',edges),[
    {id:'group:memory',relation:'contains'},{id:'mem:b',relation:'related'}
  ]);
});
test('D3 force-link object endpoints are resolved safely',()=>{
  const raw=sample();
  const edges=normalizeGraph(raw).edges.map(x=>({...x,
    source:{id:x.source},target:{id:x.target}}));
  assert.equal(linkId(edges[0].source),'core:soryuka');
  assert.deepEqual(graphRoute('core:soryuka','mem:b',edges),
    ['core:soryuka','group:memory','mem:b']);
});
test('old node coordinates and velocity remain stable across new real memories',()=>{
  const initial=normalizeGraph(sample());
  initial.nodes[0].x=15;initial.nodes[0].y=6;initial.nodes[0].z=12;
  initial.nodes[1].x=18;initial.nodes[1].y=8;initial.nodes[1].z=15;
  initial.nodes[2].x=40;initial.nodes[2].y=40;initial.nodes[2].z=40;
  initial.nodes[2].vx=2;
  const expanded=normalizeGraph(sample());
  seedNewNodes(initial.nodes,expanded.nodes)(expanded.edges);
  assert.deepEqual([expanded.nodes[2].x,expanded.nodes[2].y,expanded.nodes[2].z,expanded.nodes[2].vx],
    [40,40,40,2]);
  const newNode=expanded.nodes[3];
  assert.ok([newNode.x,newNode.y,newNode.z].every(Number.isFinite));
  assert.ok(Math.hypot(newNode.x-18,newNode.y-8,newNode.z-15)<160);
  assert.deepEqual(
    expanded.nodes.map(n=>[n.x,n.y,n.z]),
    seedNewNodes(initial.nodes,normalizeGraph(sample()).nodes)(expanded.edges).map(n=>[n.x,n.y,n.z])
  );
});
test('invalid payloads and duplicate nodes are safely rejected',()=>{
  assert.throws(()=>normalizeGraph({nodes:[],links:null}),/данные/);
  const raw=sample();
  raw.nodes.push({...raw.nodes[2]},{id:null,name:'bad'});
  raw.links.push({source:'mem:a',target:'missing',relation:'related'});
  raw.categories.push({id:'unexpected',color:'url(evil)'});
  const result=normalizeGraph(raw);
  assert.equal(result.nodes.length,5);
  assert.equal(result.edges.length,4);
  assert.equal(result.colors.unexpected,'#7886ad');
});
test('huge graphs are bounded without synthetic nodes or edges',()=>{
  const raw=sample();
  for(let i=0;i<5000;i++)raw.nodes.push({id:'mem:'+i,name:'Реальная запись '+i,kind:'fact'});
  for(let i=0;i<5000;i++)raw.links.push({source:'group:memory',target:'mem:'+i,relation:'contains'});
  const graph=normalizeGraph(raw);
  assert.ok(graph.nodes.length<=GRAPH_LIMITS.nodes);
  assert.ok(graph.edges.length<=GRAPH_LIMITS.links);
  assert.ok(graph.edges.every(e=>graph.nodes.some(n=>n.id===e.target)));
});
test('graph card links are limited to avoid thousands of DOM buttons',()=>{
  const edges=Array.from({length:100},(_,i)=>({source:'mem:a',target:'mem:'+i,relation:'related'}));
  assert.equal(neighborsFor('mem:a',edges).length,GRAPH_LIMITS.cardLinks);
});
test('non-finite node dimensions are clamped to safe materials',()=>{
  const raw=sample();raw.nodes[0].size=Infinity;
  raw.nodes[2].size=-100;raw.nodes[3].size=1e100;
  const n=normalizeGraph(raw).nodes;
  assert.equal(n[0].size,4);
  assert.equal(n[2].size,1);
  assert.equal(n[3].size,24);
});
