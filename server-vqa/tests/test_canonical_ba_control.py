import importlib.util
import sys
from pathlib import Path
import pytest
pytest.importorskip('scipy',reason='--with-spatial-geometry required')
pycolmap=pytest.importorskip('pycolmap',reason='--with-spatial-geometry required')
import numpy as np

research=Path(__file__).resolve().parents[2]/'deploy/spatial/research';sys.path.insert(0,str(research))
spec=importlib.util.spec_from_file_location('canonical_ba_control',research/'canonical_ba_control.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_canonical_roundtrip_and_shared_adjustment_match_native_projection():
    k=np.array([[500,510,320.5,240.5],[520,480,329.5,245.5]],float);reference=np.array([510,495,325,243.])
    shared=reference*np.array([1.02,.99,1,1]);shared[2:]+=[3,-2]
    rays=np.array([[.1,.2,2],[-.3,.1,3.]])
    native=rays[:,:2]/rays[:,2:]*k[:,:2]+k[:,2:]
    canonical=np.array([m.canonical_pixels(q,kk,reference) for q,kk in zip(native,k)])
    np.testing.assert_allclose(np.array([m.native_pixels(q,kk,reference) for q,kk in zip(canonical,k)]),native,atol=1e-12)
    adjusted=m.adjusted_native_k(k,reference,shared)
    projected=rays[:,:2]/rays[:,2:]*adjusted[:,:2]+adjusted[:,2:]
    shared_pixels=rays[:,:2]/rays[:,2:]*shared[:2]+shared[2:]
    np.testing.assert_allclose(projected,np.array([m.native_pixels(q,kk,reference) for q,kk in zip(shared_pixels,k)]),atol=1e-12)


def test_candidate_bounds_reject_unbounded_native_solution():
    reference=np.array([500.,510.,320.,240.])
    assert m.within_candidate_bounds(reference,reference)
    for bad in [reference*[1.1,1,1,1],reference+[0,0,11,0],reference+[0,0,0,np.nan]]:
        assert not m.within_candidate_bounds(reference,bad)


def test_shared_camera_clone_preserves_real_frame_rig_track_mapping():
    original=pycolmap.Reconstruction();point=np.array([.1,.2,3.]);images=[];tracks=[]
    for i in range(1,4):
        k=np.array([500.+i*3,510.-i*2,320.5+i,240.5-i]);original.add_camera(pycolmap.Camera(camera_id=i,model='PINHOLE',width=640,height=480,params=k))
        sensor=pycolmap.sensor_t(type=pycolmap.SensorType.CAMERA,id=i);rig=pycolmap.Rig(rig_id=i);rig.add_ref_sensor(sensor);original.add_rig(rig)
        pose=pycolmap.Rigid3d(pycolmap.Rotation3d(),np.array([-.2*i,0,0]));frame=pycolmap.Frame(frame_id=i,rig_id=i,rig_from_world=pose);frame.add_data_id(pycolmap.data_t(sensor_id=sensor,id=i));original.add_frame(frame)
        xyz=point+pose.translation;xy=xyz[:2]/xyz[2]*k[:2]+k[2:];image=pycolmap.Image(name=f'{i:05d}.jpg',keypoints=np.array([xy]),camera_id=i,image_id=i);image.frame_id=i;original.add_image(image);tracks.append(pycolmap.TrackElement(image_id=i,point2D_idx=0))
    pid=original.add_point3D(point,pycolmap.Track(tracks));images=sorted(original.images.values(),key=lambda i:i.name)
    reference=np.array([506.,506.,322.5,238.5]);clone,mapping=m.clone_canonical(original,images,[pid],[0],reference)
    assert len(clone.cameras)==1 and len(clone.rigs)==1 and len(clone.frames)==3
    for image in images:
        new=clone.images[image.image_id];np.testing.assert_allclose(new.cam_from_world().matrix(),image.cam_from_world().matrix())
        assert new.camera_id==1 and new.points2D[0].point3D_id==mapping[0]
        np.testing.assert_allclose(new.points2D[0].xy,m.canonical_pixels(image.points2D[0].xy,original.cameras[image.camera_id].params,reference))
