/* Real pixels, not mocked GL. Explicit local native render directory required. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs');const path=require('node:path');const http=require('node:http');
if(!process.env.SPATIAL_JOB||!process.env.SPATIAL_NATIVE_RENDERS||!process.env.SPATIAL_COMPARISON_OUTPUT)throw Error('Provide real scan, native eval directory and new output directory.');
(async()=>{
 let localFiles=null;
 const out=process.env.SPATIAL_COMPARISON_OUTPUT;fs.mkdirSync(out,{recursive:false});
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try {
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(process.env.SPATIAL_UI_URL||'http://127.0.0.1:9013/spatial-scans/ui');
 const suppliedModel=process.env.SPATIAL_MODEL_FILE,suppliedResult=process.env.SPATIAL_RESULT_FILE;
 if(!!suppliedModel!==!!suppliedResult)throw Error('Provide both experimental model and result, or neither.');
 let modelURL=null;
 if(suppliedModel){
   // Stream large real models instead of duplicating 100k SH3 arrays in both
   // Node and the Playwright protocol. Serve only the two explicitly supplied files.
   const origin=new URL(process.env.SPATIAL_UI_URL||'http://127.0.0.1:9013').origin;
   localFiles=http.createServer((req,res)=>{
     const file=req.url==='/model'?suppliedModel:req.url==='/result'?suppliedResult:null;
     if(req.method!=='GET'||!file){res.writeHead(404);res.end();return;}
     res.writeHead(200,{'Content-Type':'application/json','Access-Control-Allow-Origin':origin});
     const stream=fs.createReadStream(file);stream.on('error',()=>res.destroy());stream.pipe(res);
   });
   await new Promise(resolve=>localFiles.listen(0,'127.0.0.1',resolve));
   modelURL='http://127.0.0.1:'+localFiles.address().port;
 }
 const rows=suppliedModel?await page.evaluate(async(url)=>{
   const [model,result]=await Promise.all([fetch(url+'/model').then(r=>r.json()),fetch(url+'/result').then(r=>r.json())]);
   if(!model.referenceViews?.length)throw Error('Experimental model lacks measured reference cameras');
   window.comparisonModel=model;return result.evaluation;
 },modelURL):await page.evaluate(async(job)=>{
   const [model,result]=await Promise.all([fetch('/spatial-scans/'+job+'/splat-reference/files/splats.json').then(r=>r.json()),fetch('/spatial-scans/'+job+'/splat-reference').then(r=>r.json())]);
   if(!model.referenceViews?.length)throw Error('Model lacks measured reference cameras');
   window.comparisonModel=model;return result.evaluation;
 },process.env.SPATIAL_JOB);
 const measured=[];
 for(const row of rows){
   const index=row.originalFrameIndex;const filename=String(index).padStart(5,'0')+'.png';
   const native=path.join(process.env.SPATIAL_NATIVE_RENDERS,filename);
   if(!fs.existsSync(native))throw Error('Missing native evaluation frame '+index);
   const bytes=fs.readFileSync(native).toString('base64');
   const value=await page.evaluate(async({index,bytes})=>{
     const model=window.comparisonModel;model.initialView=model.referenceViews.find(v=>v.originalFrameIndex===index);
     if(!model.initialView)throw Error('Missing calibration '+index);
     const img=new Image();img.src='data:image/png;base64,'+bytes;await img.decode();
     const canvas=document.getElementById('splatOrbitCanvas');canvas.hidden=false;canvas.style.width=img.width+'px';canvas.style.maxWidth='none';
     window.comparisonViewer?.dispose();window.comparisonViewer=new SpatialSplatViewer(canvas);window.comparisonViewer.setModel(model);
     if(canvas.width!==img.width||canvas.height!==img.height)throw Error('Pixel dimensions differ');
     const gl=canvas.getContext('webgl2');const actual=new Uint8Array(canvas.width*canvas.height*4);gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,actual);
     const ref=document.createElement('canvas');ref.width=img.width;ref.height=img.height;const ctx=ref.getContext('2d');ctx.drawImage(img,0,0);const expected=ctx.getImageData(0,0,img.width,img.height).data;
     let square=0,max=0;
     for(let y=0;y<img.height;y++)for(let x=0;x<img.width;x++)for(let c=0;c<3;c++){
       const d=actual[((img.height-1-y)*img.width+x)*4+c]-expected[(y*img.width+x)*4+c];square+=d*d;max=Math.max(max,Math.abs(d));
     }
     const mse=square/(img.width*img.height*3*255*255);const ext=gl.getExtension('WEBGL_debug_renderer_info');
     return {index,maxAbsoluteByteError:max,psnr:-10*Math.log10(Math.max(mse,1e-12)),renderer:ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER),png:canvas.toDataURL()};
   },{index,bytes});
   fs.writeFileSync(path.join(out,filename),Buffer.from(value.png.split(',')[1],'base64'));delete value.png;measured.push(value);
 }
 fs.writeFileSync(path.join(out,'comparison.json'),JSON.stringify({scope:'renderer_fidelity_not_reconstruction_acceptance',frames:measured,errors},null,2));
 if(errors.length||measured.some(v=>v.maxAbsoluteByteError>2))throw Error('Native renderer pixel fidelity failed; evidence retained');
 console.log(JSON.stringify({frames:measured.length,maxAbsoluteByteError:Math.max(...measured.map(v=>v.maxAbsoluteByteError)),errors}));
 }finally{await browser.close();if(localFiles)await new Promise(resolve=>localFiles.close(resolve));}
})().catch(e=>{console.error(e);process.exit(1)});
