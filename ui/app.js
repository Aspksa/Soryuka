// Soryuka 5.0 — TypeScript-compatible browser source, checked-in JS build (no dependencies).
// App and API data remain on loopback. Never put the Cloud.ru key in client code.
const byId = id => document.getElementById(id);
const state = {session:localStorage.getItem('soryuka.session')||'',emotion:'serene',gesture:'still',busy:false,memoryNames:new Map(),episodePreview:false};
const $messages=byId('messages');
const $input=byId('message');

async function api(path,data){
  const response=await fetch('/api'+path,{method:data===undefined?'GET':'POST',
    headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});
  let json;
  try{json=await response.json();}catch{throw Error('Некорректный ответ сервера');}
  if(!response.ok)throw Error(json.error||'Ошибка запроса '+response.status);
  return json;
}
function notify(text,isError=false){
  const el=byId('status');el.textContent=text;el.classList.toggle('error',isError);
}
function message(role,body){
  const node=document.createElement('div');node.className='message '+role;
  const who=document.createElement('span');who.className='who';who.textContent=role==='user'?'Хозяин':'Сорюка';
  const value=document.createElement('div');value.textContent=body;
  node.append(who,value);$messages.append(node);$messages.scrollTop=$messages.scrollHeight;
}
function showEmpty(){
  $messages.replaceChildren();
  const p=document.createElement('div');p.className='placeholder';
  const strong=document.createElement('strong');strong.textContent='Мой хозяин, я готова к беседе.';
  const line=document.createElement('p');line.textContent='Моя личность и наша история — рядом. Что создадим сегодня?';
  p.append(strong,line);$messages.append(p);
}
function clearPlaceholder(){
  const p=$messages.querySelector('.placeholder');if(p)p.remove();
}
function textItem(text,details,actions=[]){
  const node=document.createElement('div');node.className='item';
  const top=document.createElement('div');top.textContent=text;node.append(top);
  if(details){const caption=document.createElement('small');caption.textContent=details;node.append(caption);}
  actions.forEach(a=>{
    const btn=document.createElement('button');btn.type='button';btn.textContent=a.label;
    btn.addEventListener('click',async()=>{btn.disabled=true;try{await a.run();}catch(e){notify(e.message,true);}finally{btn.disabled=false;}});
    node.append(btn);
  });
  return node;
}
function renderItems(id,items,render){
  const root=byId(id);root.replaceChildren();if(!items.length){
    const empty=document.createElement('p');empty.textContent='Пока пусто';empty.className='item';root.append(empty);return;
  }
  items.forEach(row=>root.append(render(row)));
}
async function loadSessions(){
  if(!state.session){const x=await api('/session',{});state.session=x.session_id;state.episodePreview=false;localStorage.setItem('soryuka.session',state.session);}
  try{
    const result=await api('/history?session_id='+encodeURIComponent(state.session));
    if(result.items.length){$messages.replaceChildren();result.items.forEach(x=>message(x.role,x.body));}
    else showEmpty();
  }catch{
    state.session=(await api('/session',{})).session_id;
    state.episodePreview=false;
    localStorage.setItem('soryuka.session',state.session);
    showEmpty();
  }
}
async function sendMessage(event){
  event.preventDefault();
  const value=$input.value.trim();
  if(!value||state.busy)return;
  state.busy=true;byId('send').disabled=true;
  clearPlaceholder();message('user',value);$input.value='';
  notify('Сорюка отвечает…');
  try{
    const result=await api('/chat',{session_id:state.session,message:value,intent:byId('intent').value});
    message('assistant',result.answer);
    showFactSuggestions(result.memory_suggestions||[]);
    state.emotion=result.avatar.emotion;state.gesture=result.avatar.gesture;
    byId('emotional-state').textContent=({serene:'Спокойствие',care:'Нежность',play:'Игривость',solemn:'Величие',focus:'Сосредоточенность',concern:'Внимание'})[state.emotion]||state.emotion;
    notify('Cloud.ru · Сорюка на связи');
  }catch(e){message('assistant','Не удалось получить ответ: '+e.message);notify(e.message,true);}
  finally{state.busy=false;byId('send').disabled=false;$input.focus();}
}
async function loadMemories(){
  const d=await api('/memories');
  populateV4Memories(d.items);
  renderItems('memories',d.items,row=>textItem(row.body,row.kind,[{label:'Удалить',run:async()=>{
    if(!confirm('Удалить это воспоминание?'))return;
    await api('/memory/delete',{id:row.id});await loadMemories();
  }}]));
}
async function loadGoals(){
  const d=await api('/goals');
  populateV4Goals(d.items);
  renderItems('goals',d.items,row=>{
    const actions=[];
    if(row.status==='proposed'){
      actions.push({label:'Одобрить',run:async()=>{await api('/goal/action',{id:row.id,action:'approve'});await loadGoals();}});
      actions.push({label:'Отклонить',run:async()=>{await api('/goal/action',{id:row.id,action:'reject'});await loadGoals();}});
    }
    if(row.status==='approved')actions.push({label:'Выполнено',run:async()=>{
      const evidence=prompt('Укажи подтверждение результата (тест, выполненная задача):');
      if(!evidence)return;
      await api('/goal/action',{id:row.id,action:'complete',evidence});await loadGoals();
    }});
    return textItem(row.title,row.skill+' · '+row.status+' · критерий: '+row.criterion,actions);
  });
}
async function loadMetrics(){
  const d=await api('/metrics');
  renderItems('metrics',d.skills,row=>textItem(row.skill,'Оценок: '+row.rating_count+' · Средняя оценка владельца: '+row.mean_owner_rating+'/5'));
  renderItems('feedback-records',d.feedback,row=>textItem(row.note,row.skill+' · '+row.rating+'/5',[{label:'Удалить оценку',run:async()=>{
    if(!confirm('Удалить эту оценку?'))return;
    await api('/feedback/delete',{id:row.id});await loadMetrics();
  }}]));
}
async function loadRecommendations(){
  const d=await api('/recommendations');
  renderItems('recommendations',d.items,row=>textItem(row.title,row.skill+' · '+row.reason+' · Проверка: '+row.criterion,[{
    label:'Предложить эту цель',run:async()=>{
      await api('/goal',{skill:row.skill,title:row.title,criterion:row.criterion});
      await loadGoals();await loadRecommendations();
    }
  }]));
}
async function loadDiary(){
  const d=await api('/diary');
  renderItems('diary',d.items.slice(0,15),row=>textItem(row.action,row.created_at+' · '+row.subject));
}
async function loadProposals(){
  const d=await api('/proposals');
  renderItems('proposals',d.items,row=>{
    const actions=[];
    if(row.status==='pending'){
      actions.push({label:'Одобрить',run:async()=>{await api('/proposal/action',{id:row.id,action:'approve'});await loadProposals();}});
      actions.push({label:'Отклонить',run:async()=>{await api('/proposal/action',{id:row.id,action:'reject'});await loadProposals();}});
    }
    if(row.status==='approved')actions.push({label:'Откатить',run:async()=>{
      await api('/proposal/action',{id:row.id,action:'rollback'});await loadProposals();
    }});
    return textItem(row.field+' → '+JSON.parse(row.after_json),row.status+' · '+row.reason,actions);
  });
}
async function loadTimeline(){
  const d=await api('/timeline');
  renderItems('timeline',d.items,row=>textItem(row.body,row.fiction?'Сюжетный эпизод':'Реальное событие',[{label:'Удалить',run:async()=>{
    if(!confirm('Удалить событие из хронологии?'))return;
    await api('/timeline/delete',{id:row.id});await loadTimeline();
  }}]));
}

function fillSelect(id,rows,renderLabel){
  const select=byId(id),prior=select.value;
  select.replaceChildren();
  for(const r of rows){
    const option=document.createElement('option');
    option.value=r.id; option.textContent=renderLabel(r);
    select.append(option);
  }
  if(rows.some(x=>x.id===prior))select.value=prior;
}
function populateV4Memories(rows){
  state.memoryNames=new Map(rows.map(x=>[x.id,x.body]));
  for(const id of ['v4-link-a','v4-link-b','v4-review-id','v4-evidence-id','v5-cause','v5-effect']){
    fillSelect(id,rows,x=>x.body.slice(0,90)+' · '+x.kind);
  }
}
function populateV4Goals(rows){
  fillSelect('v4-assessment-goal',rows.filter(x=>x.status==='approved'),
    x=>x.skill+' · '+x.title.slice(0,80));
}
async function v4Search(){
  const q=byId('v4-search').value.trim();
  if(!q)return;
  const r=await api('/memory/search?q='+encodeURIComponent(q));
  renderItems('v4-search-results',r.items,x=>textItem(x.body,
    x.kind+' · локальное сходство: '+Math.round(x.score*100)+'% · '+x.provenance));
}
async function v4Links(){
  const r=await api('/memory/graph');
  renderItems('v4-links',r.items,x=>textItem(
    (state.memoryNames.get(x.source_id)||x.source_id)+' ↔ '+(state.memoryNames.get(x.target_id)||x.target_id),
    x.relation,[{label:'Убрать связь',run:async()=>{
      if(!confirm('Удалить подтверждённую связь?'))return;
      await api('/memory/unlink',x);await v4Links();
    }}]));
}
async function v4Review(){
  const id=byId('v4-review-id').value;
  if(!id)return notify('Сначала добавь воспоминания',true);
  const r=await api('/memory/review?id='+encodeURIComponent(id));
  renderItems('v4-review-results',r.items,x=>{
    const actions=x.kind==='possible_duplicate'?[{label:'Объединить после проверки',run:async()=>{
      if(!confirm('Оставить выбранную запись и удалить похожую? Это действие нельзя отменить.'))return;
      await api('/memory/merge',{keep_id:id,remove_id:x.memory_id,confirmed:true});
      await loadMemories();await v4Links();await v4Review();
    }}]:[];
    return textItem(x.body,x.kind==='possible_conflict'
      ?'Возможно противоречие — проверь оба утверждения вручную'
      :'Возможно совпадение · сходство '+Math.round(x.similarity*100)+'%',actions);
  });
}
async function v4Chronology(){
  const d=await api('/memory/chronology');
  renderItems('v4-chronology',d.items,x=>textItem(x.body,
    (x.fiction?'Художественное событие · ':'')+x.category+' · '+x.created_at+' · '+x.source));
}
async function v4Map(){
  const d=await api('/academy/map');
  renderItems('v4-skill-map',d.items,x=>{
    const counts=x.goals||{},checks=x.reference_checks||{};
    const rating=x.owner_ratings?x.owner_ratings.mean_owner_rating+'/5 из '+x.owner_ratings.rating_count+' оценок':'нет оценок';
    return textItem(x.skill,'Предложено: '+(counts.proposed||0)+' · Одобрено: '+(counts.approved||0)+
      ' · Выполнено: '+(counts.completed||0)+' · Эталонные совпадения: '+(checks.passed||0)+'/'+(checks.tests||0)+
      ' · Оценки хозяина: '+rating);
  });
}
async function v4Assessments(){
  const d=await api('/academy/assessments');
  renderItems('v4-assessments',d.items,x=>textItem(x.question,
    (x.passed?'Совпало с эталоном':'Не совпало с эталоном')+' · '+x.skill+
    (x.review?' · Разбор: '+x.review:'')));
}
async function v4Strategies(){
  const d=await api('/academy/strategies');
  fillSelect('v5-transfer-strategy',d.items.filter(x=>x.status==='approved'),
    x=>x.skill+' · '+x.name.slice(0,75));
  renderItems('v4-strategies',d.items,x=>{
    const actions=[];
    if(x.status==='proposed'){
      actions.push({label:'Одобрить как идею',run:async()=>{await api('/academy/strategy/action',{id:x.id,action:'approve'});await v4Strategies();}});
      actions.push({label:'Отклонить',run:async()=>{await api('/academy/strategy/action',{id:x.id,action:'reject'});await v4Strategies();}});
    }
    if(x.status==='approved')actions.push({label:'Отозвать одобрение',run:async()=>{
      await api('/academy/strategy/action',{id:x.id,action:'rollback'});await v4Strategies();
    }});
    return textItem(x.name,x.skill+' · '+x.status+' · Было '+x.baseline+'% → '+x.trial+
      '% (предоставлено хозяином, не проверено автоматически) · '+x.evidence,actions);
  });
}
async function v4Council(){
  const d=await api('/academy/council');
  renderItems('v4-council',d.items,x=>textItem(x.title,x.skill+' · '+x.reason+' · '+x.criterion,[{
    label:'Предложить цель',run:async()=>{
      await api('/goal',{skill:x.skill,title:x.title,criterion:x.criterion});
      await loadGoals();await v4Council();
    }
  }]));
}
async function v4Diary(){
  const d=await api('/academy/diary');
  renderItems('v4-diary',d.items,x=>textItem(x.action,x.created_at));
}
function setupV4(){
  bindForm('v4-search-form',v4Search);
  bindForm('v4-link-form',async()=>{
    await api('/memory/link',{source_id:byId('v4-link-a').value,target_id:byId('v4-link-b').value,
      relation:byId('v4-link-type').value,confirmed:byId('v4-link-confirm').checked});
    await v4Links();
  });
  byId('v4-review').addEventListener('click',()=>v4Review().catch(e=>notify(e.message,true)));
  bindForm('v4-provenance-form',async()=>{
    await api('/memory/provenance',{id:byId('v4-evidence-id').value,
      status:byId('v4-evidence-status').value,source_note:byId('v4-evidence-note').value,
      confirmed:byId('v4-evidence-confirm').checked});
    await loadMemories();
  });
  byId('v4-chronology-refresh').addEventListener('click',()=>v4Chronology().catch(e=>notify(e.message,true)));
  byId('v4-map-refresh').addEventListener('click',()=>v4Map().catch(e=>notify(e.message,true)));
  bindForm('v4-assessment-form',async()=>{
    const x=await api('/academy/assessment',{goal_id:byId('v4-assessment-goal').value,
      question:byId('v4-assessment-question').value,reference:byId('v4-assessment-reference').value,
      answer:byId('v4-assessment-answer').value,review:byId('v4-assessment-review').value,
      confirmed:byId('v4-assessment-confirm').checked});
    notify(x.passed?'Ответ совпал с эталоном':'Ответ не совпал с эталоном');
    await Promise.all([v4Assessments(),v4Map()]);
  });
  bindForm('v4-strategy-form',async()=>{
    await api('/academy/strategy',{skill:byId('v4-strategy-skill').value,
      name:byId('v4-strategy-name').value,baseline:Number(byId('v4-baseline').value),
      trial:Number(byId('v4-trial').value),evidence:byId('v4-strategy-evidence').value,
      confirmed:byId('v4-strategy-confirm').checked});
    await v4Strategies();
  });
  byId('v4-council-refresh').addEventListener('click',()=>v4Council().catch(e=>notify(e.message,true)));
  byId('v4-diary-refresh').addEventListener('click',()=>v4Diary().catch(e=>notify(e.message,true)));
}


function showFactSuggestions(items){
  renderItems('v5-fact-suggestions',items,x=>textItem(x.text,
    'Предложение, не сохранено. Сорюка не объявляет это установленным фактом.',[{
    label:'Сохранить с моим разрешением',run:async()=>{
      if(!confirm('Сохранить это личное утверждение в локальную память?'))return;
      await api('/memory',{kind:x.kind,text:x.text,confirmed:true});
      await loadMemories();
      notify('Воспоминание сохранено с твоим разрешением');
    }
  }]));
}
async function v5Permissions(){
  const r=await api('/v5/permissions');
  byId('v5-background').checked=!!r.items.background_local;
  byId('v5-capture').checked=!!r.items.fact_suggestions;
  byId('v5-embedding').checked=!!r.items.cloud_embeddings;
}
async function v5EpisodePreview(){
  if(!state.session)return;
  const d=await api('/v5/episode/preview?session_id='+encodeURIComponent(state.session));
  state.episodePreview=true;
  renderItems('v5-episode',[d],x=>textItem(x.digest,
    x.message_count+' сообщений · дословные фрагменты, не новая биография'));
}
async function v5Episodes(){
  const d=await api('/v5/episodes');
  renderItems('v5-episode-list',d.items.slice(0,15),x=>textItem(x.digest,
    'Источник: разговор '+x.session_id+' · '+x.source_messages+' сообщений',[{
    label:'Удалить сводку',run:async()=>{
      if(!confirm('Удалить эту сохранённую сводку без восстановления?'))return;
      await api('/v5/episode/delete',{id:x.id,confirmed:true});
      await v5Episodes();
    }
  }]));
}
async function v5Causal(){
  const d=await api('/v5/causal');
  renderItems('v5-causal-links',d.items,x=>textItem(
    (state.memoryNames.get(x.cause_id)||x.cause_id)+' → '+(state.memoryNames.get(x.effect_id)||x.effect_id),
    'Источник обоснования (со слов хозяина): '+x.evidence_note));
}
async function v5Growth(){
  const [tasks,reviews]=await Promise.all([api('/v5/growth/tasks'),api('/v5/growth/reviews')]);
  renderItems('v5-growth-tasks',tasks.items.slice(0,18),
    x=>textItem(x.kind+' · '+x.status,'Попыток: '+x.attempts+
       (x.result_json?' · '+JSON.parse(x.result_json).count+' предложений':'')));
  renderItems('v5-growth-proposals',reviews.items.filter(x=>x.status==='pending').slice(0,20),
    x=>textItem(x.kind,JSON.stringify(x.candidate),[
      {label:'Изучить позже',run:async()=>{await api('/v5/growth/review',{id:x.id,action:'accept'});await v5Growth();}},
      {label:'Отклонить',run:async()=>{await api('/v5/growth/review',{id:x.id,action:'dismiss'});await v5Growth();}}
    ]));
}
async function v5Plan(){
  const [plan,diary]=await Promise.all([api('/v5/growth/plan'),api('/v5/growth/diary')]);
  renderItems('v5-plan',plan.items,x=>textItem(x.kind,x.reason+' · '+x.status));
  renderItems('v5-diary',diary.items.slice(0,20),x=>textItem(x.event,x.at));
}
async function v5Transfers(){
  const d=await api('/v5/skill/transfers');
  renderItems('v5-transfers',d.items.slice(0,20),x=>{
    const actions=[];
    if(x.status==='pending'){
      actions.push({label:'Одобрить запись',run:async()=>{
        await api('/v5/skill/transfer/decision',{id:x.id,action:'approve'});await v5Transfers();
      }},{label:'Отклонить',run:async()=>{
        await api('/v5/skill/transfer/decision',{id:x.id,action:'reject'});await v5Transfers();
      }});
    } else if(x.status==='approved'){
      actions.push({label:'Отозвать',run:async()=>{
        await api('/v5/skill/transfer/decision',{id:x.id,action:'revoke'});await v5Transfers();
      }});
    }
    return textItem(x.from_skill+' → '+x.to_skill,
      x.status+' · '+x.rationale+' · нет изменения облачной модели',actions);
  });
}
function setupV5(){
  byId('v5-save-permissions').addEventListener('click',async()=>{
    if(!confirm('Сохранить разрешения? Включённый фон будет выполнять только локальные задания. Embeddings могут стоить денег при отдельном ручном запросе.'))return;
    try{
      const settings=[['background_local','v5-background'],['fact_suggestions','v5-capture'],
                      ['cloud_embeddings','v5-embedding']];
      for(const [name,id] of settings)
        await api('/v5/permissions',{name,enabled:byId(id).checked,confirmed:true});
      await v5Permissions();notify('Разрешения хозяина сохранены');
    }catch(e){notify(e.message,true);}
  });
  byId('v5-episode-preview').addEventListener('click',()=>v5EpisodePreview().catch(e=>notify(e.message,true)));
  byId('v5-episode-save').addEventListener('click',async()=>{
    if(!state.episodePreview)return notify('Сначала прочитай сводку',true);
    if(!confirm('Сохранить эту дословную сводку диалога локально?'))return;
    try{
      await api('/v5/episode/save',{session_id:state.session,confirmed:true});
      state.episodePreview=false;await v5Episodes();notify('Сводка сохранена с источником');
    }catch(e){notify(e.message,true);}
  });
  bindForm('v5-evidence-form',async()=>{
    const q=byId('v5-question').value;
    const [d,evidence]=await Promise.all([
      api('/v5/reason?q='+encodeURIComponent(q)),
      api('/v5/evidence?q='+encodeURIComponent(q))
    ]);
    const root=byId('v5-evidence');
    renderItems('v5-evidence',d.questions,x=>textItem(x.question,
      x.confidence_label+' · подтверждённых записей: '+x.evidence_count+
      ' · источники: '+x.source_ids.join(', ')));
    evidence.sources.forEach(x=>root.append(textItem(x.body,
      'Источник '+x.id+' · '+x.provenance+' · переходов по связям: '+x.hop)));
  });
  bindForm('v5-causal-form',async()=>{
    await api('/v5/causal',{cause_id:byId('v5-cause').value,
      effect_id:byId('v5-effect').value,evidence:byId('v5-causal-evidence').value,
      confirmed:byId('v5-causal-confirm').checked});
    await v5Causal();
  });
  byId('v5-index-embeddings').addEventListener('click',async()=>{
    if(!confirm('Выполнить до 2 Cloud.ru embeddings-запросов? Это может стоить денег. Продолжить?'))return;
    try{
      const d=await api('/v5/embeddings/index',{confirmed:true,limit:2});
      notify('Индексировано: '+d.indexed+' · из кэша: '+d.cache_hits);
    }catch(e){notify(e.message,true);}
  });
  bindForm('v5-vector-form',async()=>{
    if(!confirm('Отправить запрос в Cloud.ru для смыслового поиска? Возможна оплата.'))return;
    const d=await api('/v5/embeddings/search',{confirmed:true,query:byId('v5-vector-q').value});
    renderItems('v5-vectors',d.items,x=>textItem(x.body,
      'Сходство векторов: '+Math.round(x.score*100)+'% · не гарантия истинности'));
  });
  byId('v5-refresh-growth').addEventListener('click',()=>v5Growth().catch(e=>notify(e.message,true)));
  byId('v5-seed-growth').addEventListener('click',async()=>{
    if(!confirm('Поставить в очередь три локальные проверки? Они не отправляют данные в Cloud.ru.'))return;
    try{
      for(const kind of ['memory_review','learning_exercises','growth_recommendations'])
        await api('/v5/growth/enqueue',{kind,confirmed:true});
      await v5Growth();notify('Локальные задачи добавлены');
    }catch(e){notify(e.message,true);}
  });
  byId('v5-run-growth').addEventListener('click',async()=>{
    try{
      const d=await api('/v5/growth/run',{});
      await v5Growth();notify('Локальных заданий обработано: '+d.processed+' · '+d.status);
    }catch(e){notify(e.message,true);}
  });
  byId('v5-plan-refresh').addEventListener('click',()=>v5Plan().catch(e=>notify(e.message,true)));
  bindForm('v5-transfer-form',async()=>{
    await api('/v5/skill/transfer',{from_skill:byId('v5-transfer-from').value,
      to_skill:byId('v5-transfer-to').value,strategy_id:byId('v5-transfer-strategy').value,
      rationale:byId('v5-transfer-rationale').value});
    await v5Transfers();
  });
}

function bindForm(id,handle){
  byId(id).addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget;
    const btn=form.querySelector('button[type=submit]');
    btn.disabled=true;
    try{await handle();form.reset();notify('Изменение сохранено локально');}
    catch(e){notify(e.message,true);}
    finally{btn.disabled=false;}
  });
}
function setupForms(){
  byId('chat-form').addEventListener('submit',sendMessage);
  $input.addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();byId('chat-form').requestSubmit();}});
  byId('new-chat').addEventListener('click',async()=>{
    try{state.session=(await api('/session',{})).session_id;state.episodePreview=false;localStorage.setItem('soryuka.session',state.session);showEmpty();notify('Новый разговор');}catch(e){notify(e.message,true);}
  });
  byId('delete-chat').addEventListener('click',async()=>{
    if(!state.session||!confirm('Удалить весь текущий разговор без восстановления?'))return;
    try{await api('/session/delete',{id:state.session});
        state.session=(await api('/session',{})).session_id;
        localStorage.setItem('soryuka.session',state.session);showEmpty();notify('Разговор удалён');}
    catch(e){notify(e.message,true);}
  });
  byId('backup').addEventListener('click',async()=>{
    if(!confirm('Создать резервную копию базы SQLite на локальном диске?'))return;
    try{const result=await api('/backup',{confirmed:true});notify('Копия создана: '+result.filename);}
    catch(e){notify(e.message,true);}
  });
  bindForm('memory-form',async()=>{
    await api('/memory',{kind:byId('memory-kind').value,text:byId('memory-text').value,confirmed:byId('memory-confirm').checked});
    await loadMemories();
  });
  byId('suggest-goals').addEventListener('click',async()=>{
    try{await loadRecommendations();notify('Предложения рассчитаны только из твоих оценок');}
    catch(e){notify(e.message,true);}
  });
  bindForm('goal-form',async()=>{
    await api('/goal',{skill:byId('goal-skill').value,title:byId('goal-title').value,criterion:byId('goal-criterion').value});
    await loadGoals();
  });
  bindForm('feedback-form',async()=>{
    await api('/feedback',{skill:byId('feedback-skill').value,rating:Number(byId('feedback-rating').value),note:byId('feedback-note').value});
    await loadMetrics();
  });
  bindForm('proposal-form',async()=>{
    await api('/proposal',{field:byId('proposal-field').value,value:byId('proposal-value').value,reason:byId('proposal-reason').value});
    await loadProposals();
  });
  bindForm('event-form',async()=>{
    await api('/timeline',{text:byId('event-text').value,fiction:byId('event-fiction').checked,confirmed:byId('event-confirm').checked});
    await loadTimeline();
  });
}
function canvasTails(){
  const canvas=byId('soul-canvas'),ctx=canvas.getContext('2d');
  if(!ctx)return;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
  let width=0,height=0,ratio=1,last=0;
  function resize(){
    ratio=Math.min(devicePixelRatio||1,2);width=canvas.clientWidth;height=canvas.clientHeight;
    canvas.width=Math.floor(width*ratio);canvas.height=Math.floor(height*ratio);ctx.setTransform(ratio,0,0,ratio,0,0);
  }
  resize();window.addEventListener('resize',resize);
  const duration=1000/28;
  function draw(t){
    if(document.hidden){requestAnimationFrame(draw);return;}
    if(reduced&&last>0)return;
    if(t-last<duration){requestAnimationFrame(draw);return;}last=t;
    const cx=width*.55,cy=height*.44,span=Math.min(width,height);
    ctx.clearRect(0,0,width,height);
    ctx.save();
    ctx.translate(cx,cy);
    ctx.beginPath();ctx.arc(0,0,span*.19,0,2*Math.PI);ctx.strokeStyle='rgba(209,168,95,.25)';ctx.lineWidth=1;ctx.stroke();
    ctx.beginPath();ctx.arc(0,0,span*.32,0,2*Math.PI);ctx.strokeStyle='rgba(209,168,95,.10)';ctx.stroke();
    const glow=ctx.createRadialGradient(0,0,2,0,0,span*.25);glow.addColorStop(0,'rgba(231,177,86,.24)');glow.addColorStop(1,'rgba(231,177,86,0)');
    ctx.fillStyle=glow;ctx.beginPath();ctx.arc(0,0,span*.25,0,2*Math.PI);ctx.fill();
    // Exactly nine tails share one visual root; the curves are a symbolic placeholder.
    for(let i=0;i<9;i++){
      const angle=Math.PI+(i/8)*Math.PI;const wave=reduced?0:Math.sin(t*.0012+i*.8)*span*.018;
      const distance=span*(state.gesture==='tail_wrap'?.3:.43);
      const endX=Math.cos(angle)*distance,endY=Math.sin(angle)*distance+wave;
      const alpha=.14+(i%3)*.07;
      ctx.beginPath();ctx.moveTo(0,span*.08);ctx.bezierCurveTo(endX*.28,-span*.05,endX*.72,endY*.6,endX,endY);
      ctx.strokeStyle='rgba(236,196,116,'+alpha+')';ctx.lineWidth=8+(i%2)*4;ctx.lineCap='round';ctx.stroke();
      ctx.strokeStyle='rgba(255,244,212,.38)';ctx.lineWidth=.9;ctx.stroke();
    }
    const core=ctx.createRadialGradient(0,0,3,0,0,span*.09);
    core.addColorStop(0,'#ffe1a2');core.addColorStop(.2,'#b58740');core.addColorStop(1,'rgba(37,25,8,0)');
    ctx.fillStyle=core;ctx.beginPath();ctx.arc(0,0,span*.09,0,Math.PI*2);ctx.fill();
    ctx.restore();requestAnimationFrame(draw);
  }requestAnimationFrame(draw);
}
async function start(){
  setupForms();setupV4();setupV5();canvasTails();
  try{
    const [health,profile]=await Promise.all([api('/health'),api('/profile')]);
    byId('status-dot').classList.toggle('online',health.configured);
    notify(health.configured?'Cloud.ru подключён':'Cloud.ru не настроен · см. README');
    await loadSessions();
    await Promise.all([loadMemories(),loadGoals(),loadMetrics(),loadProposals(),loadTimeline(),loadDiary(),v4Map(),v4Links(),v4Assessments(),v4Strategies(),v5Permissions(),v5Episodes(),v5Causal(),v5Growth(),v5Plan(),v5Transfers()]);
  }catch(e){notify(e.message,true);}
}
start();
