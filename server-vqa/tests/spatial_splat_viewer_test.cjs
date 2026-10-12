const assert=require('node:assert/strict');
const test=require('node:test');
const {camera,project,quaternionMatrix,validateModel}=require('../app/spatial_splat_viewer.js');
const cam=camera([0,0,0],[0,0,1],100,200,200);
test('behind camera is invisible; world Y points upward',()=>{
 assert.equal(project([0,0,-2],[-2,-2,-2],[1,0,0,0],cam),null);
 assert(project([0,1,2],[-2,-2,-2],[1,0,0,0],cam).y<100);
});
test('covariance projection retains anisotropy and normalized quaternion',()=>{
 const p=project([0,0,2],[Math.log(.4),Math.log(.1),Math.log(.1)],[1,0,0,0],cam);
 assert(Math.abs(p.covariance[0][0]-400.3)<1e-8);
 assert(Math.abs(p.covariance[1][1]-25.3)<1e-8);
 assert(p.major>p.minor*3);
 const q=project([0,0,2],[Math.log(.4),Math.log(.1),Math.log(.1)],[2,0,0,0],cam);
 assert.deepEqual(q,p);
 assert.throws(()=>quaternionMatrix([0,0,0,0]),/rotation/);
});
test('quarter rotation swaps projected ellipse axes',()=>{
 const p=project([0,0,2],[Math.log(.4),Math.log(.1),Math.log(.1)],[Math.SQRT1_2,0,0,Math.SQRT1_2],cam);
 assert(Math.abs(p.covariance[0][0]-25.3)<1e-8);
 assert(Math.abs(p.covariance[1][1]-400.3)<1e-8);
});
test('malformed models fail visibly before rendering',()=>{
 assert.throws(()=>validateModel({means:[]}),/没有高斯/);
 assert.throws(()=>validateModel({means:[[0,0,0]]}),/字段/);
});
test('offscreen covariance follows official bounded perspective derivative without moving mean',()=>{
 const p=project([100,0,1],[Math.log(.1),Math.log(.1),Math.log(.1)],[1,0,0,0],cam);
 assert.equal(p.x,-9900);
 assert(Math.abs(p.covariance[0][0]-(100*(1+1.3**2)+.3))<1e-8);
 assert(Math.abs(p.covariance[1][1]-100.3)<1e-8);
});
const {sphericalHarmonicColor,colorForView}=require('../app/spatial_splat_viewer.js');
const coefficients=degree=>Array.from({length:(degree+1)**2},()=>[0,0,0]);
const near=(a,b)=>assert(Math.abs(a-b)<1e-12,`${a} != ${b}`);
test('Brush degree1 uses camera-to-mean direction and ARKit Y-up to Brush Z-up',()=>{
 const c=coefficients(1);c[1][0]=1;c[2][1]=1;c[3][2]=1;
 // Native forward +Z becomes Brush -Y, giving positive red SH1.
 const forward=sphericalHarmonicColor(c,1,[0,0,2]);
 near(forward[0],.5+.48860251190292);near(forward[1],.5);near(forward[2],.5);
 const upward=sphericalHarmonicColor(c,1,[0,2,0]);near(upward[1],.5+.48860251190292);
 const model={shDegree:1,shCoefficients:[c],means:[[0,0,2]]};
 assert.deepEqual(colorForView(model,0,[0,0,0]),forward);
 assert.notDeepEqual(colorForView(model,0,[0,0,4]),forward);
});
test('Brush degree2 and degree3 coefficient ordering agrees with official axis values',()=>{
 for(let index=4;index<16;index++){
  const degree=index<9?2:3,c=coefficients(degree);c[index][0]=.1;
  // Brush +Z is ARKit +Y. Only coefficients 6 and 12 are nonzero on this pole.
  const expected=index===6?.63078313050504:index===12?.746352665180231:0;
  near(sphericalHarmonicColor(c,degree,[0,1,0])[0],.5+.1*expected);
 }
 const c=coefficients(3);c[9][0]=.2;c[15][1]=.3;
 // Brush +X cubic sine is zero, cosine basis is -0.5900435899266435.
 const rgb=sphericalHarmonicColor(c,3,[1,0,0]);
 near(rgb[0],.5);near(rgb[1],.5-.3*.5900435899266435);
});
test('SH0 radiance has lower clamp only and legacy colors stay unchanged',()=>{
 const c=[[5,-5,.3141592653589793]];
 const rgb=sphericalHarmonicColor(c,0,[1,0,0]);
 near(rgb[0],.5+5*.2820947917738781);assert(rgb[0]>1);assert.equal(rgb[1],0);
 const legacy={colors:[[1.927,.2,.3]],means:[[0,0,2]]};
 assert.strictEqual(colorForView(legacy,0,[0,0,0]),legacy.colors[0]);
});
test('SH metadata is strict; missing coefficients never silently fall back to SH0',()=>{
 const model={means:[[0,0,2]],logScales:[[-2,-2,-2]],rotations:[[1,0,0,0]],colors:[[.5,.5,.5]],opacities:[.5],shDegree:3};
 assert.throws(()=>validateModel(model),/坐标/);
 model.shCoordinateConvention='Brush_Z_up';assert.throws(()=>validateModel(model),/不完整/);
 model.shCoefficients=[coefficients(3)];validateModel(model);
 model.shCoefficients[0].pop();assert.throws(()=>validateModel(model),/无效/);
 assert.throws(()=>sphericalHarmonicColor(coefficients(1),1,[0,0,0]),/direction/);
});
test('recorded real official Brush export DC coefficients preserve native radiance',()=>{
 // First Gaussian coefficients recorded from a real official Brush SH0 export.
 // No frame, scan, device identifier or image is stored in this scalar fixture.
 const real=[[-1.3976950645446777,-1.6338623762130737,-1.7133750915527344]];
 const expected=[.10571750180389206,.03909593319499932,.016665810317882113];
 const actual=sphericalHarmonicColor(real,0,[.2,-.4,.8]);
 actual.forEach((v,i)=>near(v,expected[i]));
});
const {packSHTexture}=require('../app/spatial_splat_viewer.js');
test('GPU immutable SH texture preserves original identity across a sorted draw order',()=>{
 const model={shDegree:3,means:[[1,2,3],[-4,5,6]],shCoefficients:[coefficients(3),coefficients(3)]};
 model.shCoefficients[0][15]=[.125,-.25,2];model.shCoefficients[1][0]=[-1,.5,3];
 const texture=packSHTexture(model,8);assert.equal(texture.stride,17);
 assert.equal(texture.width,8);assert.equal(texture.height,5);
 // Depth sorting [1,0] must still fetch original mean and coefficient, including row crossings.
 for(const id of [1,0]){
  assert.deepEqual(Array.from(texture.data.slice(id*17*4,id*17*4+3)),model.means[id]);
  for(let c=0;c<16;c++)assert.deepEqual(Array.from(texture.data.slice((id*17+1+c)*4,(id*17+1+c)*4+3)),model.shCoefficients[id][c]);
 }
 assert.equal(texture.data.byteLength,8*5*16);
});
test('SH GPU resources reject dimension, index precision and float overflow without truncation',()=>{
 const model={shDegree:3,means:[[0,0,1]],shCoefficients:[coefficients(3)]};
 assert.throws(()=>packSHTexture(model,4),/容量不足/);
 assert.throws(()=>packSHTexture(model,NaN),/尺寸/);
 assert.throws(()=>packSHTexture({...model,means:{length:2**24+1}},4096),/索引/);
 model.shCoefficients[0][4][0]=1e100;
 assert.throws(()=>packSHTexture(model,8),/单精度/);
});
test('legacy color path receives only a complete dummy sampler; coefficient SH0 remains distinct',()=>{
 const legacy=packSHTexture({means:[[0,0,1]],colors:[[1.9,.2,.3]]},4096);
 assert.equal(legacy.degree,-1);assert.equal(legacy.stride,0);assert.equal(legacy.data.byteLength,16);
 const dc=packSHTexture({means:[[0,0,1]],shDegree:0,shCoefficients:[[[5,-5,.25]]]},8);
 assert.equal(dc.degree,0);assert.equal(dc.stride,2);
 assert.deepEqual(Array.from(dc.data.slice(4,7)),[5,-5,.25]);
});
