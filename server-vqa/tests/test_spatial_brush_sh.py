"""Check the official channel-major PLY protocol, not scene quality."""
import numpy as np
import pytest
from app.spatial_brush import export_viewer_model, validate_model


def ply(path, degree=3, partial=False):
    names=['x','y','z','opacity']+[f'rot_{i}' for i in range(4)]+[f'scale_{i}' for i in range(3)]+[f'f_dc_{i}' for i in range(3)]
    names += [f'f_rest_{i}' for i in range(3*((degree+1)**2-1))]
    if partial: names.pop()
    values=np.zeros(len(names),dtype='<f4')
    values[names.index('rot_0')]=1
    for i,name in enumerate(names):
        if name.startswith('f_rest_'): values[i]=int(name[7:])+.125
    header='ply\nformat binary_little_endian 1.0\nelement vertex 1\n'+''.join(f'property float {name}\n' for name in names)+'end_header\n'
    path.write_bytes(header.encode()+values.tobytes())


def test_sh3_channel_major_coefficients_preserved(tmp_path):
    source=tmp_path/'official.ply';ply(source)
    assert validate_model(source,sh_degree=3)==1
    model=export_viewer_model(source,tmp_path/'viewer.json')
    assert model['shDegree']==3
    assert model['shCoordinateConvention']=='Brush_Z_up'
    assert model['shCoefficients'][0][1]==[.125,15.125,30.125]
    assert model['shCoefficients'][0][15]==[14.125,29.125,44.125]
    assert 'view_dependent' in model['colorConvention']


def test_partial_sh_is_rejected_without_silent_dc_fallback(tmp_path):
    source=tmp_path/'partial.ply';ply(source,partial=True)
    with pytest.raises(RuntimeError,match='properties_invalid'):validate_model(source)


def test_requested_degree_must_match_actual_model(tmp_path):
    source=tmp_path/'dc.ply';ply(source,degree=0)
    with pytest.raises(RuntimeError,match='sh_degree_mismatch'):validate_model(source,sh_degree=3)
    model=export_viewer_model(source,tmp_path/'viewer.json')
    assert 'shCoefficients' not in model
    assert model['colorConvention'].startswith('SH0_')
