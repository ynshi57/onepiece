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

    func analyze(pixelBuffer: CVPixelBuffer, orientation: CGImagePropertyOrientation = .right) -> LocalSegmentationCueSignal? {
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
               let cue = Self.cue(fromPixelBuffer: pixelBufferObservation.pixelBuffer) {
                return cue
            }
            if let feature = result as? VNCoreMLFeatureValueObservation,
               let array = feature.featureValue.multiArrayValue,
               let cue = Self.cue(fromMultiArray: array) {
                return cue
            }
        }
        return nil
    }

    private static func cue(fromPixelBuffer pixelBuffer: CVPixelBuffer) -> LocalSegmentationCueSignal? {
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
        return cue(width: width, height: height, sample: sample)
    }

    private static func cue(fromMultiArray array: MLMultiArray) -> LocalSegmentationCueSignal? {
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
        return cue(width: width, height: height, sample: sample)
    }

    private static func cue(width: Int, height: Int, sample: (Int, Int) -> Double?) -> LocalSegmentationCueSignal? {
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
                    if value >= 0.55 { traversable += 1 }
                }
            }
            guard valid >= 8 else { return nil }
            return Double(traversable) / Double(valid)
        }
        return LocalSegmentationCueSignal(
            nearPathTraversableRatio: coverage(in: LocalPathGuidanceEngine.nearPathROI),
            leftFrontTraversableRatio: coverage(in: LocalPathGuidanceEngine.leftFrontROI),
            rightFrontTraversableRatio: coverage(in: LocalPathGuidanceEngine.rightFrontROI)
        )
    }
}
