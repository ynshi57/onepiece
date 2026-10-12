#include "bridge.h"
#include <opencv2/core.hpp>
#include <opencv2/features2d.hpp>
#include <cstring>
#include <cmath>
#include <exception>
extern "C" int spatial_sift_abi_version() { return 2; }
// Caller owns image and output buffers; returns feature count or -1 on error.
extern "C" int spatial_sift(const unsigned char* image, int width, int height,
                            int stride, int capacity, int nfeatures, int layers, double contrast, double edge,
                            double sigma, int precise_upscale, float* keypoints, float* descriptors,
                            char* error, int error_capacity) {
    try {
        if (!image || !keypoints || !descriptors || width<=0 || height<=0 || stride<width || capacity<=0 || nfeatures<=0 || layers<=0 || !std::isfinite(contrast) || !std::isfinite(edge) || !std::isfinite(sigma) || contrast<=0 || edge<=0 || sigma<=0)
            throw std::runtime_error("invalid image or output capacity");
        cv::Mat gray(height,width,CV_8UC1,const_cast<unsigned char*>(image),stride);
        auto sift=cv::SIFT::create(nfeatures,layers,contrast,edge,sigma,precise_upscale!=0);
        std::vector<cv::KeyPoint> points;cv::Mat values;
        sift->detectAndCompute(gray,cv::noArray(),points,values);
        if (points.size()>static_cast<size_t>(capacity))
            throw std::runtime_error("SIFT response ties exceeded output capacity; increase nfeatures budget");
        int count=static_cast<int>(points.size());
        for(int i=0;i<count;i++) {
            keypoints[6*i]=points[i].pt.x;keypoints[6*i+1]=points[i].pt.y;
            keypoints[6*i+2]=points[i].size;keypoints[6*i+3]=points[i].angle;
            keypoints[6*i+4]=points[i].response;keypoints[6*i+5]=static_cast<float>(points[i].octave);
            std::memcpy(descriptors+128*i,values.ptr<float>(i),128*sizeof(float));
        }
        return count;
    } catch(const std::exception& e) {
        if(error && error_capacity>0) {std::strncpy(error,e.what(),error_capacity-1);error[error_capacity-1]=0;}
        return -1;
    }
}
