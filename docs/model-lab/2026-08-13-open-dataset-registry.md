# VQASee Open Dataset Registry for Path Guidance

Date: 2026-08-13

## Purpose

Use public/open research datasets to evaluate VQASee path guidance without waiting for slow iPhone field tests. Large datasets are not committed to the repository. Each dataset is adapted into the common VQASee JSONL manifest:

```json
{
  "frame_id": "indoor-office-0001",
  "image": "images/indoor-office-0001.jpg",
  "split": "indoor",
  "scene_tags": ["office", "floor"],
  "ground_truth": {
    "near_path_status": "candidateOpen|caution|blocked|unknown",
    "left_front_status": "candidateOpen|caution|blocked|unknown",
    "right_front_status": "candidateOpen|caution|blocked|unknown",
    "focus_direction": "left|center|right|unknown"
  },
  "prediction": {
    "near_path_status": "candidateOpen|caution|blocked|unknown",
    "left_front_status": "candidateOpen|caution|blocked|unknown",
    "right_front_status": "candidateOpen|caution|blocked|unknown",
    "focus_direction": "left|center|right|unknown"
  }
}
```

## P0 Indoor datasets

### ScanNet

Use for indoor RGB-D video, camera poses, 3D reconstructions and semantic segmentation. Good for indoor office/room/corridor continuity and depth-aware path guidance evaluation.

Caveat: requires accepting ScanNet terms of use; do not commit raw data.

### Fast-SCNN floor segmentation dataset/model

Use for quick indoor floor segmentation POC. Good for validating floor mask → traversability ratio → path guidance signal.

Caveat: model is indoor-floor focused and should not be treated as outdoor or stair/curb solution.

### ADE20K / MIT Scene Parsing

Use for diverse indoor/outdoor semantic segmentation images and class coverage checks. Good for image-level robustness, not video continuity.

Caveat: class semantics may include floor/road-like labels but does not directly encode VQASee traversability.

## P1 Outdoor pedestrian / road datasets

### Mapillary Vistas

Use for street-level semantic segmentation with diverse weather, viewpoints, regions and road scene classes. Good for outdoor sidewalk/road/curb/traffic objects.

Caveat: dataset access/login/license must be respected.

### BDD100K

Use for driving/road-risk contexts: drivable area segmentation, lane detection, road object detection, video and image tasks.

Caveat: driving perspective differs from handheld walking perspective; useful for road/vehicle/drive-risk evaluation, not direct walking guidance.

### Cityscapes / KITTI road-style datasets

Use for sanity-checking road/drivable segmentation and outdoor geometry. Good for road boundary and vehicle scene validation.

Caveat: camera viewpoint is vehicle-mounted, not iPhone handheld.

## What reports should answer

- Indoor: Are water bottles/chairs/desks incorrectly blocking path guidance?
- Outdoor sidewalk: Does the model confuse road/sidewalk/curb?
- Road/driving: Does VQASee avoid saying “safe to drive” while still highlighting risk areas?
- Video: Does path guidance flicker frame-to-frame?
- Capability gap: Are depth/segmentation missing or active?

## Repository policy

- Do not commit raw dataset images/videos.
- Do not commit large model files unless approved and license-cleared.
- Commit only adapters, manifests, tiny examples, reports, and scripts.
