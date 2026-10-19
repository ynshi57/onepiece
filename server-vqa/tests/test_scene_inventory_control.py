"""VLM protocol tests only; not visual recognition accuracy tests."""
import importlib.util
import json
from pathlib import Path
import pytest

path=Path(__file__).parents[2]/'deploy/spatial/research/scene_inventory_control.py'
spec=importlib.util.spec_from_file_location('scene_inventory_control',path)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def response(objects,reason='stop'):
    return {'choices':[{'finish_reason':reason,'message':{'content':json.dumps({'objects':objects})}}]}


def test_uncertain_inventory_keeps_its_evidence_without_inventing_coordinates():
    item={'name':'未知乐器','evidence':'可见长条木质琴身和琴弦，种类不确定','uncertain':True}
    assert module.parse_inventory(response([item]))==[item]
    assert module.parse_inventory(response([]))==[]  # Empty view does not establish absence.


@pytest.mark.parametrize('reason',['length',None,'content_filter'])
def test_incomplete_response_never_becomes_usable_inventory(reason):
    item={'name':'椅子','evidence':'可见椅背','uncertain':False}
    with pytest.raises(ValueError,match='incomplete'):
        module.parse_inventory(response([item],reason))


@pytest.mark.parametrize('item',[
    {'name':'椅子','evidence':'','uncertain':False},
    {'name':'椅子','evidence':'椅背','uncertain':'false'},
    {'name':'椅子','evidence':'椅背','uncertain':False,'worldPosition':[0,0,0]},
])
def test_schema_rejects_missing_evidence_and_model_invented_positions(item):
    with pytest.raises(ValueError):module.parse_inventory(response([item]))
