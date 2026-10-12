"""Independent RGB/pose epipolar consistency; not scale or drift ground truth."""
from __future__ import annotations
import time
from pathlib import Path
import numpy as np
from PIL import Image


def features(path, maximum=160, expected_size=None):
    image=Image.open(path).convert('L')
    if expected_size is not None and tuple(image.size) != tuple(expected_size):
        raise ValueError('native_image_dimensions_mismatch')
    native=np.array(image.size,dtype=float)
    image.thumbnail((320,320))
    a=np.asarray(image,dtype=float)/255
    scale=np.array(image.size,dtype=float)/native
    gy,gx=np.gradient(a)
    def box(v):
        return sum(np.roll(np.roll(v,i,0),j,1) for i in range(-2,3) for j in range(-2,3))/25
    xx,yy,xy=box(gx*gx),box(gy*gy),box(gx*gy)
    score=(xx*yy-xy*xy)-.04*(xx+yy)**2
    score[:6]=0;score[-6:]=0;score[:,:6]=0;score[:,-6:]=0
    points=[];patches=[]
    for _ in range(maximum):
        y,x=np.unravel_index(np.argmax(score),score.shape)
        if score[y,x]<1e-6:break
        patch=a[y-4:y+5,x-4:x+5].ravel();norm=np.linalg.norm(patch-patch.mean())
        if norm>.1:
            points.append([(x+.5)/scale[0]-.5,(y+.5)/scale[1]-.5])
            patches.append((patch-patch.mean())/norm)
        score[max(0,y-6):y+7,max(0,x-6):x+7]=0
    return np.asarray(points),np.asarray(patches)


def matches(a,b):
    if len(a)<2 or len(b)<2:return np.empty((0,2),dtype=int)
    distance=np.maximum(0,2-2*(a@b.T))
    nearest=np.argsort(distance,axis=1)[:,:2]
    reverse=np.argmin(distance,axis=0)
    return np.array([(i,int(js[0])) for i,js in enumerate(nearest) if reverse[js[0]]==i and distance[i,js[0]]<.7 and distance[i,js[0]]<.75**2*distance[i,js[1]]],dtype=int).reshape(-1,2)


def fit_fundamental(points_a, points_b):
    def normalize(p):
        center=p.mean(axis=0);scale=np.sqrt(2)/max(1e-12,np.mean(np.linalg.norm(p-center,axis=1)))
        transform=np.array([[scale,0,-scale*center[0]],[0,scale,-scale*center[1]],[0,0,1]])
        return (np.c_[p,np.ones(len(p))]@transform.T)[:,:2],transform
    a,ta=normalize(points_a);b,tb=normalize(points_b)
    x,y=a.T;u,v=b.T
    design=np.c_[u*x,u*y,u,v*x,v*y,v,x,y,np.ones(len(a))]
    # Planar and pure rotation data may make F non-unique; use image homography fallback below.
    if np.linalg.matrix_rank(design)<8:raise ValueError('degenerate_matches')
    _,_,vt=np.linalg.svd(design,full_matrices=len(a)==8)
    f=vt[-1].reshape(3,3);left,singular,right=np.linalg.svd(f);singular[-1]=0
    f=left@np.diag(singular)@right
    result=tb.T@f@ta
    return result/max(1e-15,np.linalg.norm(result))


def sampson(f,a,b):
    x=np.c_[a,np.ones(len(a))];y=np.c_[b,np.ones(len(b))]
    fx=x@f.T;fty=y@f
    return np.sqrt(np.sum(y*fx,axis=1)**2/np.maximum(1e-20,np.sum(fx[:,:2]**2,axis=1)+np.sum(fty[:,:2]**2,axis=1)))


def homography_inliers(a,b,threshold=3.0):
    best=np.zeros(len(a),dtype=bool);rng=np.random.default_rng(43)
    if len(a)<4:return best
    center=a.mean(axis=0);scale=max(1,np.std(a));aa=(a-center)/scale
    cb=b.mean(axis=0);sb=max(1,np.std(b));bb=(b-cb)/sb
    for _ in range(200):
        ids=rng.choice(len(a),4,replace=False);rows=[]
        for (x,y),(u,v) in zip(aa[ids],bb[ids]):
            rows.extend([[-x,-y,-1,0,0,0,u*x,u*y,u],[0,0,0,-x,-y,-1,v*x,v*y,v]])
        design=np.asarray(rows)
        if np.linalg.matrix_rank(design)<8:continue
        _,_,vt=np.linalg.svd(design,full_matrices=True);h=vt[-1].reshape(3,3)
        projected=np.c_[aa,np.ones(len(a))]@h.T
        valid=np.abs(projected[:,2])>1e-9
        error=np.full(len(a),np.inf)
        error[valid]=np.linalg.norm((projected[valid,:2]/projected[valid,2,None])*sb+cb-b[valid],axis=1)
        mask=error<=threshold
        if mask.sum()>best.sum():best=mask
    return best


def independent_inliers(a,b,threshold=3.0,iterations=300):
    """Image-only F RANSAC: threshold in native pixels, seed 42."""
    best=np.zeros(len(a),dtype=bool)
    if len(a)<8:return best
    rng=np.random.default_rng(42)
    for _ in range(iterations):
        indices=rng.choice(len(a),8,replace=False)
        try:
            f=fit_fundamental(a[indices],b[indices]);mask=sampson(f,a,b)<=threshold
        except (ValueError,np.linalg.LinAlgError):continue
        if mask.sum()>best.sum():best=mask
    if best.sum()>=8:
        try:
            f=fit_fundamental(a[best],b[best]);refined=sampson(f,a,b)<=threshold
            if refined.sum()>=best.sum():best=refined
        except (ValueError,np.linalg.LinAlgError):pass
    return best


def sift_features(path, expected_size=None):
    from .spatial_sift_native import extract
    image=Image.open(path).convert('L')
    if expected_size is not None and tuple(image.size)!=tuple(expected_size):
        raise ValueError('native_image_dimensions_mismatch')
    native=np.array(image.size,dtype=float);image.thumbnail((640,640))
    scale=np.array(image.size,dtype=float)/native
    keys,descriptors=extract(np.asarray(image), maximum=800)
    points=(keys[:,:2]+.5)/scale-.5
    return points,descriptors


def sift_matches(a,b):
    if len(a)<2 or len(b)<2:return np.empty((0,2),dtype=int)
    a,b=np.asarray(a,dtype=np.float64),np.asarray(b,dtype=np.float64)
    distances=np.maximum(0,np.sum(a*a,axis=1)[:,None]+np.sum(b*b,axis=1)[None,:]-2*a@b.T)
    forward=np.argsort(distances,axis=1)[:,:2];back=np.argsort(distances,axis=0)[:2,:]
    output=[]
    for i,(j,next_j) in enumerate(forward):
        first,second=back[:,j]
        if first==i and distances[i,j]<.75**2*distances[i,next_j] and distances[i,j]<.75**2*distances[second,j]:output.append((i,int(j)))
    return np.asarray(output,dtype=int).reshape(-1,2)


def cv_support(a,b):
    # Independent geometry remains inspectable NumPy code, not part of SIFT.
    support=independent_inliers(a,b)
    hm=homography_inliers(a,b)
    method='fundamental_matrix'
    if hm.sum()>support.sum():support=hm;method='homography_planar_or_rotation'
    return support,method


def diagnose(document, directory, maximum_pairs=20, minimum_matches=12, feature_method="sift"):
    started=time.perf_counter();frames=document.get('keyframes',[])
    count=min(maximum_pairs,max(0,len(frames)-1))
    selected=np.unique(np.linspace(0,len(frames)-2,count,dtype=int)).tolist() if count else []
    results=[];cache={}
    dependency_error=None
    if feature_method=='sift':
        try:
            from .spatial_sift_native import library
            library()
        except RuntimeError as exc:dependency_error=str(exc).split(':',1)[0]
    elif feature_method!='patch':raise ValueError('unknown_feature_method')
    for index in selected:
        a,b=frames[index:index+2]
        item={'sourceKeyframeIndices':[index,index+1],'startTime':a.get('time'),'endTime':b.get('time'),'status':'failed'}
        try:
            if dependency_error:
                item.update(status='not_available',reason=dependency_error);results.append(item);continue
            if a['pose']['epoch']!=b['pose']['epoch']:
                item.update(status='not_assessable',reason='different_epoch');results.append(item);continue
            for frame in (a,b):
                name=frame['imageFile'];path=Path(directory)/name
                if path.resolve().parent!=Path(directory).resolve():raise ValueError('unsafe_image_path')
                if name not in cache:cache[name]=(sift_features if feature_method=='sift' else features)(path, expected_size=(frame['width'],frame['height']))
            pa,da=cache[a['imageFile']];pb,db=cache[b['imageFile']]
            m=(sift_matches if feature_method=='sift' else matches)(da,db);item.update(cornerCounts=[len(pa),len(pb)],matchCount=len(m))
            if len(m)<minimum_matches:
                item.update(status='not_assessable',reason='insufficient_texture_or_matches');results.append(item);continue
            if feature_method=='sift':
                image_inliers,support_method=cv_support(pa[m[:,0]],pb[m[:,1]])
            else:
                image_inliers=independent_inliers(pa[m[:,0]],pb[m[:,1]])
                support_method='fundamental_matrix'
                homography_support=homography_inliers(pa[m[:,0]],pb[m[:,1]])
                if homography_support.sum()>image_inliers.sum():
                    image_inliers=homography_support;support_method='homography_planar_or_rotation'
            item['imageIndependentSupportMethod']=support_method
            item.update(imageIndependentInlierCount=int(image_inliers.sum()),imageIndependentInlierFraction=float(image_inliers.mean()),imageRansacThresholdNativePixels=3.0,imageRansacIterations=300)
            ka=np.array(a['intrinsics']).reshape(3,3,order='F');kb=np.array(b['intrinsics']).reshape(3,3,order='F')
            ta=np.array(a['cameraToWorld']).reshape(4,4,order='F');tb=np.array(b['cameraToWorld']).reshape(4,4,order='F')
            if not all(np.isfinite(v).all() for v in (ka,kb,ta,tb)):
                raise ValueError('non_finite_camera_parameters')
            if ka[0,0]<=0 or ka[1,1]<=0 or kb[0,0]<=0 or kb[1,1]<=0:
                raise ValueError('invalid_focal_length')
            # ARKit camera (-Z forward, +Y up) -> OpenCV (+Z forward, +Y down).
            axes=np.diag([1,-1,-1,1]);relative=axes@np.linalg.inv(tb)@ta@axes
            r,t=relative[:3,:3],relative[:3,3];baseline=float(np.linalg.norm(t));item['baselineMeters']=baseline
            if baseline<.02:
                h=kb@r@np.linalg.inv(ka)
                x=np.c_[pa[m[:,0]],np.ones(len(m))]
                projected=x@h.T
                usable=np.abs(projected[:,2])>1e-9
                if not usable.all():
                    raise ValueError('rotation_projection_at_infinity')
                residual=np.linalg.norm(projected[:,:2]/projected[:,2,None]-pb[m[:,1]],axis=1)
                item.update(rawPoseResidual={'method':'rotation_prediction','medianPixels':float(np.median(residual)),'p95Pixels':float(np.percentile(residual,95))})
                if image_inliers.sum()<minimum_matches or image_inliers.mean()<.5:
                    item.update(status='not_assessable',reason='unreliable_matches');results.append(item);continue
                residual=residual[image_inliers]
                item.update(status='measured',method='rotation_prediction',medianRotationPixels=float(np.median(residual)),p95RotationPixels=float(np.percentile(residual,95)),within3PixelsFraction=float(np.mean(residual<=3)),limitation='低基线旋转预测一致性；近物体真实平移及误匹配会增加残差，不能作为平移或尺度真值')
                results.append(item);continue
            tx=np.array([[0,-t[2],t[1]],[t[2],0,-t[0]],[-t[1],t[0],0]])
            f=np.linalg.inv(kb).T@tx@r@np.linalg.inv(ka)
            x=np.c_[pa[m[:,0]],np.ones(len(m))];y=np.c_[pb[m[:,1]],np.ones(len(m))]
            fx=x@f.T;fty=y@f;n=np.sum(y*fx,axis=1)**2
            residual=np.sqrt(n/np.maximum(1e-20,np.sum(fx[:,:2]**2,axis=1)+np.sum(fty[:,:2]**2,axis=1)))
            item.update(rawPoseResidual={'method':'epipolar','medianPixels':float(np.median(residual)),'p95Pixels':float(np.percentile(residual,95))})
            if image_inliers.sum()<minimum_matches or image_inliers.mean()<.5:
                item.update(status='not_assessable',reason='unreliable_matches');results.append(item);continue
            residual=residual[image_inliers]
            item.update(status='measured',method='epipolar',medianSampsonPixels=float(np.median(residual)),p95SampsonPixels=float(np.percentile(residual,95)),within3PixelsFraction=float(np.mean(residual<=3)))
        except (OSError,KeyError,ValueError,TypeError,RuntimeError,np.linalg.LinAlgError) as exc:
            item.update(reason=type(exc).__name__+': '+str(exc))
        results.append(item)
    return {'status':'diagnostics_only','originalPreserved':True,'scaleVerified':False,'driftGroundTruthMeasured':False,'featureMethod':feature_method,'method':('vendored OpenCV 4.11 SIFT C ABI / 800 features / 640px + mutual ratio + independent NumPy F/H RANSAC' if feature_method=='sift' else 'Harris 9x9 patch baseline'),'message':'使用真实图像匹配核对姿态一致性；未验证尺度或绝对漂移。低纹理、低基线及失败帧对均计入选中分母。独立几何支持仍不保证物理匹配身份；旋转预测受近物体真实平移影响。','selectedPairCount':len(results),'measuredPairCount':sum(p['status']=='measured' for p in results),'pairs':results,'elapsedMilliseconds':(time.perf_counter()-started)*1000}
