// Shared transport and session helpers for both Soryuka chat surfaces.
// No automatic retry: a failed request may already have been charged/saved.
(function(root){
  const key='soryuka.session';
  async function api(path,data){
    const response=await fetch('/api'+path,{method:data===undefined?'GET':'POST',
      headers:data===undefined?{}:{'Content-Type':'application/json'},
      body:data===undefined?undefined:JSON.stringify(data)});
    let json;
    try{json=await response.json();}catch{throw Error('Сервер вернул некорректный ответ');}
    if(!response.ok)throw Error(json.error||'Ошибка запроса '+response.status);
    return json;
  }
  function currentSession(){return localStorage.getItem(key)||'';}
  function setSession(id){localStorage.setItem(key,id);return id;}
  async function ensureSession(){return currentSession()||setSession((await api('/session',{})).session_id);}
  async function history(id){const result=await api('/history?session_id='+encodeURIComponent(id));
    if(!Array.isArray(result.items))throw Error('Некорректная история');return result.items;}
  root.SoryukaChat=Object.freeze({api,currentSession,setSession,ensureSession,history,key});
})(window);
