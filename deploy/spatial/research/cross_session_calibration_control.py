"""Apply already frozen K parameters to another session, with no target fitting."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from pair_geometry_control import camera,sha,summary
from pair_geometry_hypotheses import modified_k,f_for,errors


def run(document,control,calibration,output):
    d=json.loads(document.read_text());c=json.loads(control.read_text());fit=json.loads(calibration.read_text())
    if sha(document)!=c['inputDocumentSHA256']:raise ValueError('target_document_changed')
    if fit['inputDocumentSHA256']==c['inputDocumentSHA256']:raise ValueError('same_session_not_transfer')
    if not fit['sharedKSolverSuccess'] or fit.get('sharedKAtBounds'):raise ValueError('source_calibration_not_converged_or_at_bounds')
    p=np.array(fit['sharedKParameters'],float)
    if p.shape!=(4,) or not np.isfinite(p).all():raise ValueError('invalid_frozen_K')
    rows=[]
    for record in c['records']:
        item=dict(frameA=record['frameA'],frameB=record['frameB'],status=record['status']);rows.append(item)
        if record['status']!='measured' or record.get('baselineARUnits',0)<1e-6:
            item['status']='unmeasurable_preserved';continue
        ca,cb=[camera(d['keyframes'][record[k]]) for k in ('frameA','frameB')]
        a,b=np.array(record['pointsA']),np.array(record['pointsB']);score=~np.array(record['fittingMask'],bool)
        rel=cb.world_to_camera@np.linalg.inv(ca.world_to_camera)
        for name,ka,kb in [('original',ca.k,cb.k),('transferredK',modified_k(ca.k,p),modified_k(cb.k,p))]:
            try:item[name]=summary(np.abs(errors(f_for(ka,kb,rel[:3,:3],rel[:3,3]),a,b)),score)
            except ValueError as error:item[name]=dict(status='failed',reason=str(error),requestedCount=int(score.sum()))
    result=dict(method='cross_session_frozen_K_control',targetDocumentSHA256=sha(document),targetControlSHA256=sha(control),
        sourceCalibrationSHA256=sha(calibration),sourceDocumentSHA256=fit['inputDocumentSHA256'],
        sharedKParameters=p.tolist(),targetFittingPerformed=False,requestedPairCount=len(rows),records=rows,
        productAccepted=False,warning='Transfer consistency does not identify physical calibration separately from correlated pose/feature error. No original camera changed.')
    output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n');return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ['document','control','calibration','output']:parser.add_argument('--'+field,type=Path,required=True)
    a=parser.parse_args();run(a.document,a.control,a.calibration,a.output)
