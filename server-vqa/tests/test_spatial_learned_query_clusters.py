import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'deploy/spatial/research'))
from learned_query_clusters import frozen_neighborhoods,choose_fit_candidate

def test_frozen_training_covisibility_excludes_unconnected_views():
    c=frozen_neighborhoods({1:[10,11],2:[10],3:[20],4:[20,21]},limit=2)
    assert c==[(1,2),(3,4)]

def test_selection_cannot_use_scoring_success():
    candidates=[dict(solverReturnedPose=True,fitInlierCount=12,fitInlierMedianErrorPixels=1.,scoringPassed=100),dict(solverReturnedPose=True,fitInlierCount=20,fitInlierMedianErrorPixels=1.1,scoringPassed=0)]
    assert choose_fit_candidate(candidates)==1

def test_failed_solver_has_no_nearest_camera_fallback():
    assert choose_fit_candidate([dict(solverReturnedPose=False)]) is None
