/* WebGL2 covariance Gaussian preview; quality is checked against native Brush renders. */
(function (root) {
  'use strict';
  const dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0);
  const sub=(a,b)=>a.map((v,i)=>v-b[i]);
  const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
  const unit=a=>{const n=Math.hypot(...a);if(n<1e-10)throw Error('invalid_camera_direction');return a.map(v=>v/n);};
  function quaternionMatrix(q) {
    const n=Math.hypot(...q);if(!Number.isFinite(n)||n<1e-8)throw Error('invalid_splat_rotation');
    const [w,x,y,z]=q.map(v=>v/n);
    return [[1-2*(y*y+z*z),2*(x*y-w*z),2*(x*z+w*y)],
      [2*(x*y+w*z),1-2*(x*x+z*z),2*(y*z-w*x)],
      [2*(x*z-w*y),2*(y*z+w*x),1-2*(x*x+y*y)]];
  }
  function camera(eye,target,focal,width,height) {
    const forward=unit(sub(target,eye));
    const right=unit(cross(forward,[0,1,0]));
    const down=cross(forward,right);
    return {eye,rows:[right,down,forward],focal,width,height};
  }
  function project(mean,logScale,rotation,cam) {
    const xyz=cam.rows.map(row=>dot(row,sub(mean,cam.eye)));
    const [x,y,z]=xyz;if(z<.01)return null;
    const r=quaternionMatrix(rotation),scale=logScale.map(v=>Math.exp(2*v));
    // Camera-space rotated axes form covariance B diag(s^2) B^T.
    const b=cam.rows.map(row=>[0,1,2].map(k=>dot(row,r.map(axis=>axis[k]))));
    const cov=b.map(row=>b.map(other=>row.reduce((sum,v,k)=>sum+v*other[k]*scale[k],0)));
    // Match Brush's projection Jacobian: bound perspective derivatives at
    // 15% beyond the frustum, without moving or removing the Gaussian mean.
    // Unbounded x/z for offscreen splats otherwise creates enormous ellipses.
    const fx=cam.fx??cam.focal,fy=cam.fy??cam.focal,cx=cam.cx??cam.width/2,cy=cam.cy??cam.height/2;
    const clippedX=Math.max((-.15*cam.width-cx)/fx,Math.min((1.15*cam.width-cx)/fx,x/z));
    const clippedY=Math.max((-.15*cam.height-cy)/fy,Math.min((1.15*cam.height-cy)/fy,y/z));
    const j=[[fx/z,0,-fx*clippedX/z],[0,fy/z,-fy*clippedY/z]];
    const c=j.map(row=>j.map(other=>row.reduce((s,v,i)=>s+v*cov[i].reduce((t,u,k)=>t+u*other[k],0),0)));
    c[0][0]+=.3;c[1][1]+=.3;
    const a=c[0][0],d=c[1][1],off=c[0][1],disc=Math.hypot(a-d,2*off);
    const major=Math.sqrt(Math.max(0,(a+d+disc)/2)),minor=Math.sqrt(Math.max(0,(a+d-disc)/2));
    return {x:fx*x/z+cx,y:fy*y/z+cy,z,major,minor,angle:.5*Math.atan2(2*off,a-d),covariance:c};
  }
  // Brush v0.3.0 project_visible.wgsl (Apache-2.0): mean-camera direction.
  // Coefficients remain in training Z-up coordinates, while geometry is Y-up.
  function sphericalHarmonicColor(coefficients,degree,directionARKit) {
    if(!Number.isInteger(degree)||degree<0||degree>3)throw Error('unsupported_spherical_harmonic_degree');
    const d=unit(directionARKit),[x,y,z]=[d[0],-d[2],d[1]];
    const z2=z*z,c1=x*x-y*y,s1=2*x*y;
    const basis=[0.2820947917738781];
    if(degree>=1)basis.push(-0.48860251190292*y,0.48860251190292*z,-0.48860251190292*x);
    if(degree>=2)basis.push(0.5462742152960395*s1,-1.092548430592079*z*y,
      0.9461746957575601*z2-0.3153915652525201,-1.092548430592079*z*x,0.5462742152960395*c1);
    if(degree>=3){
      const tmp0=-2.285228997322329*z2+0.4570457994644658,tmp1=1.445305721320277*z;
      basis.push(-0.5900435899266435*(x*s1+y*c1),tmp1*s1,tmp0*y,
        z*(1.865881662950577*z2-1.119528997770346),tmp0*x,tmp1*c1,
        -0.5900435899266435*(x*c1-y*s1));
    }
    if(!Array.isArray(coefficients)||coefficients.length!==basis.length||
       coefficients.some(c=>!Array.isArray(c)||c.length!==3||!c.every(Number.isFinite)))throw Error('invalid_spherical_harmonic_coefficients');
    // Native rasterizer clamps negative radiance before alpha blending, not HDR >1.
    return [0,1,2].map(channel=>Math.max(0,basis.reduce((v,b,i)=>v+b*coefficients[i][channel],.5)));
  }
  function colorForView(model,index,eye) {
    if(model.shDegree===undefined||model.shDegree===0&&!model.shCoefficients)return model.colors[index];
    return sphericalHarmonicColor(model.shCoefficients[index],model.shDegree,sub(model.means[index],eye));
  }
  function validateModel(model) {
    const names=['means','logScales','rotations','colors','opacities'];
    const count=model?.means?.length;if(!count)throw Error('模型没有高斯');
    for(const name of names)if(!Array.isArray(model[name])||model[name].length!==count)throw Error('模型字段不完整');
    if(model.shDegree!==undefined||model.shCoefficients!==undefined){
      if(!Number.isInteger(model.shDegree)||model.shDegree<0||model.shDegree>3)throw Error('模型球谐阶数不支持');
      if(model.shDegree>0||model.shCoefficients!==undefined){
        if(model.shCoordinateConvention!=='Brush_Z_up')throw Error('模型球谐坐标约定无效');
        if(!Array.isArray(model.shCoefficients)||model.shCoefficients.length!==count)throw Error('模型球谐系数不完整');
        for(const coefficients of model.shCoefficients){
          if(!Array.isArray(coefficients)||coefficients.length!==(model.shDegree+1)**2||
             coefficients.some(c=>!Array.isArray(c)||c.length!==3||!c.every(Number.isFinite)))throw Error('模型球谐系数无效');
        }
      }
    }
    for(let i=0;i<count;i++) {
      for(const [name,size] of [['means',3],['logScales',3],['rotations',4],['colors',3]])
        if(model[name][i]?.length!==size||!model[name][i].every(Number.isFinite))throw Error('模型数值无效');
      quaternionMatrix(model.rotations[i]);
      if(model.logScales[i].some(v=>!Number.isFinite(Math.exp(2*v))))throw Error('模型尺度无效');
      if(!Number.isFinite(model.opacities[i])||model.opacities[i]<0||model.opacities[i]>1||model.colors[i].some(v=>v<0||v>65504))throw Error('模型透明度或颜色无效');
    }
  }
  // Immutable per-model GPU data. Sorting retains the original Gaussian index.
  // A texel holds native ARKit mean XYZ or one Brush coefficient RGB; alpha is unused.
  function packSHTexture(model,maximumSize) {
    if(!Number.isInteger(maximumSize)||maximumSize<1)throw Error('GPU 球谐纹理尺寸无效');
    if(!model.shCoefficients)return {width:1,height:1,stride:0,degree:-1,data:new Float32Array(4)};
    const count=model.means.length,stride=1+(model.shDegree+1)**2;
    if(count>2**24)throw Error('GPU 高斯索引超出精确范围；未减少模型数量');
    const texels=count*stride,width=Math.min(2048,maximumSize),height=Math.ceil(texels/width);
    if(!Number.isSafeInteger(texels)||texels<1||height>maximumSize||width*height>0x7fffffff)
      throw Error('GPU 球谐纹理容量不足；未减少模型数量');
    const data=new Float32Array(width*height*4);
    for(let i=0;i<count;i++) {
      let offset=i*stride*4;
      data.set(model.means[i],offset);
      for(const coefficient of model.shCoefficients[i]){offset+=4;data.set(coefficient,offset);}
    }
    if(!data.every(Number.isFinite))throw Error('GPU 球谐数值超出单精度范围');
    return {width,height,stride,degree:model.shDegree,data};
  }
  function gaussianGPU(canvas,model) {
    const gl=canvas.getContext('webgl2',{alpha:false,antialias:false,preserveDrawingBuffer:true});
    if(!gl)throw Error('本浏览器无法运行 GPU 高斯预览；原照片和训练器渲染仍可查看');
    if(!gl.getExtension('EXT_color_buffer_float'))throw Error('本浏览器缺少浮点高斯混合支持；原照片和训练器渲染仍可查看');
    const resources=[];
    const keep=(value,remove)=>{resources.push(()=>remove.call(gl,value));return value;};
    const release=()=>{for(const remove of resources.splice(0).reverse())remove();};
    try {
    if(gl.getParameter(gl.MAX_VERTEX_TEXTURE_IMAGE_UNITS)<1)throw Error('GPU 缺少顶点纹理读取能力');
    const packed=packSHTexture(model,gl.getParameter(gl.MAX_TEXTURE_SIZE));
    const shTexture=keep(gl.createTexture(),gl.deleteTexture);
    gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_2D,shTexture);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA32F,packed.width,packed.height,0,gl.RGBA,gl.FLOAT,packed.data);
    if(gl.getError()!==gl.NO_ERROR)throw Error('GPU 球谐纹理上传失败；未减少模型数量');
    gl.activeTexture(gl.TEXTURE0);
    const fullFloat=!!gl.getExtension('EXT_float_blend');
    const target=keep(gl.createFramebuffer(),gl.deleteFramebuffer),radiance=keep(gl.createTexture(),gl.deleteTexture);let targetWidth=0,targetHeight=0;
    const shader=(type,source)=>{const s=keep(gl.createShader(type),gl.deleteShader);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error('GPU 着色器失败：'+gl.getShaderInfoLog(s));return s;};
    const vertex=shader(gl.VERTEX_SHADER,`#version 300 es
      precision highp float;
      precision highp int;
      layout(location=0) in vec2 corner;
      layout(location=1) in vec4 geometry;
      layout(location=2) in vec2 angleOpacity;
      layout(location=3) in vec3 color;
      layout(location=4) in float originalIndex;
      uniform vec2 viewport;
      uniform highp sampler2D shTexture;
      uniform int shTextureWidth,shStride,shDegree;
      uniform vec3 eye;
      vec3 readSH(int index){return texelFetch(shTexture,ivec2(index%shTextureWidth,index/shTextureWidth),0).xyz;}
      vec3 viewColor(){
        if(shDegree<0)return color;
        int base=int(originalIndex)*shStride;
        float b[16];b[0]=0.2820947917738781;
        if(shDegree>0){
          vec3 d=normalize(readSH(base)-eye);
          // Native geometry Y-up -> official Brush SH training Z-up.
          float x=d.x,y=-d.z,z=d.y,z2=z*z,c1=x*x-y*y,s1=2.0*x*y;
          b[1]=-0.48860251190292*y;b[2]=0.48860251190292*z;b[3]=-0.48860251190292*x;
          if(shDegree>=2){
            b[4]=0.5462742152960395*s1;b[5]=-1.092548430592079*z*y;
            b[6]=0.9461746957575601*z2-0.3153915652525201;
            b[7]=-1.092548430592079*z*x;b[8]=0.5462742152960395*c1;
          }
          if(shDegree>=3){
            float tmp0=-2.285228997322329*z2+0.4570457994644658,tmp1=1.445305721320277*z;
            b[9]=-0.5900435899266435*(x*s1+y*c1);b[10]=tmp1*s1;b[11]=tmp0*y;
            b[12]=z*(1.865881662950577*z2-1.119528997770346);
            b[13]=tmp0*x;b[14]=tmp1*c1;b[15]=-0.5900435899266435*(x*c1-y*s1);
          }
        }
        vec3 radiance=vec3(0.5);
        for(int i=0;i<16;i++){if(i>=shStride-1)break;radiance+=b[i]*readSH(base+1+i);}
        return max(radiance,vec3(0.0));
      }
      out vec2 sigmaPosition; out vec3 rgb; out float opacity;
      void main(){
        float extent=sqrt(2.0*log(255.0*angleOpacity.y));
        sigmaPosition=corner*extent;
        vec2 offset=sigmaPosition*geometry.zw;
        float c=cos(angleOpacity.x),s=sin(angleOpacity.x);
        vec2 pixel=geometry.xy+vec2(c*offset.x-s*offset.y,s*offset.x+c*offset.y);
        gl_Position=vec4(2.0*pixel.x/viewport.x-1.0,1.0-2.0*pixel.y/viewport.y,0.0,1.0);
        rgb=viewColor();opacity=angleOpacity.y;
      }`);
    const fragment=shader(gl.FRAGMENT_SHADER,`#version 300 es
      precision highp float;
      in vec2 sigmaPosition; in vec3 rgb; in float opacity;
      out vec4 outputColor;
      void main(){float alpha=min(.999,opacity*exp(-.5*dot(sigmaPosition,sigmaPosition)));
        if(alpha<1.0/255.0)discard;
        outputColor=vec4(rgb*alpha,alpha);}`);
    const program=keep(gl.createProgram(),gl.deleteProgram);gl.attachShader(program,vertex);gl.attachShader(program,fragment);gl.linkProgram(program);
    if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error('GPU 程序失败：'+gl.getProgramInfoLog(program));
    const vao=keep(gl.createVertexArray(),gl.deleteVertexArray);gl.bindVertexArray(vao);
    const corners=keep(gl.createBuffer(),gl.deleteBuffer);gl.bindBuffer(gl.ARRAY_BUFFER,corners);
    gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,2,gl.FLOAT,false,0,0);
    const instances=keep(gl.createBuffer(),gl.deleteBuffer);gl.bindBuffer(gl.ARRAY_BUFFER,instances);
    for(const [location,size,offset] of [[1,4,0],[2,2,4],[3,3,6],[4,1,9]]){
      gl.enableVertexAttribArray(location);gl.vertexAttribPointer(location,size,gl.FLOAT,false,40,offset*4);gl.vertexAttribDivisor(location,1);
    }
    const viewport=gl.getUniformLocation(program,'viewport'),eyeUniform=gl.getUniformLocation(program,'eye');
    gl.useProgram(program);
    gl.uniform1i(gl.getUniformLocation(program,'shTexture'),1);
    gl.uniform1i(gl.getUniformLocation(program,'shTextureWidth'),packed.width);
    gl.uniform1i(gl.getUniformLocation(program,'shStride'),packed.stride);
    gl.uniform1i(gl.getUniformLocation(program,'shDegree'),packed.degree);
    const storageBytes=packed.data.byteLength;
    return {
      draw(projected,model,eye){
        if(gl.isContextLost())throw Error('GPU 连接丢失，请重新打开模型');
        const visible=projected.filter(p=>model.opacities[p.i]>=1/255);
        const data=new Float32Array(visible.length*10);
        visible.forEach((p,n)=>data.set([p.x,p.y,p.major,p.minor,p.angle,model.opacities[p.i],...model.colors[p.i],p.i],n*10));
        if(targetWidth!==canvas.width||targetHeight!==canvas.height){
          gl.bindTexture(gl.TEXTURE_2D,radiance);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);
          gl.texImage2D(gl.TEXTURE_2D,0,fullFloat?gl.RGBA32F:gl.RGBA16F,canvas.width,canvas.height,0,gl.RGBA,fullFloat?gl.FLOAT:gl.HALF_FLOAT,null);
          gl.bindFramebuffer(gl.FRAMEBUFFER,target);gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,radiance,0);
          if(gl.checkFramebufferStatus(gl.FRAMEBUFFER)!==gl.FRAMEBUFFER_COMPLETE)throw Error('GPU 浮点画面不可用');
          targetWidth=canvas.width;targetHeight=canvas.height;
        }
        gl.bindFramebuffer(gl.FRAMEBUFFER,target);
        // Native Brush's saved evaluation RGB uses a zero background.
        gl.viewport(0,0,canvas.width,canvas.height);gl.clearColor(0,0,0,1);gl.clear(gl.COLOR_BUFFER_BIT);
        gl.disable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);gl.enable(gl.BLEND);gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);
        gl.useProgram(program);gl.bindVertexArray(vao);gl.uniform2f(viewport,canvas.width,canvas.height);gl.uniform3f(eyeUniform,...eye);
        gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_2D,shTexture);gl.activeTexture(gl.TEXTURE0);
        gl.bindBuffer(gl.ARRAY_BUFFER,instances);gl.bufferData(gl.ARRAY_BUFFER,data,gl.DYNAMIC_DRAW);
        gl.drawArraysInstanced(gl.TRIANGLES,0,6,visible.length);
        // Preserve spherical-harmonic radiance >1 during compositing, clamp only for display.
        gl.bindFramebuffer(gl.READ_FRAMEBUFFER,target);gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER,null);
        gl.blitFramebuffer(0,0,canvas.width,canvas.height,0,0,canvas.width,canvas.height,gl.COLOR_BUFFER_BIT,gl.NEAREST);
        gl.bindFramebuffer(gl.FRAMEBUFFER,null);
        if(gl.getError()!==gl.NO_ERROR)throw Error('GPU 绘制失败');
      },
      storageBytes,
      dispose:release
    };
    }catch(error){release();throw error;}
  }
  class SpatialSplatViewer {
    constructor(canvas,{onStatus}={}) {this.canvas=canvas;this.onStatus=onStatus||(()=>{});this.disposeCurrent=null;}
    setModel(model,options={}) {
      this.disposeCurrent?.();this.disposeCurrent=null;this.canvas.width=this.canvas.width;validateModel(model);
      const canvas=this.canvas;
      canvas.style.touchAction='none';canvas.setAttribute('aria-label','训练高斯自由视角预览');
      this.onStatus(`独立坐标段 ${options.epoch ?? '未知'} · ${model.means.length} 个高斯，不与其他段轨迹拼接。${options.trainer?'本机 GPU 训练模型':'低清 CPU 实验模型'}可能缺失结构、模糊或出现空洞。GPU 自由视角预览，画质仍需与训练器渲染核对。拖动旋转，滚轮缩放，世界 Y 轴向上。`);
    const lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];
    for(const mean of model.means)for(let k=0;k<3;k++){lo[k]=Math.min(lo[k],mean[k]);hi[k]=Math.max(hi[k],mean[k]);}
    const reference=model.initialView;
    if(reference&&(!reference.eye?.every(Number.isFinite)||!reference.target?.every(Number.isFinite)||reference.eye.length!==3||reference.target.length!==3))throw Error('参考相机无效');
    const center=reference?reference.target:lo.map((v,k)=>(v+hi[k])/2),extent=Math.max(.1,Math.hypot(...sub(hi,lo)));
    const offset=reference?sub(reference.eye,center):null;
    const initialRadius=offset?Math.hypot(...offset):extent*1.5;
    if(!Number.isFinite(initialRadius)||initialRadius<1e-6)throw Error('参考相机方向无效');
    const initialYaw=offset?Math.atan2(offset[0],offset[2]):0;
    const initialPitch=offset?Math.asin(Math.max(-1,Math.min(1,offset[1]/initialRadius))):.15;
    const zoomExtent=reference?initialRadius:extent;
    const calibrated=reference?.cameraToWorld?.length===16&&reference?.intrinsics?.length===9;
    if(calibrated&&(!reference.cameraToWorld.every(Number.isFinite)||!reference.intrinsics.every(Number.isFinite)||!(reference.width>0&&reference.height>0)))throw Error('参考相机参数无效');
    const initialEye=center.map((v,i)=>v+[initialRadius*Math.sin(initialYaw)*Math.cos(initialPitch),initialRadius*Math.sin(initialPitch),initialRadius*Math.cos(initialYaw)*Math.cos(initialPitch)][i]);
    const initialForward=unit(sub(center,initialEye));
    const initialBase=calibrated&&Math.hypot(...cross(initialForward,[0,1,0]))<1e-8
      ?{rows:[reference.cameraToWorld.slice(0,3),reference.cameraToWorld.slice(4,7).map(v=>-v),reference.cameraToWorld.slice(8,11).map(v=>-v)]}
      :camera(initialEye,center,1,1,1);
    const exactRight=calibrated?reference.cameraToWorld.slice(0,3):initialBase.rows[0];
    const roll=Math.atan2(dot(exactRight,initialBase.rows[1]),dot(exactRight,initialBase.rows[0]));
    let yaw=initialYaw,pitch=initialPitch,radius=initialRadius,drag=null,referencePose=true;
    const gpu=gaussianGPU(canvas,model);
    function draw() {
      canvas.width=Math.max(128,Math.round(canvas.clientWidth||800));canvas.height=calibrated?Math.round(canvas.width*reference.height/reference.width):(canvas.height||500);
      const eye=center.map((v,i)=>v+[radius*Math.sin(yaw)*Math.cos(pitch),radius*Math.sin(pitch),radius*Math.cos(yaw)*Math.cos(pitch)][i]);
      const cam=calibrated&&referencePose
        ?{eye:reference.eye,rows:initialBase.rows,focal:canvas.height*.9,width:canvas.width,height:canvas.height}
        :camera(eye,center,canvas.height*.9,canvas.width,canvas.height);
      if(calibrated){
        const [right,down,forward]=cam.rows;
        cam.rows=[right.map((v,i)=>v*Math.cos(roll)+down[i]*Math.sin(roll)),down.map((v,i)=>v*Math.cos(roll)-right[i]*Math.sin(roll)),forward];
        if(referencePose){
          // Initial/reset view uses the full measured basis, including near-vertical
          // poses where a world-up orbit camera has an ambiguous roll.
          const t=reference.cameraToWorld;
          cam.eye=t.slice(12,15).map((v,i)=>center[i]+(v-center[i])*radius/initialRadius);
          cam.rows=[t.slice(0,3),t.slice(4,7).map(v=>-v),t.slice(8,11).map(v=>-v)];
        }
        cam.fx=reference.intrinsics[0]*canvas.width/reference.width;
        cam.fy=reference.intrinsics[4]*canvas.height/reference.height;
        cam.cx=(reference.intrinsics[6]+.5)*canvas.width/reference.width-.5;
        cam.cy=(reference.intrinsics[7]+.5)*canvas.height/reference.height-.5;
        if(reference.renderCalibration){
          const k=reference.renderCalibration;
          if(!['fl_x','fl_y','cx','cy','w','h'].every(name=>Number.isFinite(k[name]))||k.fl_x<=0||k.fl_y<=0||k.w<=0||k.h<=0)throw Error('训练器相机参数无效');
          cam.fx=k.fl_x*canvas.width/k.w;cam.fy=k.fl_y*canvas.height/k.h;
          cam.cx=k.cx*canvas.width/k.w;cam.cy=k.cy*canvas.height/k.h;
        }
      }
      const projected=model.means.map((p,i)=>{const q=project(p,model.logScales[i],model.rotations[i],cam);return q&&{...q,i};}).filter(Boolean).sort((a,b)=>b.z-a.z);
      gpu.draw(projected,model,cam.eye);
    }
    const down=e=>{drag=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId);};
    const move=e=>{if(!drag)return;referencePose=false;yaw+=(e.clientX-drag[0])*.008;pitch=Math.max(-1.4,Math.min(1.4,pitch+(e.clientY-drag[1])*.008));drag=[e.clientX,e.clientY];draw();};
    const up=()=>{drag=null;};
    const wheel=e=>{e.preventDefault();radius=Math.max(zoomExtent*.05,Math.min(zoomExtent*20,radius*Math.exp(e.deltaY*.001)));draw();};
    canvas.addEventListener('pointerdown',down);canvas.addEventListener('pointermove',move);canvas.addEventListener('pointerup',up);canvas.addEventListener('pointercancel',up);canvas.addEventListener('wheel',wheel,{passive:false});
    this.reset=()=>{referencePose=true;yaw=initialYaw;pitch=initialPitch;radius=initialRadius;draw();};
    let observer=null;
    this.disposeCurrent=()=>{gpu.dispose();observer?.disconnect();canvas.removeEventListener('pointerdown',down);canvas.removeEventListener('pointermove',move);canvas.removeEventListener('pointerup',up);canvas.removeEventListener('pointercancel',up);canvas.removeEventListener('wheel',wheel);};
    try {observer=typeof ResizeObserver==='function'?new ResizeObserver(draw):null;observer?.observe(canvas);draw();this.draw=draw;}
    catch(error){this.disposeCurrent();this.disposeCurrent=null;canvas.width=canvas.width;throw error;}
    }
    reset() {}
    dispose() {this.disposeCurrent?.();}
  }
  const api={quaternionMatrix,camera,project,validateModel,sphericalHarmonicColor,colorForView,packSHTexture,SpatialSplatViewer};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.SpatialSplatViewer=SpatialSplatViewer;
})(typeof window!=='undefined'?window:globalThis);
