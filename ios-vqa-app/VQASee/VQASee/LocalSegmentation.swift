import CoreGraphics
import CoreML
import CoreVideo
import Foundation
import Vision

/// Optional RGB-only traversability/floor segmentation runner.
///
/// Expected custom model contract for `VQASeeTraversabilitySegmentation`:
/// - Output is either a single-channel pixel buffer or MLMultiArray.
/// - Higher values mean more traversable/floor-like.
/// - The runner computes coarse ROI coverage only; it never declares a route safe.
final class LocalTraversabilitySegmentationRunner {
    private let visionModel: VNCoreMLModel?

    init(bundle: Bundle = .main, modelName: String = "VQASeeTraversabilitySegmentation") {
        var loadedModel: VNCoreMLModel?
        if let compiledURL = bundle.url(forResource: modelName, withExtension: "mlmodelc"),
           let mlModel = try? MLModel(contentsOf: compiledURL),
           let visionModel = try? VNCoreMLModel(for: mlModel) {
            loadedModel = visionModel
        }
        self.visionModel = loadedModel
    }

    var isAvailable: Bool {
        visionModel != nil
    }

    /// Coarse ROI cue only (backward compatible).
    func analyze(
        pixelBuffer: CVPixelBuffer,
        orientation: CGImagePropertyOrientation = .right,
        config: PerceptionConfig = .default
    ) -> LocalSegmentationCueSignal? {
        analyzeDetailed(pixelBuffer: pixelBuffer, orientation: orientation, config: config)?.cue
    }

    /// Run the segmentation model ONCE and derive both the coarse ROI cue and a
    /// traversable guidance line from the same per-pixel output (no double
    /// inference, per the frame budget).
    func analyzeDetailed(
        pixelBuffer: CVPixelBuffer,
        orientation: CGImagePropertyOrientation = .right,
        config: PerceptionConfig = .default
    ) -> LocalSegmentationResult? {
        guard let visionModel else { return nil }
        let request = VNCoreMLRequest(model: visionModel)
        request.imageCropAndScaleOption = .scaleFill
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: orientation, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return nil
        }
        for result in request.results ?? [] {
            if let pixelBufferObservation = result as? VNPixelBufferObservation,
               let sampler = Self.sampler(fromPixelBuffer: pixelBufferObservation.pixelBuffer) {
                return Self.result(width: sampler.width, height: sampler.height, sample: sampler.sample, config: config)
            }
            if let feature = result as? VNCoreMLFeatureValueObservation,
               let array = feature.featureValue.multiArrayValue,
               let sampler = Self.sampler(fromMultiArray: array) {
                return Self.result(width: sampler.width, height: sampler.height, sample: sampler.sample, config: config)
            }
        }
        return nil
    }

    /// Build both cue and guidance line from one traversability sampler.
    private static func result(
        width: Int, height: Int, sample: (Int, Int) -> Double?, config: PerceptionConfig
    ) -> LocalSegmentationResult {
        let cue = cueValue(width: width, height: height, sample: sample, config: config)
        let path = GuidancePathBuilder.centerline(
            width: width,
            height: height,
            sample: sample,
            threshold: config.thresholds.segTraversablePixel,
            source: "ios_segmentation"
        )
        return LocalSegmentationResult(cue: cue, guidancePath: path)
    }

    private static func sampler(
        fromPixelBuffer pixelBuffer: CVPixelBuffer
    ) -> (width: Int, height: Int, sample: (Int, Int) -> Double?)? {
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)
        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let pixelFormat = CVPixelBufferGetPixelFormatType(pixelBuffer)
        guard let base = CVPixelBufferGetBaseAddress(pixelBuffer) else {
            CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly)
            return nil
        }
        // NOTE: caller must keep the buffer alive; we unlock after building the
        // grid values into a copy to avoid dangling access.
        var values = [Double](repeating: .nan, count: width * height)
        for y in 0..<height {
            if pixelFormat == kCVPixelFormatType_OneComponent8 {
                let row = base.advanced(by: y * bytesPerRow).assumingMemoryBound(to: UInt8.self)
                for x in 0..<width { values[y * width + x] = Double(row[x]) / 255.0 }
            } else if pixelFormat == kCVPixelFormatType_OneComponent32Float {
                let row = base.advanced(by: y * bytesPerRow).assumingMemoryBound(to: Float32.self)
                for x in 0..<width {
                    let v = row[x]
                    values[y * width + x] = v.isFinite ? Double(v) : .nan
                }
            }
        }
        CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly)
        let sample: (Int, Int) -> Double? = { x, y in
            guard x >= 0, x < width, y >= 0, y < height else { return nil }
            let v = values[y * width + x]
            return v.isNaN ? nil : v
        }
        return (width, height, sample)
    }

    private static func sampler(
        fromMultiArray array: MLMultiArray
    ) -> (width: Int, height: Int, sample: (Int, Int) -> Double?)? {
        let shape = array.shape.map { $0.intValue }
        guard shape.count >= 2 else { return nil }
        let height = shape[shape.count - 2]
        let width = shape[shape.count - 1]
        guard width > 8, height > 8 else { return nil }
        let strides = array.strides.map { $0.intValue }
        let hStride = strides[strides.count - 2]
        let wStride = strides[strides.count - 1]
        let sample: (Int, Int) -> Double? = { x, y in
            guard x >= 0, x < width, y >= 0, y < height else { return nil }
            let value = array[y * hStride + x * wStride].doubleValue
            return value.isFinite ? value : nil
        }
        return (width, height, sample)
    }

    private static func cueValue(width: Int, height: Int, sample: (Int, Int) -> Double?, config: PerceptionConfig) -> LocalSegmentationCueSignal? {
        let traversablePixel = config.thresholds.segTraversablePixel
        func coverage(in roi: CGRect) -> Double? {
            let xStart = max(0, Int(roi.minX * CGFloat(width)))
            let xEnd = min(width - 1, Int(roi.maxX * CGFloat(width)))
            let yStart = max(0, Int(roi.minY * CGFloat(height)))
            let yEnd = min(height - 1, Int(roi.maxY * CGFloat(height)))
            let stepX = max(1, (xEnd - xStart) / 10)
            let stepY = max(1, (yEnd - yStart) / 8)
            var traversable = 0
            var valid = 0
            for y in stride(from: yStart, through: yEnd, by: stepY) {
                for x in stride(from: xStart, through: xEnd, by: stepX) {
                    guard let value = sample(x, y) else { continue }
                    valid += 1
                    if value >= traversablePixel { traversable += 1 }
                }
            }
            guard valid >= 8 else { return nil }
            return Double(traversable) / Double(valid)
        }
        return LocalSegmentationCueSignal(
            nearPathTraversableRatio: coverage(in: config.nearROI),
            leftFrontTraversableRatio: coverage(in: config.leftROI),
            rightFrontTraversableRatio: coverage(in: config.rightROI)
        )
    }
}
