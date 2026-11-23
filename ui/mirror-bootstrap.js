/* Soryuka WebGL startup diagnostics - runs before the Three.js module imports. */
(function(){
  const panel=document.getElementById('soryuka-engine-error');
  const message=document.getElementById('soryuka-engine-message');
  const status=document.getElementById('render-status');
  let ready=false;
  const retry=document.getElementById('soryuka-engine-retry');
  retry?.addEventListener('click',()=>window.location.reload());
  const show=reason=>{
    if(ready&&reason!=='webglcontextlost')return;
    if(message)message.textContent=reason;
    if(panel)panel.hidden=false;
    if(status)status.textContent='3D недоступно';
  };
  try{
    const test=document.createElement('canvas');
    const gl=test.getContext('webgl2',{failIfMajorPerformanceCaveat:false})||
             test.getContext('webgl',{failIfMajorPerformanceCaveat:false});
    if(!gl)show('WebGL недоступен. Включи аппаратное ускорение в браузере, обнови драйвер видеокарты и перезапусти Soryuka.');
    const ext=gl?.getExtension('WEBGL_lose_context');
    if(ext)ext.loseContext();
  }catch{
    show('3D-движок не смог создать WebGL-контекст. Проверь драйвер и аппаратное ускорение.');
  }
  // Import-map or script load errors happen before the renderer module can report them.
  window.addEventListener('error',event=>{
    if(!ready){
      const text=String(event.message||'');
      show(text?'Ошибка инициализации 3D-сцены: '+text.slice(0,150):
        'Не удалось загрузить библиотеки Three.js. Проверь локальную папку ui/node_modules.');
    }
  });
  window.addEventListener('unhandledrejection',event=>{
    if(!ready)show('Ошибка запуска 3D-сцены. Проверь библиотеки, WebGL и журнал браузера.');
  });
  window.addEventListener('soryuka:renderer-ready',()=>{
    ready=true;
    if(panel)panel.hidden=true;
  });
  window.addEventListener('soryuka:renderer-failed',event=>{
    ready=false;
    show(event.detail||'WebGL-контекст потерян. Обнови страницу или перезапусти браузер.');
  });
  setTimeout(()=>{
    if(!ready)show('3D-сцена не загрузилась. Проверь WebGL, локальные библиотеки и консоль браузера.');
  },12000);
})();
