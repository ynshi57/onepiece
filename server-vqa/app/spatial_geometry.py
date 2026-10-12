"""Derived plane diagnostics. Candidates are never substituted for observations."""
from __future__ import annotations
from collections import Counter
import numpy as np


def _cross(a, b):
    return float(a[0] * b[1] - a[1] * b[0])


def _inside(p, polygon):
    inside = False
    for a, b in zip(polygon, np.roll(polygon, -1, axis=0)):
        # Boundary contact alone is not positive area overlap.
        if abs(_cross(b-a, p-a)) < 1e-9 and np.dot(p-a, p-b) <= 0:
            return False
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + (p[1]-a[1]) * (b[0]-a[0]) / (b[1]-a[1])
            if p[0] < x:
                inside = not inside
    return inside


def polygons_overlap(a, b):
    """Positive-area simple-polygon overlap, without a bbox approximation."""
    for x, y in zip(a, np.roll(a, -1, axis=0)):
        for u, v in zip(b, np.roll(b, -1, axis=0)):
            c1, c2 = _cross(y-x, u-x), _cross(y-x, v-x)
            c3, c4 = _cross(v-u, x-u), _cross(v-u, y-u)
            if c1*c2 < -1e-16 and c3*c4 < -1e-16:
                return True
    # Edge midpoints also handle coincident boundaries with contained interiors.
    if any(_inside(p, b) for p in a) or any(_inside(p, a) for p in b):
        return True
    if any(_inside((x+y)/2, b) for x,y in zip(a,np.roll(a,-1,axis=0))):
        return True
    if any(_inside((x+y)/2, a) for x,y in zip(b,np.roll(b,-1,axis=0))):
        return True
    # Identical polygons have all tested points on the boundary.
    return len(a) == len(b) and any(np.allclose(a, np.roll(b, i, axis=0), atol=1e-8, rtol=0) or np.allclose(a, np.roll(b[::-1], i, axis=0), atol=1e-8, rtol=0) for i in range(len(b)))


def diagnose(document, maximum_residual=0.03, maximum_angle_degrees=5, maximum_distance=0.05):
    """Return fits and pair decisions; unknown epochs cannot safely merge."""
    fits, internal = [], {}
    for index, surface in enumerate(document.get('surfaces', [])):
        sid = surface.get('id', str(index))
        result = {'id': sid, 'kind': surface.get('kind'), 'epoch': surface.get('epoch'), 'status': 'invalid'}
        try:
            points = np.array([[p['x'],p['y'],p['z']] for p in surface['vertices']], dtype=float)
            if len(points) < 3 or not np.isfinite(points).all():
                raise ValueError()
            center = points.mean(axis=0)
            _, singular, vt = np.linalg.svd(points-center, full_matrices=False)
            if singular[1] < 1e-6:
                raise ValueError()
            normal = vt[2]
            residual = float(np.max(np.abs((points-center) @ normal)))
            result.update(normal=normal.tolist(), center=center.tolist(), maxResidualMeters=residual, status='eligible' if residual <= maximum_residual else 'non_planar')
            internal[index] = (points, center, normal, vt[:2])
        except (KeyError, ValueError, TypeError, np.linalg.LinAlgError):
            pass
        fits.append(result)
    pairs, reasons = [], Counter()
    for i in range(len(fits)):
        for j in range(i+1,len(fits)):
            a,b = fits[i],fits[j]
            reason = None
            if a['status'] != 'eligible' or b['status'] != 'eligible': reason='invalid_or_non_planar'
            elif a['epoch'] is None or b['epoch'] is None: reason='unknown_epoch'
            elif a['epoch'] != b['epoch']: reason='different_epoch'
            elif a['kind'] != b['kind']: reason='different_kind'
            else:
                pa,ca,na,basis=internal[i]; pb,cb,nb,_=internal[j]
                angle=float(np.degrees(np.arccos(np.clip(abs(np.dot(na,nb)),0,1))))
                distance=max(float(np.max(np.abs((pb-ca)@na))),float(np.max(np.abs((pa-cb)@nb))))
                if angle > maximum_angle_degrees: reason='different_normal'
                elif distance > maximum_distance: reason='different_plane'
                elif not polygons_overlap((pa-ca)@basis.T,(pb-ca)@basis.T): reason='no_polygon_overlap'
            if reason:
                reasons[reason]+=1
            else:
                pairs.append({'sourceIDs':[a['id'],b['id']], 'angleDegrees':angle, 'maximumPlaneDistanceMeters':distance, 'status':'candidate_not_fused'})
    return {'status':'diagnostics_only','originalPreserved':True,'fusionPerformed':False,'message':'仅诊断真实多边形重叠候选；未执行多边形并集或重建房间', 'thresholds':{'maxResidualMeters':maximum_residual,'maxAngleDegrees':maximum_angle_degrees,'maxPlaneDistanceMeters':maximum_distance},'planes':fits,'candidates':pairs,'rejectedPairReasons':dict(reasons)}
