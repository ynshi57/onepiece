"""Isolated official SuperPoint/LightGlue CPU experiment, train-only, no map claims.

Requires externally verified source/weight manifests in --resources; never downloads
or installs dependencies. SuperPoint has a restrictive noncommercial research license.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time
import numpy as np

REVISION='eb42fee2d71449efb0aa5c10549752b5d75384d8'

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def native_points(points,scale):return (np.asarray(points)+.5)/np.asarray(scale)-.5

def plan_pairs(train, evaluation, *, limit=None, failed=(), components=None, zero_features=(), offsets=(1,2)):
    if set(train)&set(evaluation) or len(set(train))!=len(train):raise ValueError('invalid_training_partition')
    if not offsets or any(not isinstance(d,int) or d<=0 for d in offsets):raise ValueError('positive_training_sequence_offsets_required')
    pairs=sorted({(train[a],train[a+d]) for d in offsets for a in range(max(0,len(train)-d))})
    if limit is not None:
        failure=set(failed);zero=set(zero_features);components=components or {}
        def rank(pair):
            a,b=pair
            return (0 if a in zero or b in zero else 1,
                    0 if a in components and b in components and components[a]!=components[b] else 1,
                    0 if a in failure or b in failure else 1,abs(a-b),a,b)
        pairs=sorted(pairs,key=rank)[:limit]
    return pairs

def verify_resources(root, *, features_only=False):
    root=Path(root);source=json.loads((root/'source-verification.json').read_text())
    if source['revision']!=REVISION or not source.get('files'):raise ValueError('pinned_source_hash_manifest_required')
    for row in source['files']:
        path=root/'source'/row['path']
        if digest(path)!=row['sha256']:raise ValueError('source_sha256_mismatch')
    weights=json.loads((root/'weight-manifest.json').read_text());result={}
    for row in weights:
        path=Path(row['path'])
        if path.stat().st_size!=row['size'] or digest(path)!=row['sha256']:raise ValueError('weight_digest_mismatch')
        result[row['name']]=row
    required={'superpoint_v1'} if features_only else {'superpoint_v1','superpoint_lightglue'}
    if not required.issubset(result) or set(result)-{'superpoint_v1','superpoint_lightglue'}:raise ValueError('required_weights_missing')
    return dict(sourceRevision=REVISION,source=source,weights=weights,
                license='LightGlue Apache-2.0; SuperPoint restrictive noncommercial internal research',
                productAccepted=False)

def descriptor_mutual_ratio(a,b,ratio=.75):
    """Mutual Euclidean NN with bidirectional Lowe ratio; NOT LightGlue."""
    a,b=np.asarray(a,float),np.asarray(b,float)
    if len(a)<2 or len(b)<2:return np.empty((0,2),dtype=int)
    if a.ndim!=2 or b.ndim!=2 or a.shape[1]!=b.shape[1] or not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('invalid_descriptors')
    a=a/np.maximum(np.linalg.norm(a,axis=1,keepdims=True),1e-12);b=b/np.maximum(np.linalg.norm(b,axis=1,keepdims=True),1e-12)
    distances=np.maximum(0,np.sum(a*a,1)[:,None]+np.sum(b*b,1)[None,:]-2*a@b.T)
    forward=np.argsort(distances,axis=1)[:,:2];back=np.argsort(distances,axis=0)[:2,:];pairs=[]
    for i,(j,j2) in enumerate(forward):
        i1,i2=back[:,j]
        if i1==i and distances[i,j]<ratio**2*distances[i,j2] and distances[i,j]<ratio**2*distances[i2,j]:pairs.append((i,int(j)))
    return np.asarray(pairs,dtype=int).reshape(-1,2)

def atomic_npz(path, **arrays):
    path=Path(path);temporary=path.with_suffix('.partial')
    with temporary.open('wb') as stream:np.savez_compressed(stream,**arrays)
    temporary.replace(path)

def geometric_check(frame_a,frame_b,points_a,points_b,matches):
    import pycolmap
    if not len(matches):return dict(status='no_matches',inlierCount=0,config=0)
    def cam(frame):
        k=np.asarray(frame['intrinsics']).reshape(3,3,order='F')
        return pycolmap.Camera(model='PINHOLE',width=frame['width'],height=frame['height'],params=[k[0,0],k[1,1],k[0,2]+.5,k[1,2]+.5])
    options=pycolmap.TwoViewGeometryOptions();options.ransac.max_error=3.;pycolmap.set_random_seed(42)
    geometry=pycolmap.estimate_calibrated_two_view_geometry(cam(frame_a),np.asarray(points_a,float)+.5,cam(frame_b),np.asarray(points_b,float)+.5,np.asarray(matches,dtype=np.uint32),options)
    inliers=np.asarray(geometry.inlier_matches,dtype=np.uint32).reshape(-1,2)
    return dict(status='verified_attempt',inlierCount=len(inliers),config=int(geometry.config),inlierMatches=inliers.tolist(),
                ransacMaxErrorPixels=3.,pixelConvention='COLMAP_corner_points_and_K_plus_half')

def _worker(connection,root,kind,threads):
    try:
        sys.path.insert(0,str(Path(root)/'python-deps'));sys.path.insert(0,str(Path(root)/'source'))
        import torch
        torch.set_num_threads(threads)
        provenance=verify_resources(root,features_only=kind in ('feature','descriptor'));weights={r['name']:r['path'] for r in provenance['weights']}
        def local_weights(url,**kwargs):
            name='superpoint_v1' if url.endswith('/superpoint_v1.pth') else 'superpoint_lightglue'
            if not url.endswith('/'+name+'.pth'):raise ValueError('unexpected_weight_request')
            return torch.load(weights[name],map_location='cpu',weights_only=True)
        torch.hub.load_state_dict_from_url=local_weights
        from lightglue import SuperPoint,LightGlue
        model=(SuperPoint(max_num_keypoints=1024).eval().cpu() if kind=='feature' else LightGlue(features='superpoint',flash=False,mp=False).eval().cpu() if kind=='match' else None)
        if kind=='match':model.load_state_dict(torch.load(weights['superpoint_lightglue'],map_location='cpu',weights_only=True),strict=True)
        connection.send({'ready':True})
        while True:
            task=connection.recv()
            if task is None:break
            start=time.monotonic()
            try:
                with torch.inference_mode():
                    if kind=='feature':
                        from PIL import Image
                        with Image.open(task['image']) as original:
                            size=original.size
                            if list(size)!=task['expectedSize']:raise ValueError('native_image_dimensions_mismatch')
                            image=original.convert('RGB');image.thumbnail((640,640),Image.Resampling.BILINEAR)
                            resized=image.size;pixels=np.asarray(image).copy()
                        tensor=torch.from_numpy(pixels).permute(2,0,1).float()/255
                        output=model.extract(tensor,resize=None)
                        points=native_points(output['keypoints'][0].numpy(),np.asarray(resized)/size)
                        atomic_npz(task['output'],points=points,descriptors=output['descriptors'][0].numpy(),scores=output['keypoint_scores'][0].numpy(),image_size=np.asarray(size),cacheKey=np.asarray(task['cacheKey']))
                        result=dict(status='extracted',keypointCount=len(points),nativeSize=size,resizedSize=resized)
                    else:
                        fa=np.load(task['featureA']);fb=np.load(task['featureB'])
                        def features(f):return {'keypoints':torch.from_numpy(f['points']).float()[None], 'descriptors':torch.from_numpy(f['descriptors']).float()[None], 'image_size':torch.from_numpy(f['image_size']).float()[None]}
                        if kind=='descriptor':
                            matches=descriptor_mutual_ratio(fa['descriptors'],fb['descriptors']);scores=np.empty(0)
                        else:
                            output=model({'image0':features(fa),'image1':features(fb)})
                            matches=output['matches'][0].numpy();scores=output['scores'][0].numpy()
                        atomic_npz(task['output'],matches=matches,scores=scores)
                        result=dict(status='matched',matchCount=len(matches),matchingElapsedSeconds=time.monotonic()-start,
                                    geometry=geometric_check(task['frameA'],task['frameB'],fa['points'],fb['points'],matches))
                        if task.get('siftControl'):
                            control=Path(task['siftControl']);a,b=task['indices'];sa=np.load(control/f'features-{a}.npz');sb=np.load(control/f'features-{b}.npz')
                            sm=np.load(control/f'matches-{a}-{b}.npy')
                            result['siftControl']=dict(matchCount=len(sm),geometry=geometric_check(task['frameA'],task['frameB'],sa['points'],sb['points'],sm))
                result['elapsedSeconds']=time.monotonic()-start;connection.send(result)
            except Exception as error:connection.send(dict(status='failed',reason=type(error).__name__,elapsedSeconds=time.monotonic()-start))
    except Exception as error:connection.send(dict(ready=False,reason=type(error).__name__))
    finally:connection.close()

class TimedWorker:
    def __init__(self,root,kind,threads=2):self.root=root;self.kind=kind;self.threads=threads;self.process=None
    def start(self):
        context=mp.get_context('spawn');self.connection,other=context.Pipe()
        self.process=context.Process(target=_worker,args=(other,self.root,self.kind,self.threads));self.process.start();other.close()
        if not self.connection.poll(90):self.close();return {'status':'initialization_timeout'}
        message=self.connection.recv()
        if not message.get('ready'):self.close();return dict(status='initialization_failed',reason=message.get('reason'))
        return None
    def call(self,task,timeout):
        if self.process is None:
            failure=self.start()
            if failure:return failure
        self.connection.send(task)
        if not self.connection.poll(timeout):self.close();return dict(status='timeout',timeoutSeconds=timeout)
        try:return self.connection.recv()
        except EOFError:self.close();return dict(status='worker_exited')
    def close(self):
        if self.process is not None:
            if self.process.is_alive():
                try:self.connection.send(None)
                except (BrokenPipeError,EOFError,OSError):pass
                self.process.join(.5)
            if self.process.is_alive():self.process.terminate()
            self.process.join(5)
            if self.process.is_alive():self.process.kill();self.process.join(5)
            self.connection.close();self.process=None

def run(args):
    if args.limit_pairs is not None and args.limit_pairs<=0:raise ValueError('positive_pair_limit_required')
    if min(args.feature_timeout,args.pair_timeout)<=0:raise ValueError('positive_cpu_deadline_required')
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'server-vqa'))
    from app.spatial_splat_dataset import partition_epoch_frames
    data=Path(args.data);document=json.loads((data/'document.json').read_text());training,evaluation=partition_epoch_frames(document,args.epoch)
    train=[i for i,_ in training];excluded=[i for i,_ in evaluation];provenance=verify_resources(args.resources,features_only=args.features_only or args.matcher=='descriptor_mutual_ratio')
    control=Path(args.sift_control);baseline=json.loads((control/'result.json').read_text());inputs=json.loads((control/'inputs.json').read_text())
    if set(inputs['trainingIndices'])!=set(train) or set(inputs['evaluationExcluded'])!=set(excluded):raise ValueError('control_partition_mismatch')
    component={int(Path(name).stem):c['id'] for c in baseline['components'] for name in c['registeredNames']}
    zeros=[f['index'] for f in inputs['features'] if f['keypoints']==0]
    offsets=tuple(range(1,len(train))) if args.pair_offsets=='all' else tuple(int(d) for d in args.pair_offsets.split(','))
    pairs=plan_pairs(train,excluded,limit=args.limit_pairs,failed=baseline['unregisteredTrainingFrames'],components=component,zero_features=zeros,offsets=offsets)
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True);(out/'features').mkdir(exist_ok=True);(out/'matches').mkdir(exist_ok=True)
    summary=dict(inputDocumentSHA256=digest(data/'document.json'),siftControlInputsSHA256=digest(control/'inputs.json'),matcher=args.matcher,descriptorRatio=.75 if args.matcher=='descriptor_mutual_ratio' else None,trainingIndices=train,evaluationExcluded=excluded,provenance=provenance,features=[],pairs=[],productAccepted=False,mapReconstructed=False,featureWidth=640,maxKeypoints=1024,pairPolicy='training_sequence_offsets',pairOffsets=list(offsets),featureTimeoutSeconds=args.feature_timeout,pairTimeoutSeconds=args.pair_timeout)
    def save():(out/'result.json').write_text(json.dumps(summary,indent=2,allow_nan=False))
    worker=TimedWorker(args.resources,'feature');available={};start=time.monotonic()
    try:
        for index,frame in training:
            image=data/frame['imageFile'];feature_provenance={'source':provenance['source'],'extractorWeight':next(r for r in provenance['weights'] if r['name']=='superpoint_v1')}
            cache=digest(image)+hashlib.sha256(json.dumps(feature_provenance,sort_keys=True).encode()).hexdigest();path=out/'features'/f'{index}.npz'
            if args.feature_cache and (Path(args.feature_cache)/f'{index}.npz').exists():path=Path(args.feature_cache)/f'{index}.npz'
            cached=False
            if path.exists():
                with np.load(path) as f:cached=str(f['cacheKey'])==cache and f['image_size'].tolist()==[frame['width'],frame['height']]
            result=(dict(status='extracted',cached=True,keypointCount=len(np.load(path)['points'])) if cached else worker.call(dict(image=str(image),output=str(path),cacheKey=cache,expectedSize=[frame['width'],frame['height']]),args.feature_timeout))
            result['frameIndex']=index;result['featureFile']=str(path) if result['status']=='extracted' else None;summary['features'].append(result)
            if result['status']=='extracted':available[index]=path
            save();print(json.dumps({'feature':index,**result}),flush=True)
    finally:worker.close()
    if args.features_only:
        summary['matchingNotRun']=True;summary['elapsedSeconds']=time.monotonic()-start;save();return
    cached_pairs={}
    if args.pair_cache:
        previous=json.loads((Path(args.pair_cache)/'result.json').read_text())
        if previous.get('provenance')!=provenance or previous.get('inputDocumentSHA256')!=summary['inputDocumentSHA256'] or previous.get('siftControlInputsSHA256')!=summary['siftControlInputsSHA256'] or previous.get('matcher')!=args.matcher:raise ValueError('pair_cache_provenance_mismatch')
        cached_pairs={(p['frameA'],p['frameB']):p for p in previous['pairs'] if p['status']=='matched'}
    worker=TimedWorker(args.resources,'descriptor' if args.matcher=='descriptor_mutual_ratio' else 'match')
    try:
        for a,b in pairs:
            path=out/'matches'/f'{a}-{b}.npz'
            cached=cached_pairs.get((a,b));cached_path=Path(cached['matchFile']) if cached and cached.get('matchFile') else None
            if cached_path and cached_path.exists() and cached.get('matchSHA256')==digest(cached_path):
                import shutil
                shutil.copyfile(cached_path,path);result=dict(cached);result['cached']=True;result['cacheOriginalElapsedSeconds']=result['elapsedSeconds'];result['elapsedSeconds']=0.;result['matchingElapsedSeconds']=0.
            elif a not in available or b not in available:result=dict(status='feature_unavailable')
            else:result=worker.call(dict(featureA=str(available[a]),featureB=str(available[b]),output=str(path),frameA=document['keyframes'][a],frameB=document['keyframes'][b],indices=[a,b],siftControl=str(control)),args.pair_timeout)
            result['frameA']=a;result['frameB']=b;result['matchFile']=str(path) if path.exists() else None;result['matchSHA256']=digest(path) if path.exists() else None;summary['pairs'].append(result);save()
            print(json.dumps({k:v for k,v in result.items() if k not in ('geometry','siftControl')}),flush=True)
    finally:worker.close()
    summary['elapsedSeconds']=time.monotonic()-start;summary['allRequestedFramesRetained']=len(summary['features'])==len(train);summary['allRequestedPairsRetained']=len(summary['pairs'])==len(pairs);save()
    if args.reconstruct:
        if args.matcher!='lightglue' or any(p['status']!='matched' for p in summary['pairs']):raise ValueError('complete_lightglue_pairs_required_for_mapping')
        summary['pairGraph']=pair_graph_evidence(document,train,summary['pairs'])
        summary['siftControlPairGraph']=pair_graph_evidence(document,train,summary['pairs'],key='siftControl')
        summary['maps']=[]
        for method in ('superpoint_lightglue','sift_same_pairs'):
            print(json.dumps({'mappingStarted':method}),flush=True)
            task=dict(data=str(data),output=str(out/('map_'+method)),training=train,evaluation=excluded,pairs=pairs,feature_cache=str(out/'features'),sift_control=str(control),method=method)
            # Cache files are copied into the map input directory; identity remains per-extractor.
            import shutil
            for index,path in available.items():
                destination=out/'features'/f'{index}.npz'
                if path.resolve()!=destination.resolve():shutil.copyfile(path,destination)
            summary['maps'].append(run_map_with_deadline(task,args.map_timeout));save()

def pair_graph_evidence(document,training,records,key='geometry'):
    adjacency={i:set() for i in training};baselines=[];supported=[]
    for record in records:
        a,b=record['frameA'],record['frameB']
        if a not in adjacency or b not in adjacency:raise ValueError('pair_graph_outside_training')
        ca=np.asarray(document['keyframes'][a]['cameraToWorld']).reshape(4,4,order='F')[:3,3]
        cb=np.asarray(document['keyframes'][b]['cameraToWorld']).reshape(4,4,order='F')[:3,3]
        baseline=float(np.linalg.norm(ca-cb));baselines.append(baseline)
        geometry=record.get('geometry',{}) if key=='geometry' else record.get('siftControl',{}).get('geometry',{})
        if geometry.get('config')==2 and geometry.get('inlierCount',0)>=15:
            adjacency[a].add(b);adjacency[b].add(a);supported.append(dict(frameA=a,frameB=b,rawARBaselineMeters=baseline))
    components=[];unseen=set(training)
    while unseen:
        pending=[min(unseen)];component=[]
        while pending:
            node=pending.pop()
            if node not in unseen:continue
            unseen.remove(node);component.append(node);pending.extend(adjacency[node]&unseen)
        components.append(sorted(component))
    return dict(components=components,isolatedTrainingIndices=[i for i in training if not adjacency[i]],supportedEdges=supported,allRequestedPairBaselineMeters=baselines,baselineSource='raw_AR_prior_not_independent_measurement',edgeDefinition='calibrated_config_2_at_least_15_inliers_diagnostic_only',metricAccuracyVerified=False)

def reconstruct_pair_map(data,output,training,evaluation,pairs,feature_cache,sift_control,method):
    """Independent keypoint identities; external matches only, no fake descriptors."""
    import pycolmap
    if method not in ('superpoint_lightglue','sift_same_pairs'):raise ValueError('unknown_map_feature_identity')
    start=time.monotonic();data=Path(data);output=Path(output);output.mkdir(parents=True,exist_ok=False)
    document=json.loads((data/'document.json').read_text());images=output/'images';images.mkdir();models=output/'models';models.mkdir()
    if set(training)&set(evaluation) or any(a not in training or b not in training for a,b in pairs):raise ValueError('map_training_partition_violation')
    db=pycolmap.Database(str(output/'features.db'));identities={};records=[]
    try:
        for index in training:
            frame=document['keyframes'][index];name=f'{index:05}.jpg';(images/name).symlink_to((data/frame['imageFile']).resolve())
            k=np.asarray(frame['intrinsics']).reshape(3,3,order='F')
            camera=pycolmap.Camera(model='PINHOLE',width=frame['width'],height=frame['height'],params=[k[0,0],k[1,1],k[0,2]+.5,k[1,2]+.5]);cid=db.write_camera(camera);iid=db.write_image(pycolmap.Image(name=name,camera_id=cid));identities[index]=iid
            path=Path(feature_cache)/f'{index}.npz' if method=='superpoint_lightglue' else Path(sift_control)/f'features-{index}.npz'
            with np.load(path) as features:points=features['points']
            db.write_keypoints(iid,(points+.5).astype('float32'))
            (output/f'features-{index}.npz').symlink_to(path.resolve())
            records.append(dict(frameIndex=index,keypointCount=len(points)))
        for a,b in pairs:
            path=Path(feature_cache).parent/'matches'/f'{a}-{b}.npz'
            if method=='superpoint_lightglue':
                with np.load(path) as row:matches=row['matches']
            else:matches=np.load(Path(sift_control)/f'matches-{a}-{b}.npy')
            db.write_matches(identities[a],identities[b],np.asarray(matches,dtype='uint32'))
    finally:db.close()
    inputs=dict(trainingIndices=training,evaluationExcluded=evaluation,source='official_SuperPoint640_1024_LightGlue_research' if method=='superpoint_lightglue' else 'source_SIFT1280_1500_same_pair_control',features=records,pairs=[dict(frameA=a,frameB=b) for a,b in pairs],pixelConvention='native_center_archives_COLMAP_corner_points_and_K_plus_half',posePriorUsed=False,featureIdentity='SuperPoint' if method=='superpoint_lightglue' else 'SIFT',queryFeatureRequirement='Same_extractor_descriptor_dimension_preprocessing_required',license='SuperPoint_restrictive_noncommercial_internal_research_only' if method=='superpoint_lightglue' else 'existing_source_SIFT_license')
    (output/'inputs.json').write_text(json.dumps(inputs,indent=2))
    pairfile=output/'pairs.txt';pairfile.write_text(''.join(f'{a:05}.jpg {b:05}.jpg\n' for a,b in pairs))
    options=pycolmap.TwoViewGeometryOptions();options.ransac.max_error=3.;pycolmap.set_random_seed(42)
    pycolmap.verify_matches(str(output/'features.db'),str(pairfile),options)
    opts=pycolmap.IncrementalPipelineOptions();opts.num_threads=2;opts.mapper.num_threads=2;opts.ba_refine_focal_length=False;opts.ba_refine_principal_point=False;opts.ba_refine_extra_params=False;opts.mapper.abs_pose_refine_focal_length=False;opts.mapper.abs_pose_refine_extra_params=False
    reconstructed=pycolmap.incremental_mapping(str(output/'features.db'),str(images),str(models),opts)
    result=dict(method=method,trainingIndices=training,evaluationExcluded=evaluation,pairCount=len(pairs),features=records,descriptorsWritten=False,matchingPerformedByMapper=False,intrinsicsRefined=False,components=[],productAccepted=False,metricAccuracyVerified=False)
    registered=set()
    for identity,model in reconstructed.items():
        names=[model.images[i].name for i in model.reg_image_ids()];indices=[int(Path(n).stem) for n in names];registered.update(indices)
        cameras=[]
        for image_id in model.reg_image_ids():
            image=model.images[image_id];matrix=np.eye(4);matrix[:3]=image.cam_from_world().matrix();cameras.append(dict(frameIndex=int(Path(image.name).stem),cameraToWorldCV=np.linalg.inv(matrix).tolist()))
        lengths=[point.track.length() for point in model.points3D.values()]
        triangulation=[]
        for point in model.points3D.values():
            centers=np.asarray([model.images[e.image_id].projection_center() for e in point.track.elements])
            rays=point.xyz-centers;rays=rays/np.maximum(np.linalg.norm(rays,axis=1,keepdims=True),1e-12)
            triangulation.append(float(np.degrees(np.arccos(np.clip((rays@rays.T).min(),-1,1)))))
        angle_stats=dict(median=float(np.median(triangulation)) if triangulation else None,p10=float(np.percentile(triangulation,10)) if triangulation else None,p95=float(np.percentile(triangulation,95)) if triangulation else None,definition='maximum_observed_ray_angle_per_reconstructed_track_not_metric_accuracy')
        track_stats=dict(count=len(lengths),median=float(np.median(lengths)) if lengths else None,p95=float(np.percentile(lengths,95)) if lengths else None,histogram={str(v):lengths.count(v) for v in sorted(set(lengths))})
        result['components'].append(dict(registeredNames=names,registered=len(indices),meanReprojectionError=model.compute_mean_reprojection_error(),points=model.num_points3D(),triangulationAngleDegrees=angle_stats,trackLengthDistribution=track_stats,id=identity,registeredTrainingIndices=indices,registeredCount=len(indices),pointCount=model.num_points3D(),meanReprojectionPixels=model.compute_mean_reprojection_error(),cameras=cameras,modelPath=str(models/str(identity))))
    result['mapperInitialization']=dict(minimumTriangulationAngleDegrees=opts.mapper.init_min_tri_angle,minimumInliers=opts.mapper.init_min_num_inliers,policy='unchanged_library_defaults_same_for_both_methods')
    result['unregisteredTrainingIndices']=[i for i in training if i not in registered];result['elapsedSeconds']=time.monotonic()-start
    (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False));return result


def _map_worker(connection,task):
    try:connection.send(reconstruct_pair_map(**task))
    except Exception as error:connection.send(dict(status='failed',reason=type(error).__name__,productAccepted=False))
    finally:connection.close()

def run_map_with_deadline(task,deadline=600):
    context=mp.get_context('spawn');connection,other=context.Pipe();process=context.Process(target=_map_worker,args=(other,task));process.start();other.close()
    try:
        if connection.poll(deadline):
            try:return connection.recv()
            except EOFError:return dict(status='worker_exited',productAccepted=False)
        return dict(status='timeout',timeoutSeconds=deadline,productAccepted=False)
    finally:
        process.join(.5)
        if process.is_alive():process.terminate();process.join(5)
        if process.is_alive():process.kill();process.join(5)
        connection.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data',required=True);parser.add_argument('--resources',required=True);parser.add_argument('--sift-control',required=True);parser.add_argument('--output',required=True);parser.add_argument('--epoch',type=int,default=3);parser.add_argument('--reconstruct',action='store_true');parser.add_argument('--map-timeout',type=float,default=600);parser.add_argument('--pair-offsets',default='1,2');parser.add_argument('--limit-pairs',type=int);parser.add_argument('--features-only',action='store_true');parser.add_argument('--feature-cache');parser.add_argument('--pair-cache');parser.add_argument('--matcher',choices=['lightglue','descriptor_mutual_ratio'],default='lightglue');parser.add_argument('--feature-timeout',type=float,default=45);parser.add_argument('--pair-timeout',type=float,default=45)
    run(parser.parse_args())
