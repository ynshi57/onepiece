import sys
from pathlib import Path
import numpy as np
import torch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'deploy/spatial/research'))
from semantic_model_control import gravity_turns,stream_native

def test_projected_ar_gravity_turns_right_to_down():
    # Construct proper camera basis with world-down projecting on native +x.
    c=np.eye(4);c[:3,:3]=[[0,1,0],[-1,0,0],[0,0,1]]
    turns,visible,down=gravity_turns(c.ravel(order='F'));assert visible and turns==3
    marker=np.zeros((5,7),np.uint8);marker[2,4]=1;marker[2,2]=2
    upright=np.asarray(Image.fromarray(marker).rotate(90*turns,expand=True));a=np.argwhere(upright==1)[0];b=np.argwhere(upright==2)[0];assert a[0]>b[0]

def test_all_quarter_turn_masks_roundtrip_without_interpolation():
    labels=np.arange(35,dtype=np.uint8).reshape(5,7)
    for turns in range(4):
        restored=np.asarray(Image.fromarray(labels).rotate(90*turns,expand=True).rotate(-90*turns,expand=True));assert np.array_equal(restored,labels)

def test_streaming_matches_official_bilinear_before_argmax():
    torch.manual_seed(44);logits=torch.randn(1,11,7,9)
    expanded=torch.nn.functional.interpolate(logits,size=(29,43),mode='bilinear',align_corners=False)
    confidence,labels=expanded.softmax(1).max(1);a,b=stream_native(logits,(29,43),chunk=3)
    assert np.array_equal(a,labels[0].numpy());assert np.allclose(b,confidence[0].numpy(),atol=1e-6)

def test_equal_logits_keep_first_class_like_torch_argmax():
    a,b=stream_native(torch.zeros(1,9,3,4),(7,11),chunk=2)
    assert np.all(a==0);assert np.allclose(b,1/9)

def test_look_up_gravity_is_explicitly_unobservable():
    c=np.eye(4);c[:3,:3]=[[1,0,0],[0,0,1],[0,-1,0]]
    turns,visible,_=gravity_turns(c.ravel(order='F'));assert turns==0 and not visible

def test_float_confidence_exact_quarter_turn_roundtrip():
    a=np.arange(35,dtype=np.float32).reshape(5,7)/43
    for turns in range(4):
        restored=np.asarray(Image.fromarray(a,mode='F').rotate(90*turns,expand=True).rotate(-90*turns,expand=True));assert np.array_equal(a,restored)

def test_nonfinite_logits_cannot_silently_publish_wall_zero():
    import pytest
    with pytest.raises(ValueError,match='finite_cpu'):stream_native(torch.full((1,4,3,4),float('nan')),(7,9))
