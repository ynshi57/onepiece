import importlib.util
from pathlib import Path

path = Path(__file__).resolve().parents[2] / 'deploy/spatial/research/registration_failure_audit.py'
spec = importlib.util.spec_from_file_location('registration_failure_audit', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_repeated_neighbor_observations_do_not_inflate_landmark_support():
    assert module.unique_support({1: {10}, 2: {10}, 3: {11}}) == {3: 11}


def test_conflicting_landmarks_are_not_resolved_arbitrarily():
    assert module.unique_support({1: {10, 11}, 2: {12}}) == {2: 12}


def test_missing_support_remains_missing():
    assert module.unique_support({}) == {}
