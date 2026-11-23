/**
 * Local graph helpers for Soryuka. No browser APIs, no Cloud.ru calls.
 * Only use IDs and links returned by the protected Python SQLite graph API.
 */
export const GRAPH_LIMITS=Object.freeze({nodes:1100,links:2200,cardLinks:20,routeHops:5});

function validId(id){return typeof id==='string' && id.length>0 && id.length<180;}
function safeColor(input,fallback='#7886ad'){
  return typeof input==='string'&&/^#[0-9a-fA-F]{6}$/.test(input)?input:fallback;
}
function seed(id){
  let h=2166136261;
  for(let i=0;i<id.length;i++)h=Math.imul(h^id.charCodeAt(i),16777619);
  return h>>>0;
}
function xyZFromSeed(id,distance=40){
  const hash=seed(id);
  const theta=(hash%1009)/1009*Math.PI*2;
  const z=(((hash>>>12)%1201)/1201-.5)*distance;
  return {x:Math.cos(theta)*distance,y:Math.sin(theta)*distance,z};
}
export function normalizeGraph(raw){
  if(!raw||!Array.isArray(raw.nodes)||!Array.isArray(raw.links))
    throw new Error('Неверные данные графа SQLite');
  const seen=new Set();
  const nodes=[];
  for(const item of raw.nodes){
    if(nodes.length>=GRAPH_LIMITS.nodes)break;
    if(!item||!validId(item.id)||seen.has(item.id))continue;
    seen.add(item.id);
    const type=item.kind==='character'?'core':item.kind==='cluster'?'category':
      item.kind==='system'?'core':'file';
    const n=Number(item.size);
    nodes.push({
      id:item.id,label:String(item.name??'').slice(0,140),
      category:String(item.category??'memory').slice(0,64),type,kind:String(item.kind??''),
      size:Number.isFinite(n)?Math.max(1,Math.min(24,n)):4,
      status:String(item.status??'').slice(0,80),
      description:String(item.detail??'').slice(0,240),
      created_at:String(item.created_at??'').slice(0,40)
    });
  }
  const edges=[];
  const edgeIds=new Set();
  for(const e of raw.links){
    if(edges.length>=GRAPH_LIMITS.links)break;
    if(!e||!seen.has(e.source)||!seen.has(e.target)||e.source===e.target)continue;
    const relation=String(e.relation??'related').slice(0,80);
    const key=[e.source,e.target].sort().join('\u0000')+'\u0000'+relation;
    if(edgeIds.has(key))continue;
    edgeIds.add(key);
    edges.push({
      source:e.source,target:e.target,
      relation,
      type:relation==='category'?'structural':
        ['contains','message','episode','canon','skill'].includes(relation)?'contains':'references'
    });
  }
  const colors={core:'#eacfa3',memory:'#4488ff',dialog:'#7288fc',
    story:'#f2a5ce',learning:'#a855f7',growth:'#22c55e',skill:'#e4a960'};
  if(Array.isArray(raw.categories)){
    for(const group of raw.categories)
      if(group&&validId(group.id))colors[group.id]=safeColor(group.color,colors[group.id]);
  }
  return {nodes,edges,colors,source:'local_sqlite'};
}
export function linkId(value){
  return typeof value==='string'?value:(value&&typeof value.id==='string'?value.id:null);
}
export function neighborsFor(id,edges){
  const seen=new Map();
  for(const edge of edges){
    const a=linkId(edge.source),b=linkId(edge.target);
    if(a===id&&b)seen.set(b,edge.relation);
    if(b===id&&a)seen.set(a,edge.relation);
  }
  return [...seen].map(([nodeId,relation])=>({id:nodeId,relation})).slice(0,GRAPH_LIMITS.cardLinks);
}
export function graphRoute(from,to,edges,maxHops=GRAPH_LIMITS.routeHops){
  if(!validId(from)||!validId(to))return [];
  if(from===to)return [from];
  const next=new Map(),queue=[from];next.set(from,null);
  for(let i=0;i<queue.length;i++){
    const at=queue[i],depth=(()=>{
      let p=at,d=0;while(next.get(p)!==null){p=next.get(p);d++;}return d;
    })();
    if(depth>=maxHops)continue;
    for(const edge of edges){
      const a=linkId(edge.source),b=linkId(edge.target);
      const n=a===at?b:b===at?a:null;
      if(n===null||next.has(n))continue;
      next.set(n,at);
      if(n===to){
        const result=[to];let p=to;
        while(next.get(p)!==null){p=next.get(p);result.push(p);}
        return result.reverse();
      }
      queue.push(n);
    }
  }
  return [];
}
/** Grow real new nodes around their existing graph neighbors.
 * Coordinates are *layout only*, never mistaken for stored facts or connections.
 */
export function seedNewNodes(previous,next){
  const old=new Map((previous||[]).map(n=>[n.id,n]));
  const map=new Map(next.map(n=>[n.id,n]));
  const anchors=new Map();
  for(const n of next){
    const p=old.get(n.id);
    if(p&&[p.x,p.y,p.z].every(Number.isFinite)){
      n.x=p.x;n.y=p.y;n.z=p.z;
      n.vx=Number.isFinite(p.vx)?p.vx:0;
      n.vy=Number.isFinite(p.vy)?p.vy:0;
      n.vz=Number.isFinite(p.vz)?p.vz:0;
    }
  }
  return (edges)=>{
    // A few passes support nodes attached to a new category/session.
    for(let iteration=0;iteration<3;iteration++){
      for(const edge of edges){
        const a=map.get(linkId(edge.source)),b=map.get(linkId(edge.target));
        if(!a||!b)continue;
        if(Number.isFinite(a.x)&&!Number.isFinite(b.x))anchors.set(b.id,a);
        if(Number.isFinite(b.x)&&!Number.isFinite(a.x))anchors.set(a.id,b);
      }
      for(const [id,a] of anchors){
        const n=map.get(id);
        if(!n||Number.isFinite(n.x))continue;
        const off=xyZFromSeed(id,24+(seed(id)%35));
        n.x=a.x+off.x;n.y=a.y+off.y;n.z=a.z+off.z;
        n.vx=0;n.vy=0;n.vz=0;
      }
      anchors.clear();
    }
    for(const n of next){
      if(Number.isFinite(n.x))continue;
      const off=xyZFromSeed(n.id,n.type==='core'?25:90);
      n.x=off.x;n.y=off.y;n.z=off.z;n.vx=0;n.vy=0;n.vz=0;
    }
    return next;
  };
}
