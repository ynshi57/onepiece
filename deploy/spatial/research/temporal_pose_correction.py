"""Train-only temporal correction control; not a connected visual map or SLAM."""
import copy
import numpy as np


def interpolate_corrections(document, refined_document, anchors, training_indices, evaluation_indices, *, allow_component_bridge=False):
    # Caller freezes anchors from training registration, never validation RGB.
    from app.spatial_reconstruction import camera
    from app.spatial_bundle_adjustment import _exp, _log
    train=set(training_indices);evaluation=set(evaluation_indices)
    if train & evaluation or len(train)!=len(training_indices) or len(evaluation)!=len(evaluation_indices):raise ValueError('invalid_frozen_partition')
    if len(anchors)<2 or len({a['frameIndex'] for a in anchors})!=len(anchors):raise ValueError('insufficient_or_duplicate_anchors')
    frozen=[]
    for entry in anchors:
        index=entry['frameIndex']
        if index not in train:raise ValueError('anchor_outside_training')
        raw=camera(document['keyframes'][index]);refined=camera(refined_document['keyframes'][index])
        if raw.epoch!=refined.epoch:raise ValueError('anchor_epoch_mismatch')
        delta=refined.camera_to_world@np.linalg.inv(raw.camera_to_world)
        frozen.append(dict(index=index,time=raw.time,epoch=raw.epoch,component=entry['component'],delta=delta))
    frozen.sort(key=lambda a:a['time']);result=copy.deepcopy(document);records=[]
    for index in sorted(train|evaluation):
        raw=camera(document['keyframes'][index]);same=[a for a in frozen if a['epoch']==raw.epoch]
        if not same:raise ValueError('epoch_without_training_anchor')
        left=[a for a in same if a['time']<=raw.time];right=[a for a in same if a['time']>=raw.time]
        a=left[-1] if left else same[0];b=right[0] if right else same[-1]
        bridge=a['component']!=b['component']
        if bridge and not allow_component_bridge:
            a=b=min((a,b),key=lambda x:abs(x['time']-raw.time));reason='nearest_anchor_component_bridge_not_interpolated'
        else:reason='experimental_component_bridge' if bridge else 'same_component_temporal_interpolation'
        weight=(raw.time-a['time'])/(b['time']-a['time']) if a['time']!=b['time'] else 0.
        relative=b['delta'][:3,:3]@a['delta'][:3,:3].T
        angle=np.arccos(np.clip((np.trace(relative)-1)/2,-1,1))
        if angle>=np.pi-1e-4:raise ValueError('ambiguous_rotation_interpolation')
        correction=np.eye(4);correction[:3,:3]=_exp(weight*_log(relative))@a['delta'][:3,:3]
        correction[:3,3]=(1-weight)*a['delta'][:3,3]+weight*b['delta'][:3,3]
        c2w=correction@raw.camera_to_world;frame=result['keyframes'][index]
        frame['cameraToWorld']=c2w.flatten(order='F').tolist()
        frame['pose']['position']=dict(zip('xyz',c2w[:3,3].tolist()))
        frame['pose']['forward']=dict(zip('xyz',(-c2w[:3,2]).tolist()))
        outside=raw.time<same[0]['time'] or raw.time>same[-1]['time']
        records.append(dict(frameIndex=index,training=index in train,evaluation=index in evaluation,anchorIndices=[a['index'],b['index']],componentIDs=[a['component'],b['component']],reason='nearest_anchor_extrapolation' if outside else reason,weight=weight,secondsFromNearestAnchor=min(abs(raw.time-a['time']),abs(raw.time-b['time'])),independentlyVerified=False))
    result['temporalPoseCorrection']=dict(records=records,trainingIndices=sorted(train),evaluationIndices=sorted(evaluation),evaluationRGBUsed=False,componentsVisuallyConnected=False,allowExperimentalComponentBridge=allow_component_bridge,productAccepted=False)
    return result
