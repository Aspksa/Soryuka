/**
 * Soryuka 3D graphics preferences. No cookies, backend writes or cloud calls.
 * Stored per browser origin. Values are sanitized before touching Three.js.
 */
export const GRAPHICS_STORAGE_KEY='soryuka.mirror.graphics.v1';
export const GRAPHICS_DEFAULTS=Object.freeze({
  fog:0.0008,
  light:1,
  speed:1
});
export const GRAPHICS_LIMITS=Object.freeze({
  fog:Object.freeze({min:0,max:0.003,step:0.00005}),
  light:Object.freeze({min:0.2,max:2,step:0.1}),
  speed:Object.freeze({min:0.25,max:2,step:0.05})
});
const keys=Object.keys(GRAPHICS_DEFAULTS);
const cloneDefaults=()=>({...GRAPHICS_DEFAULTS});

export function normalizeGraphics(value){
  const result=cloneDefaults();
  if(!value||typeof value!=='object'||Array.isArray(value))return result;
  for(const key of keys){
    const raw=value[key];
    if(typeof raw!=='number'||!Number.isFinite(raw))continue;
    const {min,max,step}=GRAPHICS_LIMITS[key];
    const snapped=Math.round((Math.max(min,Math.min(max,raw))-min)/step)*step+min;
    result[key]=Number(Math.max(min,Math.min(max,snapped)).toFixed(8));
  }
  return result;
}
export function readGraphics(storage){
  try{
    const raw=storage?.getItem(GRAPHICS_STORAGE_KEY);
    if(raw===null||raw===undefined)return cloneDefaults();
    const parsed=JSON.parse(raw);
    if(!parsed||parsed.version!==1)return cloneDefaults();
    return normalizeGraphics(parsed.settings);
  }catch{
    return cloneDefaults();  // Private mode, unavailable storage, corrupt JSON.
  }
}
export function saveGraphics(storage,settings){
  const result=normalizeGraphics(settings);
  try{
    storage?.setItem(GRAPHICS_STORAGE_KEY,JSON.stringify({version:1,settings:result}));
    return {settings:result,saved:!!storage};
  }catch{
    return {settings:result,saved:false};
  }
}
export function resetGraphics(storage){
  try{
    storage?.removeItem(GRAPHICS_STORAGE_KEY);
    return {settings:cloneDefaults(),saved:!!storage};
  }catch{
    return {settings:cloneDefaults(),saved:false};
  }
}
