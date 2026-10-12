/* Manual real-model browser acceptance. PLAYWRIGHT_MODULE may point to an installed package. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
if(!process.env.SPATIAL_JOB)throw Error('Set SPATIAL_JOB to a real imported scan; no private scan ID is stored in this test.');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1400,height:1100}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(process.env.SPATIAL_UI_URL||'http://127.0.0.1:9013/spatial-scans/ui');
 await page.selectOption('#history',process.env.SPATIAL_JOB);
 await page.waitForFunction(()=>document.querySelector('#frames').options.length>0);
 await page.click('#splatOrbitShow');
 await page.waitForFunction(()=>!document.querySelector('#splatOrbitCanvas').hidden&&document.querySelector('#splatOrbitStatus').textContent.includes('个高斯'));
 const canvas=page.locator('#splatOrbitCanvas');
 await canvas.scrollIntoViewIfNeeded();
 await canvas.screenshot({path:'/tmp/spatial-splat-orbit-default.png'});
 const before=await canvas.evaluate(c=>c.toDataURL());
 const bounds=await canvas.boundingBox();
 await page.mouse.move(bounds.x+bounds.width/2,bounds.y+bounds.height/2);
 await page.mouse.down();await page.mouse.move(bounds.x+bounds.width/2+170,bounds.y+bounds.height/2+45,{steps:10});await page.mouse.up();
 await page.mouse.wheel(0,-180);
 const after=await canvas.evaluate(c=>c.toDataURL());
 if(before===after)throw Error('orbit_and_zoom_did_not_change_render');
 await canvas.screenshot({path:'/tmp/spatial-splat-orbit-rotated.png'});
 await page.click('#splatOrbitReset');
 const reset=await canvas.evaluate(c=>c.toDataURL());
 if(before!==reset)throw Error('reset_did_not_restore_original_view');
 await page.screenshot({path:'/tmp/spatial-splat-orbit-page.png',fullPage:true});
 const status=await page.locator('#splatOrbitStatus').innerText();
 // Actual GPU must accept an exactly downward calibrated camera; this is a
 // rendering boundary case, not a claim about scan/reconstruction accuracy.
 await page.evaluate(async job=>{
   const model=await fetch('/spatial-scans/'+job+'/splat-reference/files/splats.json').then(r=>r.json());
   const p=model.means[0],eye=[p[0],p[1]+1,p[2]];
   model.initialView={...model.referenceViews[0],eye,target:p,
     cameraToWorld:[1,0,0,0,0,0,-1,0,0,1,0,0,...eye,1]};
   const verticalCanvas=document.createElement('canvas');
   const viewer=new SpatialSplatViewer(verticalCanvas);
   viewer.setModel(model);verticalCanvas.dispatchEvent(new WheelEvent('wheel',{deltaY:-180,cancelable:true}));viewer.reset();viewer.dispose();
 },process.env.SPATIAL_JOB);
 await page.route('**/splat-reference/files/splats.json',route=>route.fulfill({status:500,contentType:'application/json',body:'{"detail":"controlled_model_load_failure"}'}));
 await page.click('#splatOrbitShow');
 await page.waitForFunction(()=>document.querySelector('#splatOrbitStatus').textContent.includes('模型查看失败'));
 if(!await canvas.isHidden()||!await page.locator('#splatOrbitReset').isHidden())throw Error('failed_load_retained_previous_model');
 if(errors.length)throw Error(errors.join(';'));
 console.log(JSON.stringify({status,orbitAndZoomChanged:before!==after,resetRestored:before===reset,verticalCameraRendered:true,failedLoadClearedPreviousModel:true,errors}));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
