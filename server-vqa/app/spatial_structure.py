"""Measured planar support, with unobserved space deliberately left unknown.

A support cell is an indexing neighbourhood, not an inferred piece of wall.
No convex envelope is returned as a surface, no Manhattan prior fills gaps.
"""
from __future__ import annotations
import hashlib
import math
import numpy as np


def _xyz(value):
    return np.asarray([value[a] for a in 'xyz'], dtype=float)


def _dict(value):
    return dict(zip('xyz', map(float, value)))


def _basis(normal):
    normal = normal / np.linalg.norm(normal)
    if normal[np.argmax(np.abs(normal))] < 0:
        normal = -normal
    axis = np.eye(3)[np.argmin(np.abs(normal))]
    u = np.cross(axis, normal)
    u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    return normal, u, v


def recover_structure(points, candidates, *, cell_meters=.10, plane_threshold_meters=.03,
                      minimum_training_cells=12, minimum_validation_cells=6):
    """Refit candidate support planes with deterministic, disjoint spatial-cell holdout.

    Candidate selection used all input points: validation is conditional plane-fit
    stability, not independent discovery, metric ground truth or wall semantics.
    """
    if not .02 <= cell_meters <= .5 or not 0 < plane_threshold_meters <= .03:
        raise ValueError('invalid_structure_limits')
    point_by_id = {p['id']: p for p in points}
    structures, diagnostics = [], []
    for candidate in candidates:
        evidence = [point_by_id[i] for i in candidate['supportPointIDs']
                    if i in point_by_id and point_by_id[i].get('verifiedInThirdView')
                    and point_by_id[i]['epoch'] == candidate['epoch']]
        reason = None
        if not evidence:
            diagnostics.append({'candidateId': candidate['id'], 'reason': 'no_verified_support'})
            continue
        # Split independent 2cm spatial cells rather than duplicated SIFT orientations.
        representatives = {}
        for p in sorted(evidence, key=lambda p: p['id']):
            key = tuple(np.floor(_xyz(p['position'])/.02).astype(int))
            representatives.setdefault(key, p)
        train, validation = [], []
        for key, p in sorted(representatives.items()):
            digest = hashlib.sha256(str(key).encode()).digest()
            (validation if digest[0] % 3 == 0 else train).append(p)
        if len(train) < minimum_training_cells or len(validation) < minimum_validation_cells:
            diagnostics.append({'candidateId': candidate['id'], 'reason': 'insufficient_disjoint_support',
                                'trainingCells': len(train), 'validationCells': len(validation)})
            continue
        training_cloud = np.array([_xyz(p['position']) for p in train])
        center = training_cloud.mean(axis=0)
        _, singular, vh = np.linalg.svd(training_cloud-center, full_matrices=False)
        if singular[1] < 1e-5:
            reason = 'collinear_training_support'
        normal, u, v = _basis(vh[-1])
        offset = -float(normal @ center)
        validation_cloud = np.array([_xyz(p['position']) for p in validation])
        residuals = np.abs(validation_cloud @ normal + offset)
        # Every held-out cell must pass: failures are not removed from the denominator.
        if np.max(residuals) > plane_threshold_meters:
            reason = 'heldout_plane_distance_failed'
        diagnostic = {'candidateId': candidate['id'], 'trainingCells': len(train),
                      'validationCells': len(validation), 'validationFailureCells': int((residuals > plane_threshold_meters).sum()),
                      'maximumValidationMeters': float(residuals.max()),
                      'p95ValidationMeters': float(np.percentile(residuals,95)),
                      'reason': reason or 'accepted_partial_support'}
        diagnostics.append(diagnostic)
        if reason:
            continue
        origin = -offset * normal
        cloud = np.array([_xyz(p['position']) for p in evidence])
        uv = (cloud-origin) @ np.array([u,v]).T
        cells = {}
        for p, coordinate in zip(evidence, uv):
            key = tuple(np.floor(coordinate/cell_meters).astype(int))
            cells.setdefault(key, []).append(p)
        lower, upper = uv.min(axis=0), uv.max(axis=0)
        # ID depends on data identity, not RANSAC/output ordering; append/reconstruction
        # changes may produce a new identity. It is explicitly not cross-session alignment.
        identity = str(candidate['epoch'])+'|'+'|'.join(sorted(representatives[k]['id'] for k in representatives))
        structure_id = 'plane-'+hashlib.sha256(identity.encode()).hexdigest()[:16]
        support_cells, edges = [], []
        for (i,j), support in sorted(cells.items()):
            corners_uv = np.array([[i,j],[i+1,j],[i+1,j+1],[i,j+1]])*cell_meters
            corners = origin + corners_uv[:,0,None]*u + corners_uv[:,1,None]*v
            support_cells.append({'index':[int(i),int(j)], 'supportPointIDs':sorted(p['id'] for p in support),
                                  'vertices':[_dict(c) for c in corners], 'surfaceRecovered':False})
            for side, adjacent in enumerate([(i,j-1),(i+1,j),(i,j+1),(i-1,j)]):
                if adjacent not in cells:
                    edges.append({'from':_dict(corners[side]), 'to':_dict(corners[(side+1)%4]),
                                  'kind':'observed_support_to_unknown', 'physicalBoundaryVerified':False})
        # Edge-connected occupied cells remain separate; do not bridge textureless gaps.
        unseen=set(cells)
        components=[]
        while unseen:
            start=min(unseen); unseen.remove(start); component={start}; queue=[start]
            while queue:
                i,j=queue.pop()
                for neighbour in [(i-1,j),(i+1,j),(i,j-1),(i,j+1)]:
                    if neighbour in unseen:
                        unseen.remove(neighbour); component.add(neighbour); queue.append(neighbour)
            support_ids=sorted({p['id'] for key in component for p in cells[key]})
            local=np.array([uv[n] for n,p in enumerate(evidence) if p['id'] in set(support_ids)])
            components.append({'id':structure_id+'-island-'+hashlib.sha256('|'.join(support_ids).encode()).hexdigest()[:12],
                               'cellIndices':[[int(i),int(j)] for i,j in sorted(component)],
                               'supportPointIDs':support_ids,'observedSpanMeters':np.ptp(local,axis=0).tolist(),
                               'localBoundsMeters':[local.min(axis=0).tolist(),local.max(axis=0).tolist()],
                               'connectionMeaning':'adjacent_support_neighbourhoods_not_surface_continuity'})
        box_cells = int(np.prod(np.floor(upper/cell_meters).astype(int)-np.floor(lower/cell_meters).astype(int)+1))
        normal_up = abs(float(normal[1]))
        orientation = 'horizontal' if normal_up >= math.cos(math.radians(15)) else ('vertical' if normal_up <= math.sin(math.radians(15)) else 'inclined')
        structures.append({'id':structure_id,'epoch':candidate['epoch'], 'sourceCandidateID':candidate['id'],
                           'normal':_dict(normal),'offsetMeters':offset,'origin':_dict(origin),'axisU':_dict(u),'axisV':_dict(v),
                           'orientation':orientation,'semanticLabel':None,'semanticVerified':False,
                           'dimensionMeaning':'observed_support_extent_not_wall_size',
                           'observedSpanMeters':(upper-lower).tolist(),'localBoundsMeters':[lower.tolist(),upper.tolist()],
                           'supportComponents':components,'supportCells':support_cells,'supportEdges':edges,'supportPointIDs':sorted(p['id'] for p in evidence),
                           'unknownCellCountWithinExtent':max(0,box_cells-len(cells)),
                           'completeSurface':False,'physicalBoundaryVerified':False,'metricScaleVerified':False,
                           'cellMeters':cell_meters,'validation':diagnostic,
                           'identityScope':'same_record_same_support_not_cross_session',
                           'validationScope':'disjoint_spatial_cells_conditional_on_all_point_candidate_selection'})
    return {'version':'disjoint-support-plane-v1','status':'partial_support' if structures else 'insufficient_evidence',
            'planes':structures,'diagnostics':diagnostics,'roomTopologyVerified':False,'wallCount':0,
            'limitations':['Planes have unknown semantics; vertical does not establish wall.',
                          'Support cells are neighbourhoods of measured features, not recovered surface patches.',
                          'Holes, doors, windows, glass and full wall boundaries remain unknown.',
                          'Validation is conditional on candidate discovery; no external geometry ground truth.']}
