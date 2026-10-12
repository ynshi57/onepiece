import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('resect_training_views',Path(__file__).resolve().parents[2]/'deploy/spatial/research/resect_training_views.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_evaluation_targets_and_overlapping_partitions_never_match():
    with pytest.raises(ValueError,match='target_outside_training'):
        module.validate_partitions({'training_indices':[0,1],'evaluation_indices':[2],'target_indices':[2]})
    with pytest.raises(ValueError,match='partition_overlap'):
        module.validate_partitions({'training_indices':[0,1],'evaluation_indices':[1,2],'target_indices':[0]})


def row(inliers=15,passes=12,failures=0,overlap=100):
    return {'solverReturnedPose':True,'fitInlierCount':inliers,'scoring':
            [{'reason':'within_3px'}]*passes+[{'reason':'exceeds_3px'}]*failures+
            [{'reason':'scoring_patch_overlaps_localization_patch'}]*overlap}


def test_solver_return_is_not_independent_success_and_overlap_never_counts():
    assert module.classify_candidate(row(inliers=7))=='weak_localization_inlier_support'
    assert module.classify_candidate(row(passes=3,overlap=100))=='insufficient_independent_scoring_support'
    assert module.classify_candidate(row(passes=12,failures=4))=='independent_scoring_conflict'
    assert module.classify_candidate(row())=='independently_consistent_resection_candidate_not_product_accepted'


def test_whole_track_groups_prevent_nearby_training_patch_split():
    import numpy as np
    class Map:
        points3D={10:None,11:None,12:None}
    # Two nearby orientations/landmarks in one image are one union group.
    views=[(0,np.array([[100.,100.],[103.,100.],[200.,200.]]),None,[10,11,12])]
    groups=module.landmark_groups(Map(),views)
    assert groups[10]==groups[11] and groups[12]!=groups[10]
