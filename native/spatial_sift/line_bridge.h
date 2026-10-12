#pragma once
#ifdef __cplusplus
extern "C" {
#endif
int spatial_lsd_abi_version(void);
/* Caller owns grayscale image and capacity*4 endpoint floats. Returns count or -1. */
int spatial_lsd(const unsigned char* image, int width, int height, int stride,
                int capacity, float* endpoints, char* error, int error_capacity);
#ifdef __cplusplus
}
#endif
