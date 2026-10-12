/* Real-model paired CPU-SH/GPU-SH timing; no Gaussian subsampling. Not quality acceptance. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs'),http=require('node:http'),path=require('node:path');
if(!process.env.SPATIAL_MODEL_FILE||!process.env.SPATIAL_PERFORMANCE_OUTPUT)throw Error('Provide a real model and new output file.');
const renderer=fs.readFileSync(path.join(__dirname,'../app/spatial_splat_viewer.js'),'utf8');
const cpu=renderer.replace('rgb=viewColor();opacity=angleOpacity.y;','rgb=color;opacity=angleOpacity.y;')
 .replace('...model.colors[p.i],p.i],n*10)','...colorForView(model,p.i,eye),p.i],n*10)');
if(cpu===renderer||!cpu.includes('...colorForView(model,p.i,eye),p.i],n*10)'))throw Error('CPU control branch was not constructed');
(async()=>{
 const ui=process.env.SPATIAL_UI_URL||'http://127.0.0.1:9013/spatial-scans/ui';
 const server=http.createServer((req,res)=>{
  if(req.url!=='/model'||req.method!=='GET'){res.writeHead(404);res.end();return;}
  res.writeHead(200,{'Content-Type':'application/json','Access-Control-Allow-Origin':new URL(ui).origin});
  const stream=fs.createReadStream(process.env.SPATIAL_MODEL_FILE);stream.on('error',()=>res.destroy());stream.pipe(res);
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const url='http://127.0.0.1:'+server.address().port+'/model';
 let browser;const runs=[];
 try {
  browser=await chromium.launch({channel:'chrome',headless:true});
  for(const [mode,source] of [['cpu_sh_control',cpu],['gpu_sh',renderer]]){
   const page=await browser.newPage({viewport:{width:1000,height:800}}),errors=[];
   page.on('pageerror',e=>errors.push(e.message));await page.goto(ui);await page.addScriptTag({content:source});
   const setup=await page.evaluate(async url=>{
    const model=await fetch(url).then(r=>r.json());window.performanceModel=model;
    const canvas=document.createElement('canvas');canvas.style.width='512px';canvas.style.maxWidth='none';document.body.prepend(canvas);window.performanceCanvas=canvas;
    const start=performance.now();const viewer=new SpatialSplatViewer(canvas);viewer.setModel(model);window.performanceViewer=viewer;
    const gl=canvas.getContext('webgl2');gl.finish();const initialMs=performance.now()-start;
    window.frameTimes=[];window.frameGaps=[];window.pendingGPUQueries=[];window.measureActive=true;
    const ext=gl.getExtension('EXT_disjoint_timer_query_webgl2');
    if(ext){const nativeDraw=gl.drawArraysInstanced.bind(gl);gl.drawArraysInstanced=(...args)=>{const query=gl.createQuery();gl.beginQuery(ext.TIME_ELAPSED_EXT,query);nativeDraw(...args);gl.endQuery(ext.TIME_ELAPSED_EXT);pendingGPUQueries.push(query)}}
    let startEvent=0,lastRAF=null;
    canvas.addEventListener('pointermove',event=>{if(event.buttons===1)startEvent=performance.now()},true);
    canvas.addEventListener('pointermove',event=>{if(event.buttons===1){gl.finish();frameTimes.push(performance.now()-startEvent)}});
    function tick(now){if(lastRAF!==null)frameGaps.push(now-lastRAF);lastRAF=now;if(measureActive)requestAnimationFrame(tick)}requestAnimationFrame(tick);
    const debug=gl.getExtension('WEBGL_debug_renderer_info');
    return {count:model.means.length,degree:model.shDegree,initialMs,width:canvas.width,height:canvas.height,
     renderer:debug?gl.getParameter(debug.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER),gpuTimerAvailable:!!ext};
   },url);
   const canvas=page.locator('canvas').first(),bounds=await canvas.boundingBox();
   await page.mouse.move(bounds.x+bounds.width/2,bounds.y+bounds.height/2);await page.mouse.down();
   for(let i=0;i<20;i++)await page.mouse.move(bounds.x+bounds.width/2+4*(i+1),bounds.y+bounds.height/2+(i%3)*2);
   await page.mouse.up();
   await page.waitForFunction(()=>{const gl=performanceCanvas.getContext('webgl2');return pendingGPUQueries.every(q=>gl.getQueryParameter(q,gl.QUERY_RESULT_AVAILABLE))},{},{timeout:30000});
   const measured=await page.evaluate(()=>{
    measureActive=false;const gl=performanceCanvas.getContext('webgl2'),ext=gl.getExtension('EXT_disjoint_timer_query_webgl2');
    const disjoint=ext?gl.getParameter(ext.GPU_DISJOINT_EXT):null;
    const gpuMs=disjoint?[]:pendingGPUQueries.map(q=>gl.getQueryParameter(q,gl.QUERY_RESULT)/1e6);
    pendingGPUQueries.forEach(q=>gl.deleteQuery(q));
    const summarize=values=>{const v=values.slice().sort((a,b)=>a-b);return {count:v.length,median:v[Math.floor(v.length/2)]??null,p95:v[Math.min(v.length-1,Math.ceil(v.length*.95)-1)]??null,maximum:v.at(-1)??null}};
    const result={frameWallMs:summarize(frameTimes),gpuDrawMs:summarize(gpuMs),gpuDisjoint:disjoint,
     rafGapsMs:summarize(frameGaps),framesOver16_7ms:frameTimes.filter(t=>t>1000/60).length,
     estimatedMissed60HzBudgets:frameTimes.reduce((sum,t)=>sum+Math.max(0,Math.ceil(t/(1000/60))-1),0),
     note:'Frame wall time includes CPU projection/depth sort, upload and forced GPU completion. Missed budgets are estimates, not measured display drops.'};
    performanceViewer.dispose();return result;
   });
   runs.push({mode,...setup,...measured,errors});await page.close();
  }
  fs.writeFileSync(process.env.SPATIAL_PERFORMANCE_OUTPUT,JSON.stringify({scope:'renderer_performance_not_reconstruction_acceptance',runs},null,2));
  if(runs.some(r=>r.errors.length||r.frameWallMs.count!==20))throw Error('Real browser timing failed; evidence retained');
  console.log(JSON.stringify({runs}));
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(e=>{console.error(e);process.exit(1)});
