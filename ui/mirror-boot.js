/**
 * Soryuka 3D startup guard.
 * The fallback is ordinary HTML, so it still works when WebGL or the
 * WebGL renderer import fails before any scene is constructed.
 */
const panel=document.getElementById('soryuka-engine-error');
const description=document.getElementById('soryuka-error-description');
const retry=document.getElementById('soryuka-error-retry');
const status=document.getElementById('scene-status');
let ready=false;
function showFailure(message){
  const text=typeof message==='string'?message:'Не удалось запустить 3D-сцену.';
  description.textContent=text;
  panel.hidden=false;
  status.textContent='3D НЕДОСТУПЕН';
  status.className='scene-status scene-status-error';
  ready=false;
}
window.__soryukaGraphicsError=showFailure;
retry.addEventListener('click',()=>location.reload());
window.addEventListener('soryuka:scene-ready',()=>{
  ready=true;
  panel.hidden=true;
  status.textContent='3D АКТИВЕН';
  status.className='scene-status';
},{once:false});
window.addEventListener('unhandledrejection',event=>{
  if(!ready)showFailure('Ошибка инициализации 3D. Можно открыть чат, даже если WebGL недоступен.');
});
async function boot(){
  try{
    const test=document.createElement('canvas');
    const gl=test.getContext('webgl2',{failIfMajorPerformanceCaveat:false})||
      test.getContext('webgl',{failIfMajorPerformanceCaveat:false});
    if(!gl)throw Error('WebGL не поддерживается или выключено аппаратное ускорение браузера.');
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    await import('./mirror-original.js');
  }catch(err){
    console.error('Soryuka 3D launch:',err?.message||err);
    showFailure(err?.message||'Не удалось загрузить 3D-движок или его локальные библиотеки.');
  }
}
void boot();
