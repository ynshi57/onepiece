"""C ABI for the vendored, unmodified OpenCV SIFT implementation.

Only the bridge is maintained here. No Python cv2 or system OpenCV fallback.
"""
from __future__ import annotations

import ctypes
import os
from functools import lru_cache
from pathlib import Path

import numpy as np


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "native" / "spatial_sift"
LIBRARY = Path(os.getenv("VQASEE_SIFT_BUILD_DIR", str(SOURCE_ROOT / "build"))) / "libspatial_sift.dylib"


@lru_cache(maxsize=1)
def library():
    if not LIBRARY.is_file():
        raise RuntimeError("native_sift_not_built: 运行 bash deploy/spatial/build_sift.sh")
    try:
        lib = ctypes.CDLL(str(LIBRARY))
    except OSError as exc:
        raise RuntimeError("native_sift_load_failed: 请在本机重新编译源码") from exc
    try:
        lib.spatial_sift_abi_version.restype = ctypes.c_int
        lib.spatial_sift_abi_version.argtypes = []
        if lib.spatial_sift_abi_version() != 2:
            raise RuntimeError("native_sift_abi_mismatch: 请重新编译源码并重启后端")
    except AttributeError as exc:
        raise RuntimeError("native_sift_abi_mismatch: 请重新编译源码并重启后端") from exc
    pointer = ctypes.POINTER(ctypes.c_float)
    lib.spatial_sift.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_double, ctypes.c_double,
        ctypes.c_double, ctypes.c_int, pointer, pointer, ctypes.c_char_p, ctypes.c_int]
    lib.spatial_sift.restype = ctypes.c_int
    return lib


def extract(image, maximum=800, layers=3, contrast=.04, edge=10., sigma=1.6,
            precise_upscale=False):
    if image.ndim != 2 or image.dtype != np.uint8 or min(image.shape) < 8:
        raise ValueError("SIFT requires a grayscale uint8 image of at least 8x8")
    if not 1 <= maximum <= 4000 or not 1 <= layers <= 8 or not all(np.isfinite([contrast, edge, sigma])) or min(contrast, edge, sigma) <= 0:
        raise ValueError("Invalid SIFT configuration")
    source = np.ascontiguousarray(image)
    # OpenCV retains response ties, so nfeatures is not a hard output bound.
    capacity = 4000
    keys = np.empty((capacity, 6), dtype=np.float32)
    descriptors = np.empty((capacity, 128), dtype=np.float32)
    error = ctypes.create_string_buffer(2048)
    count = library().spatial_sift(source.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        source.shape[1], source.shape[0], source.strides[0], capacity, maximum, layers,
        contrast, edge, sigma, int(precise_upscale),
        keys.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        descriptors.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), error, len(error))
    if count < 0 or count > capacity:
        raise RuntimeError("native_sift_failed: " + error.value.decode("utf-8", errors="replace"))
    return keys[:count], descriptors[:count]
