"""Training-only gravity constrained line refinement, never complete wall inference."""
from __future__ import annotations
import copy
import math
import numpy as np
from .spatial_reconstruction import camera, resize_intrinsics
from .spatial_line_structure import _plane, _project, LineStructureConfig

def refine_gravity_lines(document: dict, training_result: dict) -> dict:
    if training_result.get('status') != 'training_only_local_line_candidates' or training_result.get('evaluationRGBUsed'):
        raise ValueError('training_only_line_result_required')
    result=copy.deepcopy(training_result)
    config=LineStructureConfig(**result['config'])
    frames={r['frameIndex']:r for r in result['frames']}
    candidates={r['id']:r for r in result['candidates']}
    prepared={}
    for index, record in frames.items():
        if index not in result['trainingFrameIndices']: raise ValueError('unexpected_training_frame')
        cam=camera(document['keyframes'][index]);w=config.image_width;h=round(cam.height*w/cam.width)
        k=resize_intrinsics(cam.k,cam.width,cam.height,w,h)
        prepared[index]=(cam,k,np.asarray(record['longLines'],float))
    diagnostics=[]
    for line in result['localLines']:
        source=candidates[line['candidateId']]; observed=list(zip(source['sourceFrames'],source['sourceLineIds']))
        for check in source['thirdViewChecks']:
            supported=[m for m in check['lineMatches'] if m.get('reason')=='appearance_supported']
            if supported:
                best=min(supported,key=lambda m:(m['appearanceDifference'],-m['overlapPixels'],m['lineId']))
                observed.append((check['frameIndex'],best['lineId']))
        diagnostic={'candidateId':line['candidateId'],'observations':observed,'applied':False}
        original=np.asarray(line['endpointsWorld'],float);delta=original[1]-original[0]
        if len({i for i,j in observed})<3:
            diagnostic['reason']='insufficient_independent_training_views';diagnostics.append(diagnostic);continue
        if abs(delta[1])/max(np.linalg.norm(delta),1e-12)<math.cos(math.radians(5)):
            diagnostic['reason']='not_vertical_candidate';diagnostics.append(diagnostic);continue
        normals=[];offsets=[]
        for index,identity in observed:
            cam,k,segments=prepared[index];normal=_plane(cam,k,segments[identity]);normals.append(normal);offsets.append(normal@cam.center)
        normals=np.asarray(normals)
        if np.max(np.abs(normals[:,1]))>math.sin(math.radians(3)):
            diagnostic['reason']='gravity_inconsistent_training_lines';diagnostics.append(diagnostic);continue
        matrix=normals[:,[0,2]];singular=np.linalg.svd(matrix,compute_uv=False)
        diagnostic['horizontalConstraintSingularValues']=singular.tolist()
        if singular[-1]/max(singular[0],1e-12)<.01:
            diagnostic['reason']='insufficient_horizontal_parallax';diagnostics.append(diagnostic);continue
        ys=original[:,1].copy()
        if abs(ys[1]-ys[0])<.05:
            diagnostic['reason']='insufficient_observed_vertical_span';diagnostics.append(diagnostic);continue
        def residual(xz):
            endpoints=np.array([[xz[0],ys[0],xz[1]],[xz[0],ys[1],xz[1]]]);values=[]
            for index,identity in observed:
                cam,k,segments=prepared[index];uv,z=_project(cam,k,endpoints)
                if np.any(z<=0) or not np.isfinite(uv).all(): raise ValueError('invalid_refined_projection')
                equation=np.cross(np.r_[uv[0],1.],np.r_[uv[1],1.]);length=np.linalg.norm(equation[:2])
                if length<1e-9: raise ValueError('degenerate_refined_projection')
                values.extend((np.c_[segments[identity],np.ones(2)]@equation/length).tolist())
            return np.asarray(values)
        xz=np.linalg.lstsq(matrix,np.asarray(offsets)-normals[:,1]*ys.mean(),rcond=None)[0]
        try:
            baseline=residual(original.mean(0)[[0,2]])
            original_errors=[]
            for index,identity in observed:
                cam,k,segments=prepared[index];uv,z=_project(cam,k,original)
                equation=np.cross(np.r_[uv[0],1.],np.r_[uv[1],1.]);length=np.linalg.norm(equation[:2])
                if np.any(z<=0) or length<1e-9:raise ValueError('invalid_original_line_projection')
                original_errors.extend((np.c_[segments[identity],np.ones(2)]@equation/length).tolist())
            diagnostic['originalFreeDirectionMedianPixels']=float(np.median(np.abs(original_errors)))
            diagnostic['verticalBaselineDescription']='original_midpoint_with_direction_forced_to_gravity_not_original_line'
            for _ in range(15):
                errors=residual(xz);jacobian=np.column_stack([(residual(xz+np.eye(2)[axis]*1e-5)-errors)/1e-5 for axis in (0,1)])
                weight=np.minimum(1.,3./np.maximum(np.abs(errors),1e-12));step=np.linalg.lstsq(jacobian*np.sqrt(weight[:,None]),-errors*np.sqrt(weight),rcond=None)[0]
                if np.linalg.norm(step)<1e-7:break
                objective=lambda e:float(np.sum(np.where(np.abs(e)<=3,.5*e*e,3*(np.abs(e)-1.5))))
                current=objective(errors);accepted=False
                for factor in (1.,.5,.25,.125,.0625):
                    proposal=xz+factor*step
                    try:trial=residual(proposal)
                    except ValueError:continue
                    if objective(trial)<=current:xz=proposal;accepted=True;break
                if not accepted:break
            errors=np.abs(residual(xz));median=float(np.median(errors));p90=float(np.percentile(errors,90))
            diagnostic.update(trainingMedianPixels=median,trainingP90Pixels=p90,verticalBaselineMedianPixels=float(np.median(np.abs(baseline))))
            if median>config.maximum_joint_median_residual_pixels or p90>config.maximum_joint_p90_residual_pixels:
                diagnostic['reason']='training_reprojection_failed'
            else:
                lower,upper=float(min(ys)),float(max(ys))
                for index,identity in observed:
                    cam,k,segments=prepared[index];bounds=[]
                    for pixel in segments[identity]:
                        ray=cam.world_to_camera[:3,:3].T@np.linalg.solve(k,np.r_[pixel,1.])
                        horizontal=ray[[0,2]];denominator=float(horizontal@horizontal)
                        if denominator<1e-10:raise ValueError('unobservable_training_height_interval')
                        distance=float(horizontal@(xz-cam.center[[0,2]])/denominator)
                        if distance<=0:raise ValueError('negative_training_ray_depth')
                        bounds.append(float(cam.center[1]+distance*ray[1]))
                    lower=max(lower,min(bounds));upper=min(upper,max(bounds))
                diagnostic['finiteTrainingHeightInterval']=[lower,upper]
                if upper-lower<config.minimum_source_overlap_meters:raise ValueError('insufficient_common_finite_line_support')
                endpoints=np.array([[xz[0],lower,xz[1]],[xz[0],upper,xz[1]]])
                for index,identity in observed:
                    cam,k,segments=prepared[index];uv,z=_project(cam,k,endpoints)
                    from .spatial_line_structure import _distance_to_segment
                    if np.any(z<config.minimum_depth_meters) or np.any(z>config.maximum_depth_meters) or np.max(_distance_to_segment(uv,segments[identity]))>config.source_projection_tolerance_pixels:
                        raise ValueError('finite_training_segment_coverage_failed')
                line['originalEndpointsWorld']=line['endpointsWorld'];line['endpointsWorld']=endpoints.tolist();line['geometrySource']='training_gravity_joint_pixel_refinement';line['jointMedianResidualPixels']=median;line['jointP90ResidualPixels']=p90
                diagnostic.update(applied=True,reason='training_fit_only_not_independent_validation')
        except (ValueError,np.linalg.LinAlgError) as error:diagnostic['reason']=str(error)
        diagnostics.append(diagnostic)
    result['gravityRefinement']={'diagnostics':diagnostics,'allOriginalCandidatesRetained':True,'evaluationUsedForFit':False,'productAccepted':False}
    return result
