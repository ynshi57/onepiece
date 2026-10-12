import sys
from pathlib import Path
import torch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'deploy/spatial/research'))
from instance_wall_context import category,point_context,aggregate,restore_shared_parameter_aliases

def evidence(label,contains):return dict(category=label,alternativeMaskContainsPoint=contains)

def test_missing_detection_cannot_certify_wall():
    assert point_context([]).startswith('unknown')
    assert point_context([evidence('easel',[])]).startswith('unknown')

def test_furniture_alternatives_take_precedence_over_wall_prior():
    e=[evidence('wall',[True]*3),evidence('easel',[True]*3)]
    assert point_context(e)=='furniture_conflict_model_evidence'
    e[-1]['alternativeMaskContainsPoint']=[True,False,True]
    assert point_context(e)=='unknown_instance_boundary_disagreement'

def test_architectural_context_requires_distinct_views_and_no_conflict():
    a=dict(frame=1,context='architectural_context_candidate_not_measured')
    assert aggregate([a,a,a])=='unknown'
    assert aggregate([dict(a,frame=i) for i in [1,2,3]])=='architectural_context_candidate_not_measured'
    assert aggregate([a,dict(frame=2,context='furniture_conflict_model_evidence')])=='furniture_conflict_model_evidence'

def test_mixed_prompt_phrase_is_explicitly_unknown():
    assert category('wooden easel')=='easel'
    assert category('wall cardboard box')=='ambiguous_or_unrecognized_phrase'

def test_checkpoint_alias_requires_identical_parameter_object():
    layer=torch.nn.Linear(2,3);m=torch.nn.ModuleList([layer,layer]);state={'0.weight':torch.ones_like(layer.weight),'0.bias':torch.zeros_like(layer.bias)}
    receipt=restore_shared_parameter_aliases(m,state);assert len(receipt)==2;assert torch.all(m[1].weight==1)
    independent=torch.nn.ModuleList([torch.nn.Linear(2,3),torch.nn.Linear(2,3)])
    with pytest.raises(RuntimeError):restore_shared_parameter_aliases(independent,{'0.weight':state['0.weight'],'0.bias':state['0.bias']})

def test_shared_checkpoint_conflict_not_silently_overwritten():
    layer=torch.nn.Linear(2,3);m=torch.nn.ModuleList([layer,layer]);s=m.state_dict();s['1.weight']=s['0.weight']+1
    with pytest.raises(ValueError,match='checkpoint_conflict'):restore_shared_parameter_aliases(m,s)

def test_mixed_detected_instance_blocks_wall_certification():
    e=[evidence('wall',[True]*3),evidence('ambiguous_or_unrecognized_phrase',[False,True,False])]
    assert point_context(e)=='unknown_ambiguous_detected_context'

def test_native_point_rotation_matches_exact_pixel_masks():
    import numpy as np
    from PIL import Image
    from instance_wall_context import native_point_upright
    a=np.zeros((5,7),dtype=np.uint8);a[1,4]=1
    for turns in range(4):
        y,x=np.argwhere(np.asarray(Image.fromarray(a).rotate(90*turns,expand=True))==1)[0]
        assert native_point_upright((4,1),turns,7,5)==(x,y)

def test_single_box_model_mode_agreement_is_context_not_measured_wall():
    from instance_wall_box_control import box_context
    e=dict(category='easel',singleBoxMaskContainsPoint=True,alternativeMaskContainsPoint=[False,True,False],selectedByPredictedIoU=1)
    assert box_context([e])=='furniture_conflict_model_evidence'
    e['singleBoxMaskContainsPoint']=False
    assert box_context([e])=='unknown_instance_boundary_disagreement'
    assert box_context([]).startswith('unknown')
