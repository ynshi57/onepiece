"""Partition, pixel-convention and process-limit tests; no learned-model accuracy claims."""
import importlib.util
import multiprocessing as mp
from pathlib import Path
import sys
import time
import numpy as np
import pytest
path=Path(__file__).resolve().parents[2]/'deploy/spatial/research/learned_feature_pairs.py'
spec=importlib.util.spec_from_file_location('learned_feature_pairs',path);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)

def test_pairs_never_contain_heldout_and_keep_component_break():
    pairs=m.plan_pairs([1,2,4,5],[3],limit=3,failed=[4],components={1:0,2:0,4:1,5:1})
    assert (2,4) in pairs
    assert all(3 not in pair for pair in pairs)
    assert len(pairs)==3

def test_training_overlap_rejected():
    with pytest.raises(ValueError,match='partition'):m.plan_pairs([1,2],[2])

def test_full_neighbors_are_complete_and_unique():
    pairs=m.plan_pairs(list(range(75)),[80])
    assert len(pairs)==147 and len(set(pairs))==147
    assert (73,74) in pairs and (72,74) in pairs

def test_native_half_pixel_mapping():
    np.testing.assert_allclose(m.native_points([[0,0],[10,20]],[.5,.25]),[[.5,1.5],[20.5,81.5]])

def test_missing_match_geometry_is_preserved():
    result=m.geometric_check({}, {},np.empty((0,2)),np.empty((0,2)),np.empty((0,2)))
    assert result['status']=='no_matches' and result['inlierCount']==0

def test_analytic_calibrated_geometry():
    k=np.array([[400,0,319.5],[0,400,239.5],[0,0,1.]])
    frame={'intrinsics':k.reshape(-1,order='F').tolist(),'width':640,'height':480}
    xyz=np.random.default_rng(3).uniform([-1,-.7,3],[1,.7,6],(80,3));second=xyz-[.3,0,0]
    def project(points):q=points@k.T;return q[:,:2]/q[:,2,None]
    matches=np.c_[np.arange(80),np.arange(80)]
    r=m.geometric_check(frame,frame,project(xyz),project(second),matches)
    assert r['inlierCount']>=75
    assert len(r['inlierMatches'])==r['inlierCount']

def test_unverified_source_cannot_execute(tmp_path):
    (tmp_path/'source-verification.json').write_text('{"revision":"wrong","files":[]}')
    with pytest.raises(ValueError,match='pinned_source'):m.verify_resources(tmp_path)

def _blocked_worker(connection):
    connection.recv();time.sleep(30)

def test_real_worker_deadline_terminates_process():
    class Blocked(m.TimedWorker):
        def start(self):
            context=mp.get_context('fork');self.connection,other=context.Pipe()
            self.process=context.Process(target=_blocked_worker,args=(other,));self.process.start();other.close()
    worker=Blocked('unused','match');start=time.monotonic()
    r=worker.call({'job':1},.1)
    assert r['status']=='timeout' and worker.process is None
    assert time.monotonic()-start<3

def test_descriptor_control_is_normalized_and_mutual():
    a=np.array([[1.,0,0],[0,1,0],[0,0,1]])
    b=a[[2,0,1]]*np.array([2,3,4])[:,None]
    np.testing.assert_array_equal(m.descriptor_mutual_ratio(a,b),[[0,1],[1,2],[2,0]])

def test_descriptor_duplicate_ambiguity_rejected():
    a=np.array([[1.,0],[0,1]])
    b=np.array([[1.,0],[1,0],[0,1]])
    assert [0,0] not in m.descriptor_mutual_ratio(a,b).tolist()

def test_map_cannot_import_evaluation_pair(tmp_path):
    data=tmp_path/'data';data.mkdir();(data/'document.json').write_text('{"keyframes":[]}')
    output=tmp_path/'map'
    with pytest.raises(ValueError,match='partition_violation'):
        m.reconstruct_pair_map(data,output,[1,2],[3],[(1,3)],tmp_path,tmp_path,'superpoint_lightglue')
    assert not (output/'features.db').exists()

def test_extended_training_budget_complete_419():
    pairs=m.plan_pairs(list(range(75)),[80],offsets=(1,2,3,4,7,14))
    assert len(pairs)==419 and len(set(pairs))==419
    assert (60,74) in pairs and all(80 not in pair for pair in pairs)
    with pytest.raises(ValueError,match='positive_training_sequence'):m.plan_pairs([1,2],[3],offsets=(-1,))


def test_mapper_image_preserves_decoded_native_pixels(tmp_path):
    import numpy as np
    import pytest
    pycolmap = pytest.importorskip('pycolmap', reason='optional spatial geometry profile')
    from PIL import Image
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / 'deploy/spatial/research/learned_feature_pairs.py'
    spec = importlib.util.spec_from_file_location('mapper_decode_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / 'source.jpg'
    pixels = np.arange(24*32*3, dtype=np.uint8).reshape(24,32,3)
    Image.fromarray(pixels).save(source)
    original_bytes = source.read_bytes()
    directory = tmp_path / 'images'
    directory.mkdir()
    name = module.prepare_mapper_image(source, directory, 7)
    decoded = pycolmap.Bitmap.read(str(directory/name), True)
    expected = np.asarray(Image.open(source).convert('RGB'))
    assert decoded is not None
    assert np.array_equal(decoded.to_array(), expected)
    assert source.read_bytes() == original_bytes
