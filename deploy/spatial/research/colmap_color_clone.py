"""Color-only derived COLMAP clone using lossless native RGB PNG decoding.

Original App package and frozen reference geometry remain untouched. JPEG
bitmap decoder failures are recorded, never disguised as original exports.
"""
from __future__ import annotations
import argparse,json,time
from pathlib import Path
import numpy as np
from PIL import Image
from learned_feature_pairs import digest

def geometry_snapshot(rec):
    return ({int(i):np.asarray(rec.images[i].cam_from_world().matrix()).copy() for i in rec.reg_image_ids()},{int(p):np.asarray(x.xyz).copy() for p,x in rec.points3D.items()})

def assert_geometry_unchanged(rec,before):
    cameras,points=before
    if set(cameras)!=set(rec.reg_image_ids()) or set(points)!=set(rec.points3D):raise ValueError('color_clone_geometry_identity_changed')
    if any(not np.array_equal(cameras[i],rec.images[i].cam_from_world().matrix()) for i in cameras) or any(not np.array_equal(points[i],rec.points3D[i].xyz) for i in points):raise ValueError('color_clone_geometry_values_changed')

def run(args):
    import pycolmap
    scan,source,out=map(Path,(args.scan,args.source,args.output));out.mkdir(parents=True,exist_ok=False);images=out/'derived-native-rgb-png';images.mkdir();inputs=json.loads((source/'inputs.json').read_text());doc=json.loads((scan/'document.json').read_text());rows=[];start=time.monotonic();models=out/'models';models.mkdir();receipts={str(p):digest(p) for p in (source/'models').rglob('*') if p.is_file()}
    for idx in inputs['trainingIndices']:
        f=doc['keyframes'][idx];original=scan/f['imageFile'];decoded=np.asarray(Image.open(original).convert('RGB'));target=images/f'{idx:05}.png';Image.fromarray(decoded).save(target,compress_level=1);bitmap=pycolmap.Bitmap.read(str(target),True)
        if bitmap is None or not np.array_equal(bitmap.to_array(),decoded):raise ValueError('derived_png_not_pixel_equivalent_in_colmap')
        rows.append(dict(frameIndex=idx,sourceSHA256=digest(original),derivedPNG=str(target),derivedSHA256=digest(target),originalJPEGColmapReadable=pycolmap.Bitmap.read(str(original),True) is not None,decodedRGBExactlyEqual=True,originalImageUnchanged=True))
    componentrows=[]
    for path in sorted((source/'models').iterdir()):
        if not path.is_dir():continue
        rec=pycolmap.Reconstruction(str(path));before=geometry_snapshot(rec);zero_before=sum(not np.asarray(p.color).any() for p in rec.points3D.values())
        for iid in rec.reg_image_ids():image=rec.images[iid];idx=int(Path(image.name).stem);image.name=f'{idx:05}.png'
        rec.extract_colors_for_all_images(str(images));assert_geometry_unchanged(rec,before);target=models/path.name;target.mkdir();rec.write(str(target));reloaded=pycolmap.Reconstruction(str(target));assert_geometry_unchanged(reloaded,before)
        componentrows.append(dict(component=path.name,registered=len(before[0]),points=len(before[1]),zeroRGBBefore=zero_before,zeroRGBAfter=sum(not np.asarray(p.color).any() for p in rec.points3D.values()),geometryBitwiseUnchanged=True,colorOnlyDerivedModel=str(target)))
    result=dict(sourceOriginalPackageUnchanged=True,derivedPNGsNotOriginalAppExport=True,trainingIndices=inputs['trainingIndices'],evaluationExcluded=inputs['evaluationExcluded'],imageDecoderReceipts=rows,components=componentrows,originalFrozenModelHashesUnchanged=all(digest(path)==sha for path,sha in receipts.items()),elapsedSeconds=time.monotonic()-start,metricGroundTruth=False,productAccepted=False);(out/'receipt.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='imageDecoderReceipts'}),flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('scan','source','output'):p.add_argument('--'+n,required=True)
    run(p.parse_args())
if __name__=='__main__':main()
