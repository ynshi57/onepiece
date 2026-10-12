# Official Depth Anything V2 Small source

This optional offline experiment uses official source pinned to
`a561b849ebae10a6f5ef49e26c83cbbcd36c71bf` from
[DepthAnything/Depth-Anything-V2](https://github.com/DepthAnything/Depth-Anything-V2).
`source-manifest.json` records each selected source file's Git blob SHA and byte
length. `deploy/spatial/fetch_depth_source.py` verifies both before installing
files under `upstream/`, including the original Apache-2.0 LICENSE.

Small weights are Apache-2.0, pinned to official Hugging Face revision
`03876f8651c73a60fe4c2c48294e09fcb6838fcf`, filename
`depth_anything_v2_vits.pth`, size 99,218,434 bytes, SHA256
`715fade13be8f229f8a70cc02066f656f2423a59effd0579197bbf57860e1378`.
[Official weights](https://huggingface.co/depth-anything/Depth-Anything-V2-Small/tree/03876f8651c73a60fe4c2c48294e09fcb6838fcf).
Base/Large/Giant have different licenses and are not installed by this profile.

Install using `bash setup_mac.sh --skip-core --skip-models --skip-qwen --skip-ios --with-spatial-depth`
after creating the repository virtualenv. `VQASEE_SPATIAL_DEPTH_WEIGHTS` may select
an existing verified local weight file; default weights live in the ignored
`server-vqa/models/depth-anything-v2-small/` directory. The installer rechecks
identity even when reusing a file. `bash deploy/spatial/install_depth_prior.sh --check-only`
checks dependencies and source/weight identities without installation or network.

Output is learned relative inverse depth, not measured metric depth, complete
walls, or verified room dimensions. Training-only sparse anchors may align scale;
independent heldout evidence must remain excluded from alignment and selection.
Cross-view agreement between learned predictions is not geometric ground truth.
CPU runtime, peak memory, visual quality and real scene accuracy require separate
measurement; installation success is not product acceptance.
