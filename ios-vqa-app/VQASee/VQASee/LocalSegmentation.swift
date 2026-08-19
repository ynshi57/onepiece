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
               let res = Self.resultFromPixelBuffer(pixelBufferObservation.pixelBuffer, config: config) {
                return res
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

    /// Compute cue + guidance line by reading the segmentation buffer DIRECTLY
    /// while it stays locked — no full width×height copy. Both the ROI cue (coarse
    /// grid) and the centerline (≤16 sampled rows) only touch a small fraction of
    /// the pixels, so materializing the whole grid every frame was pure waste on
    /// the device's real-time path.
    private static func resultFromPixelBuffer(
        _ pixelBuffer: CVPixelBuffer, config: PerceptionConfig
    ) -> LocalSegmentationResult? {
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(pixelBuffer) else { return nil }
        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)
        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let pixelFormat = CVPixelBufferGetPixelFormatType(pixelBuffer)
        let sample: (Int, Int) -> Double? = { x, y in
            guard x >= 0, x < width, y >= 0, y < height else { return nil }
            if pixelFormat == kCVPixelFormatType_OneComponent8 {
                let row = base.advanced(by: y * bytesPerRow).assumingMemoryBound(to: UInt8.self)
                return Double(row[x]) / 255.0
            }
            if pixelFormat == kCVPixelFormatType_OneComponent32Float {
                let row = base.advanced(by: y * bytesPerRow).assumingMemoryBound(to: Float32.self)
                let value = row[x]
                return value.isFinite ? Double(value) : nil
            }
            return nil
        }
        return result(width: width, height: height, sample: sample, config: config)
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

        // Segmentation models output per-class logits on a channel dim just before
        // (H, W): shape [..., C, H, W]. A C=2 model is binary (class 1 =
        // traversable); its raw values are logits, NOT probabilities, so they must
        // be turned into a traversable probability in [0,1] before any threshold.
        // Reading channel 0 raw (the old code) fed the WRONG class's logit,
        // thresholded at 0.5 — meaningless — which is why the guidance line wandered.
        let classCount = shape.count >= 3 ? shape[shape.count - 3] : 1
        let cStride = shape.count >= 3 ? strides[strides.count - 3] : 0

        let sample: (Int, Int) -> Double?
        if classCount == 2 {
            sample = { x, y in
                guard x >= 0, x < width, y >= 0, y < height else { return nil }
                let base = y * hStride + x * wStride
                let notTrav = array[base].doubleValue           // channel 0
                let trav = array[base + cStride].doubleValue    // channel 1 (traversable)
                guard notTrav.isFinite, trav.isFinite else { return nil }
                // 2-class softmax prob of the traversable class = sigmoid(l1 - l0).
                return 1.0 / (1.0 + exp(notTrav - trav))
            }
        } else if classCount == 1 {
            // Single-channel model already emits a traversable score/probability.
            sample = { x, y in
                guard x >= 0, x < width, y >= 0, y < height else { return nil }
                let value = array[y * hStride + x * wStride].doubleValue
                return value.isFinite ? value : nil
            }
        } else {
            // >2 classes would need a known traversable class index; we don't have
            // one here. Fail loudly (nil) rather than silently thresholding a
            // wrong/raw logit and fabricating a route.
            return nil
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
