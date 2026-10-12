# Outdoor video replay datasets for VQASee

Reviewed 2026-10-09 using official project documentation. No data downloaded,
download endpoints tested, or platform adapters implemented in this research.

## Candidates and order

1. BDD100K: 100,000 driving clips, around 40 seconds, 720p/30fps. Start with
   city/highway subsets for lane, object and vehicle-drivable-area evaluation.
   Image-task labels are primarily the 10-second keyframe; tracking subsets have
   separate temporal annotations. Do not score every video frame against one mask.
   https://bair.berkeley.edu/blog/2018/05/30/bdd/
   https://github.com/bdd100k/bdd100k
2. comma2k19: over 33 hours on California highway 280, 2,019 one-minute segments;
   HEVC video plus vehicle/sensor logs and camera poses. Useful for temporal
   consistency and replay, not an out-of-box dense lane/obstacle ground-truth set.
   https://github.com/commaai/comma2k19
3. nuScenes: 1,000 twenty-second city scenes, six cameras and timestamped sensor
   data, 3D annotations at 2Hz and trajectory prediction evaluation. Front-camera
   image sequences can be replayed; distinguish camera frames from labelled
   keyframes. Start with mini to validate the adapter, not generalization.
   https://www.nuscenes.org/nuscenes
   https://www.nuscenes.org/tutorials/prediction_tutorial.html
4. OpenLane-V2: camera sequences with 3D lane centerlines/segments, traffic
   elements and topology. Relevant to road connectivity; centerlines are not
   painted lane-marking pixel masks or navigation-route truth.
   https://github.com/OpenDriveLab/OpenLane-V2

## Minimum experiment (L2 proposal, not executed)

Use a small BDD validation selection and highway clips. Preserve original
timestamps, task splits and label availability; display synchronized video and
overlays, model latency, end-to-end result age and frame replacement counts.
Score lane/object/area accuracy only on matching annotated timestamps; show
unlabelled frames as unscored, rather than successful. Keep training/validation
separate because TwinLite is trained on BDD100K.

Products: logged replay evaluates perception, not closed-loop driving safety.
Systems: Mac latency is not iPhone latency; device replay should use the actual
local perception path, while it still cannot reproduce camera-pool pressure.
UI: show original video, current output and failure location on one timeline.
Models: distinguish lane paint, lane centerline, vehicle-drivable area and
pedestrian accessibility. Camera-only input must not silently use LiDAR truth
as model input. Dataset terms must be checked before redistribution/commercial use.

No product-priority, dependency or skill changes are justified by this survey.
