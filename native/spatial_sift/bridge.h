#pragma once
#ifdef __cplusplus
extern "C" {
#endif
int spatial_sift_abi_version(void);
/* Caller provides capacity*6 and capacity*128 float buffers.
 * No ownership transfer. Returns count or -1; errors are null terminated. */
int spatial_sift(const unsigned char* image, int width, int height, int stride,
                 int capacity, int nfeatures, int layers, double contrast, double edge,
                 double sigma, int precise_upscale, float* keypoints,
                 float* descriptors, char* error, int error_capacity);
#ifdef __cplusplus
}
#endif
