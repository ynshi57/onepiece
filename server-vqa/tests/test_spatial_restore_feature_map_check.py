import sys,json,hashlib
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'deploy/spatial/research'))
from restore_feature_map_check import partitions,uniform_indices,verify_receipt,select_fit_budget,track_groups_grid

def test_saved_epoch_partition_never_trains_on_restoration_queries():
    d={'keyframes':[{'pose':{'epoch':2}} for _ in range(157)]+[{'pose':{'epoch':3}} for _ in range(22)]}
    train,query=partitions(d);assert train==list(range(157));assert query==list(range(157,179))
    d['keyframes'][157]['pose']['epoch']=2
    with pytest.raises(ValueError):partitions(d)

def test_reference_budget_is_uniform_unique_before_query_scoring():
    selected=uniform_indices(list(range(157)),24);assert len(selected)==len(set(selected))==24;assert selected[0]==0 and selected[-1]==156
    assert uniform_indices([1,3,5],24)==[1,3,5]

def test_frozen_original_bytes_reject_modification(tmp_path):
    f=tmp_path/'original';f.write_bytes(b'actual scan bytes');r=tmp_path/'receipt.json';r.write_text(json.dumps({'files':[{'path':'original','size':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()}]}));verify_receipt(tmp_path,r);f.write_bytes(b'changed scan bytes')
    with pytest.raises(ValueError):verify_receipt(tmp_path,r)

def test_fit_budget_preserves_unselected_and_never_uses_score_field():
    matches=[dict(pointId=i,group=i*2,query=i,scoringPassed=999 if i else 0) for i in range(4)];points=np.array([[0,0],[20,0],[200,0],[400,0]],float);support={i:(10-i,1.) for i in range(4)};chosen,rejected=select_fit_budget(matches,points,support,maximum=2,minimum_pixels=128)
    assert [m['query'] for m in chosen]==[0,2];assert len(chosen)+len(rejected)==4;assert {m['unusedReason'] for m in rejected}=={'fixed_fit_budget','fixed_fit_pixel_separation'}

def test_track_group_union_is_transitive_train_pixels_only():
    class Rec:points3D={1:None,2:None,3:None,4:None}
    views=[(1,np.array([[0,0],[7,0],[20,0]],float),[1,2,4]),(2,np.array([[0,0],[7,0]],float),[2,3])];groups=track_groups_grid(Rec(),views)
    assert groups[1]==groups[2]==groups[3];assert groups[4]!=groups[1]
