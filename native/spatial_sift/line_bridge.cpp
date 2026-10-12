#include "line_bridge.h"
#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>
#include <cstring>
#include <exception>
#include <stdexcept>
#include <vector>

extern "C" int spatial_lsd_abi_version() { return 1; }
extern "C" int spatial_lsd(const unsigned char* image, int width, int height,
                            int stride, int capacity, float* endpoints,
                            char* error, int error_capacity) {
    try {
        if (!image || !endpoints || width < 8 || height < 8 || stride < width || capacity <= 0)
            throw std::runtime_error("invalid LSD image or output capacity");
        cv::Mat gray(height, width, CV_8UC1, const_cast<unsigned char*>(image), stride);
        std::vector<cv::Vec4f> lines;
        cv::createLineSegmentDetector(cv::LSD_REFINE_STD)->detect(gray, lines);
        if (lines.size() > static_cast<size_t>(capacity))
            throw std::runtime_error("LSD segments exceeded output capacity");
        for (size_t i = 0; i < lines.size(); ++i)
            std::memcpy(endpoints + 4*i, lines[i].val, 4*sizeof(float));
        return static_cast<int>(lines.size());
    } catch (const std::exception& e) {
        if (error && error_capacity > 0) {
            std::strncpy(error, e.what(), error_capacity - 1);
            error[error_capacity - 1] = 0;
        }
        return -1;
    }
}
