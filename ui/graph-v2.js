/**
 * Soryuka 3D graph utilities. No DOM, no external dependencies, no fake cognition.
 * Used by the live renderer and cross-platform Node regression tests.
 */
const finite=n=>typeof n==='number'&&Number.isFinite(n);
const safeId=id=>typeof id==='string'?id:String(id??'');
const endpoint=e=>safeId(e&&typeof e==='object'?e.id:e);

export function graphFingerprint(data){
  if(!data || !Array.isArray(data.nodes)||!Array.isArray(data.edges))return '';
  // SQLite graph snapshots arrive sorted. Include IDs and relations, not private text.
  return JSON.stringify([
    data.nodes.map(n=>safeId(n.id)),
    data.edges.map(e=>[endpoint(e.source),endpoint(e.target),
      safeId(e.type),safeId(e.relation)])
  ]);
}

function stableAngle(id){
  // Pure deterministic placement within a cluster; not invented memory data.
  let hash=2166136261;
  for(const ch of safeId(id)){hash=Math.imul(hash^ch.charCodeAt(0),16777619)>>>0;}
  return hash/4294967296*Math.PI*2;
}

export function seedStablePositions(previousNodes,nextNodes,nextEdges){
  const previous=new Map((previousNodes||[]).filter(n=>n?.id).map(n=>[safeId(n.id),n]));
  const next=new Map(nextNodes.map(n=>[safeId(n.id),n]));
  const isNew=[];
  for(const node of nextNodes){
    const old=previous.get(safeId(node.id));
    if(old&&finite(old.x)&&finite(old.y)&&finite(old.z)){
      node.x=old.x;node.y=old.y;node.z=old.z;
      if(finite(old.vx))node.vx=old.vx;
      if(finite(old.vy))node.vy=old.vy;
      if(finite(old.vz))node.vz=old.vz;
    }else if(previous.size){isNew.push(node.id);}
  }
  // Newly inserted confirmed SQLite nodes join their real category/linked parent.
  for(const node of nextNodes){
    if(finite(node.x)&&finite(node.y)&&finite(node.z))continue;
    const edge=(nextEdges||[]).find(e=>endpoint(e.target)===safeId(node.id) &&
      next.has(endpoint(e.source))) ||
      (nextEdges||[]).find(e=>endpoint(e.source)===safeId(node.id) &&
      next.has(endpoint(e.target)));
    const other=edge&&(endpoint(edge.source)===safeId(node.id)?
      next.get(endpoint(edge.target)):next.get(endpoint(edge.source)));
    const parent=other&&finite(other.x)&&finite(other.y)&&finite(other.z)?other:null;
    if(parent){
      const angle=stableAngle(node.id);
      node.x=parent.x+18*Math.cos(angle);
      node.y=parent.y+18*Math.sin(angle);
      node.z=parent.z+9*Math.cos(angle*0.67);
    }
  }
  return isNew;
}

export function relatedNodes(id,edges,map,max=12){
  const results=[];
  const used=new Set();
  for(const e of edges||[]){
    const src=endpoint(e.source),dst=endpoint(e.target);
    const target=src===id?dst:dst===id?src:null;
    if(!target||used.has(target)||!map[target])continue;
    used.add(target);
    results.push({id:target,name:map[target].label||target,relation:e.relation||e.type||'related'});
    if(results.length>=max)break;
  }
  return results;
}

export function verifiedEventTargets(event,knownIds,limit=8){
  if(!event||!Array.isArray(event.targets))return [];
  if(event.source!=='sqlite_audit'&&event.source!=='sqlite_message')return [];
  return [...new Set(event.targets.filter(id=>typeof id==='string'&&knownIds.has(id)))].slice(0,limit);
}

export function qualityFromFps(fps,current='high'){
  if(!finite(fps)||fps<=0)return current;
  // Hysteresis limits quality oscillation around the thresholds.
  if(current==='high')return fps<28?'medium':'high';
  if(current==='medium')return fps<17?'low':fps>51?'high':'medium';
  if(current==='low')return fps>33?'medium':'low';
  return 'high';
}

export function collisionRadius(node,scale=0.4){
  const radius=Math.max(1,Number(node?.size)||4)*scale;
  return radius+(node?.type==='core'?14:node?.type==='category'?8:3);
}

export function visibleLabel(type,quality,distance){
  if(type==='core'||type==='category')return true;
  return quality==='high'&&distance<260;
}
