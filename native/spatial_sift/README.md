# Source-built spatial SIFT and LSD

OpenCV 4.11.0, commit `31b0eeea0b44b370fd0712312df4214d4ae1b158`.
`SOURCE.json` records official archive and unchanged SIFT source checksums.
Original Apache 2.0 license is in `vendor/opencv/LICENSE`; bundled third-party
licenses remain alongside their code. Modules core, imgproc, features2d and
flann plus OpenCV configuration/platform/third-party support are retained.
No system OpenCV library or Python wheel is used. SIFT source is unchanged.

Build offline using `bash deploy/spatial/build_sift.sh`. Requires CMake and a
C++ compiler. Output on macOS is `native/spatial_sift/build/libspatial_sift.dylib`
(on Linux `libspatial_sift.so`). Build output is excluded from Git.

C ABI `spatial_sift` accepts grayscale uint8 pixels, width/height/row stride,
output capacity, independent nfeatures selection budget, octave layers, contrast threshold, edge threshold,
sigma, precise-upscale bool, caller-allocated keypoint float32 buffer
(capacity * 6), descriptor float32 buffer (capacity * 128), error byte buffer
and error capacity. Returns feature count or -1 with a terminated error string.
Keypoint fields are x, y, size, angle, response and packed octave. Coordinates
are in input image pixels. Defaults: output capacity 4000, nfeatures 800, 3, 0.04, 10, 1.6, false. Caller must
map resized image coordinates and intrinsics explicitly.

The same source-built library also exports `spatial_lsd` (ABI version 1) from
the unchanged OpenCV `imgproc` line segment detector. It accepts grayscale
uint8 pixels, dimensions/stride, a caller-owned float32 endpoint buffer
(`capacity * 4`), and an error buffer. It returns all detected segments or a
visible capacity error; Python then keeps the configured long segments.
`server-vqa/app/spatial_line_structure.py` uses these lines only with explicitly
selected training photos and their K/T to propose local 3D segments. A third
training view is evidence of repeatability, not independent wall distance or
complete room geometry. No Python `cv2` wheel is needed for this path.
