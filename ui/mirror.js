// Soryuka Memory Mirror: original dependency-free 3D projection inspired by
// https://github.com/gnufoo/agent-mirror (conceptual reference, no source copied).
// All graph nodes, links and activity originate in local Soryuka SQLite.
// No CDN, no WebSocket dependency, no invented AI internal thoughts.
'use strict';

const $ = id => document.getElementById(id);
const canvas = $('mirror-canvas');
const ctx = canvas.getContext('2d', { alpha: false });
const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
const palette = {core:'#f8e5b3',memory:'#55b5f5',dialog:'#6988f4',
  story:'#e8a5ca',learning:'#b6a0ff',growth:'#54d8bc',skill:'#f3be78'};
const eventColors = {read:'#608ffa',write:'#f5a66d',delete:'#eb7887',
  learn:'#c4a0ff',growth:'#65e2b9',link:'#c0e7ff',update:'#aeb7d1'};
const state = {
  items:[], links:[], byId:new Map(), neighbours:new Map(), categories:[],
  count:{}, yaw:-0.38,pitch:-0.16,zoom:1,panX:0,panY:0,
  labels:true,showLinks:true,orbit:false,filter:null,selected:null,hovered:null,
  search:'',pointer:null,dragged:false,
  width:800,height:600,lastFrame:0,time:0,frame:0,lastSync:0,
  live:new Map(),particles:[],seen:new Set(),recentEvents:0,
  cursors:{audit:0,message:0},firstActivity:true,failed:false,
  requestRunning:false,graphLoaded:false,graphRefreshing:false
};
const groupAngles = {memory:0,dialog:1,story:2,learning:3,growth:4,skill:5};
function colorToRgb(hex){
  const val=parseInt(hex.slice(1),16);
  return {r:(val>>16)&255,g:(val>>8)&255,b:val&255};
}
function withAlpha(hex,a){
  const c=colorToRgb(hex);
  return 'rgba('+c.r+','+c.g+','+c.b+','+Math.max(0,Math.min(1,a))+')';
}
function hash(str){
  let x=2166136261;
  for(let i=0;i<str.length;i++){x^=str.charCodeAt(i);x=Math.imul(x,16777619);}
  return x>>>0;
}
function rnd(seed){seed=(Math.imul(seed^seed>>>15,1|seed))>>>0;
  seed^=seed+Math.imul(seed^seed>>>7,61|seed);
  return ((seed^seed>>>14)>>>0)/4294967296;}
function clamp(x,min,max){return Math.max(min,Math.min(max,x));}
function categoryBase(cat){
  const i=groupAngles[cat]??0,angle=(Math.PI*2*i/6)-1.3;
  const r=220,alt=Math.sin(i*2.46)*90;
  return {x:Math.cos(angle)*r,y:Math.sin(angle)*r*.83,z:alt};
}
function initialPosition(node,index,groupIndex){
  if(node.id==='core:soryuka')return {x:0,y:0,z:0};
  const base=categoryBase(node.category);
  if(node.kind==='cluster')return base;
  const h=hash(node.id),count=groupIndex||0;
  const theta=count*2.399963229728653+h%100*.007;
  const ring=42+Math.sqrt(count+1)*23;
  const altitude=(rnd(h+17)-.5)*160+Math.sin(theta*1.29)*ring*.23;
  return {
    x:base.x+Math.cos(theta)*ring,
    y:base.y+Math.sin(theta)*ring*.85,
    z:base.z+altitude
  };
}
function resize(){
  const rect=canvas.getBoundingClientRect();
  state.width=Math.max(1,rect.width);state.height=Math.max(1,rect.height);
  const w=Math.round(state.width*pixelRatio),h=Math.round(state.height*pixelRatio);
  if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;}
  if(ctx)ctx.setTransform(pixelRatio,0,0,pixelRatio,0,0);
}
function project(node){
  const p=node.position;
  const cy=Math.cos(state.yaw),sy=Math.sin(state.yaw);
  const cp=Math.cos(state.pitch),sp=Math.sin(state.pitch);
  const rx=p.x*cy-p.z*sy;
  const rz=p.x*sy+p.z*cy;
  const ry=p.y*cp-rz*sp;
  const depth=p.y*sp+rz*cp;
  const perspective=950/(950+depth);
  const scale=state.zoom*perspective;
  return {x:state.width*.51+state.panX+rx*scale,
          y:state.height*.49+state.panY+ry*scale,
          z:depth,scale,visible:depth>-860};
}
function normalizeGraph(graph){
  if(!graph||!Array.isArray(graph.nodes)||!Array.isArray(graph.links))throw Error('Неверный ответ карты памяти');
  const previous=state.byId,counts=new Map();
  state.items=graph.nodes.slice(0,1200).map(d=>{
    const i=counts.get(d.category)||0;
    counts.set(d.category,i+1);
    const old=previous.get(d.id);
    return {...d,position:old?.position||initialPosition(d,i),
      screen:{x:0,y:0,z:0,scale:1,visible:true}};
  });
  state.byId=new Map(state.items.map(n=>[n.id,n]));
  state.links=graph.links.filter(e=>state.byId.has(e.source)&&state.byId.has(e.target)).slice(0,2200);
  state.neighbours=new Map(state.items.map(n=>[n.id,new Set()]));
  for(const e of state.links){
    state.neighbours.get(e.source).add(e.target);
    state.neighbours.get(e.target).add(e.source);
  }
  state.categories=Array.isArray(graph.categories)?graph.categories:[];
  state.count=graph.stats?.by_category||{};
  $('stat-nodes').textContent=String(state.items.length);
  $('stat-links').textContent=String(state.links.length);
  $('stat-items').textContent=String(graph.stats?.items||0);
  if(!state.graphLoaded){
    state.cursors=graph.cursor||state.cursors;
    state.graphLoaded=true;
    state.firstActivity=true;
  }
  if(state.selected&&!state.byId.has(state.selected)){
    state.selected=null;
    $('details').hidden=true;
  }
  renderLegend();
  const itemCount=Number(graph.stats?.items||0);
  $('graph-loading').hidden=true;
  $('scene-status').textContent=itemCount>0
    ?'ПЕРЕТАСКИВАЙ, ЧТОБЫ ВРАЩАТЬ · КОЛЕСО — МАСШТАБ'
    :'ПАМЯТЬ ПОКА ПУСТА · ПОКАЗАНЫ РЕАЛЬНЫЕ ФАЙЛЫ КАНОНА И КАТЕГОРИИ';
  state.lastSync=Date.now();
  $('sync-age').textContent='ПОСЛЕДНЯЯ СИНХРОНИЗАЦИЯ: '+new Date().toLocaleTimeString('ru-RU');
}
function renderLegend(){
  const root=$('legend-categories');
  root.replaceChildren();
  for(const cat of state.categories){
    const b=document.createElement('button');
    b.type='button';
    b.className='category'+(state.filter===cat.id?' active':'')+(state.filter&&state.filter!==cat.id?' dim':'');
    b.setAttribute('aria-pressed',String(state.filter===cat.id));
    b.style.setProperty('--cat',palette[cat.id]||cat.color||'#8392b1');
    const dot=document.createElement('span');dot.className='category-dot';dot.setAttribute('aria-hidden','true');
    const name=document.createElement('span');name.textContent=cat.label||cat.id;
    const count=document.createElement('span');count.className='count';count.textContent=String(state.count[cat.id]||0);
    b.append(dot,name,count);
    b.addEventListener('click',()=>{
      state.filter=state.filter===cat.id?null:cat.id;
      renderLegend();ensureVisibleSelection();
    });
    root.append(b);
  }
}
function ensureVisibleSelection(){
  if(state.selected&&!nodeVisible(state.byId.get(state.selected))){state.selected=null;$('details').hidden=true;}
}
function nodeVisible(n){
  if(!n)return false;
  if(n.category==='core')return true;
  if(state.filter&&n.category!==state.filter)return false;
  if(state.search&&n.kind!=='cluster'){
    if(!n.name.toLocaleLowerCase('ru-RU').includes(state.search))return false;
  }
  if(state.search&&n.kind==='cluster')return false;
  return true;
}
function hoveredNode(){
  return state.byId.get(state.hovered||state.selected)||null;
}
function selectedSet(){
  const id=state.hovered||state.selected;
  if(!id)return null;
  return state.neighbours.get(id)||new Set();
}
function ease(t){return 1-Math.pow(1-t,3);}
function pulseNode(id,kind='read',duration=1600){
  const n=state.byId.get(id)||state.byId.get('core:soryuka');
  if(!n)return;
  const now=performance.now();
  state.live.set(n.id,{start:now,end:now+duration,color:eventColors[kind]||palette[n.category]});
}
function particleFor(from,to,kind){
  if(prefersReducedMotion)return;
  if(state.particles.length>50)state.particles.splice(0,10);
  if(state.byId.has(from)&&state.byId.has(to)&&from!==to){
    state.particles.push({from,to,kind,start:performance.now(),duration:820});
  }
}
function pulseEvent(ev){
  const targets=Array.isArray(ev.targets)?ev.targets:[];
  const available=targets.filter(x=>state.byId.has(x)).slice(0,8);
  const fallback='group:'+(ev.category||'memory');
  if(!available.length)available.push(state.byId.has(fallback)?fallback:'core:soryuka');
  for(let i=0;i<available.length;i++){
    const id=available[i];
    pulseNode(id,ev.kind,1550);
    const start=id.startsWith('turn:')?'group:dialog':'core:soryuka';
    particleFor(state.byId.has(start)?start:'core:soryuka',id,ev.kind);
  }
}
function renderScene(t){
  if(!ctx)return;
  const w=state.width,h=state.height;
  ctx.clearRect(0,0,w,h);
  ctx.fillStyle='#080a11';ctx.fillRect(0,0,w,h);
  const halo=ctx.createRadialGradient(w*.5,h*.5,15,w*.5,h*.5,Math.max(w,h)*.64);
  halo.addColorStop(0,'rgba(20,31,57,.8)');
  halo.addColorStop(.42,'rgba(9,16,34,.47)');
  halo.addColorStop(1,'rgba(5,7,15,.95)');
  ctx.fillStyle=halo;ctx.fillRect(0,0,w,h);
  const pulseTime=t/1900;
  ctx.strokeStyle='rgba(102,125,168,.045)';ctx.lineWidth=1;
  const rings=Math.min(w,h)*.17;
  for(let i=1;i<=3;i++){
    ctx.beginPath();ctx.ellipse(w*.51+state.panX,h*.49+state.panY,
      rings*i*.85*state.zoom,rings*i*.37*state.zoom,
      -.3,0,Math.PI*2);ctx.stroke();
  }
  // Project once, depth-sort to make distant nodes recede like a three-dimensional scene.
  const visible=[];
  for(const n of state.items){
    n.screen=project(n);
    if(n.screen.visible&&nodeVisible(n))visible.push(n);
  }
  visible.sort((a,b)=>b.screen.z-a.screen.z);
  const highlight=selectedSet();
  if(state.showLinks){
    for(const edge of state.links){
      const a=state.byId.get(edge.source),b=state.byId.get(edge.target);
      if(!a||!b||!a.screen.visible||!b.screen.visible||!nodeVisible(a)||!nodeVisible(b))continue;
      const focus=highlight&&(edge.source===state.selected||edge.target===state.selected
         ||edge.source===state.hovered||edge.target===state.hovered);
      const trans=highlight&&!focus?.12:1;
      const isReal=edge.relation!=='contains'&&edge.relation!=='category'&&edge.relation!=='message';
      ctx.beginPath();ctx.moveTo(a.screen.x,a.screen.y);ctx.lineTo(b.screen.x,b.screen.y);
      ctx.lineWidth=focus?1.7:isReal?.9:.6;
      ctx.strokeStyle=isReal?withAlpha('#a6b8f0',focus?.7:.23*trans):
        withAlpha(palette[a.category]||'#7b92bd',(focus?.44:.1)*trans);
      ctx.stroke();
    }
  }
  for(const n of visible){
    const p=n.screen,c=palette[n.category]||'#879bb4';
    const zFactor=clamp((950-p.z)/1180,.45,1.45);
    const rad=clamp(Number(n.size||4)*p.scale*.7*zFactor,1.5,18);
    const dim=highlight&&n.id!==state.selected&&n.id!==state.hovered&&!highlight.has(n.id);
    const alpha=dim?.25:1;
    if(n.kind==='cluster'||n.kind==='character'){
      const radius=n.kind==='character'?rad*4.5:rad*3.5;
      const g=ctx.createRadialGradient(p.x,p.y,0,p.x,p.y,radius);
      g.addColorStop(0,withAlpha(c,dim?.19:.35));
      g.addColorStop(.2,withAlpha(c,dim?.07:.13));
      g.addColorStop(1,withAlpha(c,0));
      ctx.fillStyle=g;ctx.beginPath();ctx.arc(p.x,p.y,radius,0,Math.PI*2);ctx.fill();
      if(n.kind==='character'){
        ctx.strokeStyle=withAlpha(c,.32*alpha);
        ctx.lineWidth=1;
        ctx.beginPath();ctx.arc(p.x,p.y,rad*1.7+Math.sin(pulseTime)*2,0,Math.PI*2);ctx.stroke();
      }
    }
    const flicker=(prefersReducedMotion?0:Math.sin(t/700+hash(n.id)%20)*.12);
    ctx.globalAlpha=alpha;
    ctx.beginPath();ctx.arc(p.x,p.y,rad,0,Math.PI*2);
    ctx.fillStyle=withAlpha(c,clamp(.73+flicker,.35,1));ctx.fill();
    ctx.beginPath();ctx.arc(p.x-rad*.3,p.y-rad*.3,Math.max(.8,rad*.32),0,Math.PI*2);
    ctx.fillStyle=withAlpha('#ffffff',n.kind==='character'?.88:.48);ctx.fill();
    if(n.id===state.selected||n.id===state.hovered){
      ctx.beginPath();ctx.arc(p.x,p.y,rad+6,0,Math.PI*2);
      ctx.strokeStyle=withAlpha(c,.8);ctx.lineWidth=1.3;ctx.stroke();
    }
    ctx.globalAlpha=1;
    const isLabel=n.kind==='cluster'||n.kind==='character'||n.id===state.selected||n.id===state.hovered;
    if(state.labels&&(isLabel||(visible.length<40&&n.kind!=='user'&&n.kind!=='assistant'))){
      const fontSize=n.kind==='character'?14:n.kind==='cluster'?11:10;
      ctx.font=(n.kind==='character'?'600 ':'')+fontSize+'px Segoe UI, Arial, sans-serif';
      ctx.textBaseline='middle';ctx.fillStyle=withAlpha('#d7dfee',dim?.22:n.kind==='character'?.98:.7);
      ctx.fillText(n.name.length>28?n.name.slice(0,27)+'…':n.name,p.x+rad+6,p.y-1);
    }
  }
  for(const [id,anim] of state.live){
    const n=state.byId.get(id);
    const progress=(t-anim.start)/(anim.end-anim.start);
    if(!n||progress>=1){state.live.delete(id);continue;}
    const p=n.screen;
    if(!p.visible||!nodeVisible(n))continue;
    const r=Number(n.size||4)*p.scale*(1+2.1*ease(progress))+4;
    ctx.lineWidth=2.6*(1-progress);
    ctx.strokeStyle=withAlpha(anim.color,.82*(1-progress));
    ctx.beginPath();ctx.arc(p.x,p.y,r,0,Math.PI*2);ctx.stroke();
  }
  state.particles=state.particles.filter(x=>t<x.start+x.duration);
  for(const part of state.particles){
    const a=state.byId.get(part.from),b=state.byId.get(part.to);
    if(!a||!b||!nodeVisible(a)||!nodeVisible(b))continue;
    const progress=clamp((t-part.start)/part.duration,0,1);
    const p=ease(progress),ax=a.screen.x,ay=a.screen.y,bx=b.screen.x,by=b.screen.y;
    const x=ax+(bx-ax)*p,y=ay+(by-ay)*p-Math.sin(p*Math.PI)*12;
    ctx.beginPath();ctx.arc(x,y,Math.max(1,3.6*(1-p*.65)),0,Math.PI*2);
    ctx.fillStyle=withAlpha(eventColors[part.kind]||'#f0c588',.95-progress*.38);ctx.fill();
  }
}
function animate(t){
  requestAnimationFrame(animate);
  if(document.hidden)return;
  const fps=prefersReducedMotion?12:30;
  if(t-state.lastFrame<1000/fps)return;
  const elapsed=Math.min(.055,(t-state.lastFrame)/1000||.03);
  state.lastFrame=t;state.time=t;
  if(state.orbit&&!state.pointer&&!prefersReducedMotion)state.yaw+=elapsed*.13;
  renderScene(t);
}
function positionFromEvent(e){
  const rect=canvas.getBoundingClientRect();
  return {x:e.clientX-rect.left,y:e.clientY-rect.top};
}
function findNode(x,y){
  let closest=null,min=Infinity;
  for(const n of state.items){
    if(!nodeVisible(n)||!n.screen.visible)continue;
    const dx=n.screen.x-x,dy=n.screen.y-y;
    const distance=dx*dx+dy*dy;
    const radius=Math.max(9,Math.min(22,n.size*n.screen.scale+6));
    if(distance<radius*radius&&distance<min){min=distance;closest=n;}
  }
  return closest;
}
function showTooltip(node,e){
  const el=$('node-tooltip');
  if(!node){el.hidden=true;return;}
  el.replaceChildren();
  const title=document.createElement('strong');title.textContent=node.name;
  const subtitle=document.createElement('small');
  subtitle.textContent=(node.kind||'')+' · '+(node.status||'Локально');
  el.append(title,subtitle);el.hidden=false;
  const x=clamp(e.clientX+17,8,window.innerWidth-260);
  const y=clamp(e.clientY+14,8,window.innerHeight-86);
  el.style.left=x+'px';el.style.top=y+'px';
}
function selectNode(node){
  state.selected=node?.id||null;
  const box=$('details');
  if(!node){box.hidden=true;return;}
  $('detail-name').textContent=node.name;
  $('detail-kind').textContent=node.kind||'узел';
  $('detail-status').textContent=node.status||'локально';
  $('detail-description').textContent=node.detail||'Источник: локальная SQLite';
  box.hidden=false;
  pulseNode(node.id,'read',900);
}
function setButton(id,value){
  const b=$(id);b.classList.toggle('active',value);b.setAttribute('aria-pressed',String(value));
}
async function jsonApi(path){
  const response=await fetch('/api'+path,{cache:'no-store'});
  if(!response.ok)throw Error('Локальный API: '+response.status);
  return response.json();
}
async function refreshGraph(){
  if(state.graphRefreshing)return;
  state.graphRefreshing=true;
  try{
    const graph=await jsonApi('/mirror/graph');
    normalizeGraph(graph);
    state.failed=false;
    $('connection').textContent='СВЯЗЬ ЕСТЬ';
    $('connection').parentElement.classList.remove('offline');
    $('feed-status-dot').classList.remove('warn');
    $('feed-status-text').textContent='Данные из локальной SQLite';
    if(state.firstActivity)await pollActivity(true);
  }catch(error){
    state.failed=true;
    $('connection').textContent='НЕТ СВЯЗИ';
    $('connection').parentElement.classList.add('offline');
    $('feed-status-dot').classList.add('warn');
    $('feed-status-text').textContent='Сервер недоступен: '+error.message;
    $('graph-loading').hidden=false;
    $('graph-loading').textContent='Не удалось подключиться к SQLite. Открой Сорюку через START-SORYUKA.bat.';
  }finally{state.graphRefreshing=false;}
}
function eventTime(utc){
  const d=new Date(utc);
  return Number.isNaN(d.getTime())?'—':d.toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
}
function eventElement(ev){
  const root=document.createElement('div');root.className='event';root.dataset.kind=ev.kind;
  const dot=document.createElement('div');dot.className='event-symbol';dot.setAttribute('aria-hidden','true');
  const text=document.createElement('div');text.className='event-text';
  const heading=document.createElement('strong');heading.textContent=ev.title||'Изменение записи';
  const detail=document.createElement('small');
  detail.textContent=eventTime(ev.at)+' · '+(ev.category||'система').toUpperCase();
  text.append(heading,detail);root.append(dot,text);
  root.addEventListener('click',()=>{
    const target=(ev.targets||[]).find(x=>state.byId.has(x));
    if(target)selectNode(state.byId.get(target));
  });
  return root;
}
function addEvents(items,animateNew){
  const feed=$('event-feed');
  for(const ev of items){
    if(state.seen.has(ev.id))continue;
    state.seen.add(ev.id);
    if(state.seen.size>500){state.seen=new Set([...state.seen].slice(-360));}
    if(feed.querySelector('.feed-empty'))feed.replaceChildren();
    feed.prepend(eventElement(ev));state.recentEvents++;
    if(animateNew)pulseEvent(ev);
  }
  while(feed.children.length>65)feed.lastElementChild.remove();
  $('event-count').textContent=String(state.recentEvents);
}
async function pollActivity(initial=false){
  if(!state.graphLoaded||state.requestRunning)return;
  state.requestRunning=true;
  try{
    const a=initial?Math.max(0,state.cursors.audit-22):state.cursors.audit;
    const m=initial?Math.max(0,state.cursors.message-24):state.cursors.message;
    const result=await jsonApi('/mirror/activity?audit_after='+a+'&message_after='+m);
    const evs=Array.isArray(result.items)?result.items:[];
    const newCursors=result.cursor||state.cursors;
    state.cursors={audit:Math.max(state.cursors.audit,Number(newCursors.audit)||0),
                   message:Math.max(state.cursors.message,Number(newCursors.message)||0)};
    addEvents(evs,!initial);
    state.firstActivity=false;
    state.failed=false;
    $('feed-status-dot').classList.remove('warn');
    $('feed-status-text').textContent='Активность SQLite · синхронизировано '+new Date().toLocaleTimeString('ru-RU');
    if(!initial&&evs.length)void refreshGraph();
  }catch(error){
    $('feed-status-dot').classList.add('warn');
    $('feed-status-text').textContent='Ожидание локального API…';
  }finally{state.requestRunning=false;}
}
function wireEvents(){
  canvas.addEventListener('contextmenu',e=>e.preventDefault());
  canvas.addEventListener('pointerdown',e=>{
    canvas.setPointerCapture(e.pointerId);
    const p=positionFromEvent(e);
    state.pointer={id:e.pointerId,x:p.x,y:p.y,lastX:p.x,lastY:p.y,button:e.button};
    state.dragged=false;
    $('node-tooltip').hidden=true;
  });
  canvas.addEventListener('pointermove',e=>{
    const p=positionFromEvent(e);
    if(state.pointer&&state.pointer.id===e.pointerId){
      const dX=p.x-state.pointer.lastX,dY=p.y-state.pointer.lastY;
      state.pointer.lastX=p.x;state.pointer.lastY=p.y;
      if(Math.abs(p.x-state.pointer.x)+Math.abs(p.y-state.pointer.y)>5)state.dragged=true;
      if(state.dragged){
        if(state.pointer.button===2||e.shiftKey){state.panX+=dX;state.panY+=dY;}
        else{state.yaw+=dX*.006;state.pitch=clamp(state.pitch+dY*.006,-1.45,1.45);}
      }
      return;
    }
    const n=findNode(p.x,p.y);
    state.hovered=n?.id||null;
    canvas.style.cursor=n?'pointer':'grab';
    showTooltip(n,e);
  });
  function finish(e){
    if(!state.pointer||state.pointer.id!==e.pointerId)return;
    const p=positionFromEvent(e);
    if(!state.dragged&&state.pointer.button===0){
      const n=findNode(p.x,p.y);selectNode(n);
    }
    state.pointer=null;
  }
  canvas.addEventListener('pointerup',finish);
  canvas.addEventListener('pointercancel',()=>{state.pointer=null;});
  canvas.addEventListener('pointerleave',()=>{state.hovered=null;$('node-tooltip').hidden=true;});
  canvas.addEventListener('wheel',e=>{
    e.preventDefault();
    state.zoom=clamp(state.zoom*Math.exp(-e.deltaY*.00115),.24,3.3);
  },{passive:false});
  canvas.addEventListener('keydown',e=>{
    if(e.key==='+'||e.key==='='){state.zoom=clamp(state.zoom*1.15,.24,3.3);e.preventDefault();}
    if(e.key==='-'){state.zoom=clamp(state.zoom*.87,.24,3.3);e.preventDefault();}
    if(e.key==='Escape'){selectNode(null);e.preventDefault();}
    if(e.key==='ArrowLeft'||e.key==='ArrowRight'){state.yaw+=(e.key==='ArrowLeft'?-.1:.1);e.preventDefault();}
  });
  $('search').addEventListener('input',e=>{
    state.search=String(e.target.value||'').trim().toLocaleLowerCase('ru-RU');
    state.hovered=null;$('node-tooltip').hidden=true;
    if(state.search){state.filter=null;renderLegend();}
    ensureVisibleSelection();
    if(state.search){
      const result=state.items.find(x=>x.kind!=='cluster'&&x.kind!=='character'&&nodeVisible(x));
      if(result)selectNode(result);
    }
  });
  $('show-labels').addEventListener('click',()=>{state.labels=!state.labels;setButton('show-labels',state.labels);});
  $('show-links').addEventListener('click',()=>{state.showLinks=!state.showLinks;setButton('show-links',state.showLinks);});
  $('auto-orbit').addEventListener('click',()=>{state.orbit=!state.orbit;setButton('auto-orbit',state.orbit);});
  $('pulse-test').addEventListener('click',()=>{
    // Explicit demo pulse, deliberately not logged as a real memory access.
    const visible=state.items.filter(x=>x.kind!=='cluster'&&x.kind!=='character'&&nodeVisible(x));
    if(!visible.length){pulseNode('core:soryuka','read');return;}
    const node=visible[Math.floor(Math.random()*visible.length)];
    pulseNode(node.id,'read',1800);particleFor('core:soryuka',node.id,'read');
  });
  $('fit-graph').addEventListener('click',()=>{state.yaw=-.38;state.pitch=-.16;state.zoom=1;state.panX=0;state.panY=0;});
  $('clear-filter').addEventListener('click',()=>{
    state.filter=null;state.search='';$('search').value='';renderLegend();
  });
  $('close-details').addEventListener('click',()=>selectNode(null));
  $('focus-selected').addEventListener('click',()=>{
    const n=state.byId.get(state.selected);
    if(!n)return;
    state.search='';state.filter=null;$('search').value='';renderLegend();
    const p=project(n);
    state.panX+=state.width*.51-p.x;state.panY+=state.height*.49-p.y;
    state.zoom=Math.max(state.zoom,1.12);
    state.showLinks=true;setButton('show-links',true);
  });
  $('feed-open').addEventListener('click',()=>$('feed-panel').classList.add('open'));
  $('feed-close').addEventListener('click',()=>$('feed-panel').classList.remove('open'));
  window.addEventListener('resize',resize);
  window.addEventListener('keydown',e=>{
    if(e.key==='Escape'){$('feed-panel').classList.remove('open');$('node-tooltip').hidden=true;}
  });
}
function start(){
  if(!ctx){
    $('graph-loading').textContent='Canvas недоступен в этом браузере';
    return;
  }
  resize();
  wireEvents();
  requestAnimationFrame(animate);
  void refreshGraph();
  setInterval(()=>{if(!document.hidden)void pollActivity(false);},3000);
  setInterval(()=>{if(!document.hidden)void refreshGraph();},15000);
  document.addEventListener('visibilitychange',()=>{
    if(!document.hidden){void refreshGraph();void pollActivity(false);}
  });
}
start();
