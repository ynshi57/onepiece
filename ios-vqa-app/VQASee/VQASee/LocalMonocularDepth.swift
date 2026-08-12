import CoreGraphics
import CoreML
import CoreVideo
import Foundation
import Vision

/// Optional RGB-only monocular depth runner. It looks for the downloaded
/// DepthAnythingV2SmallF16 model. If the model is absent or has an unexpected
/// output shape, it fails open and leaves the ARKit/YOLO path intact.
final class LocalMonocularDepthRunner {
    private let visionModel: VNCoreMLModel?

    init(bundle: Bundle = .main, modelName: String = "DepthAnythingV2SmallF16") {
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

    func analyze(pixelBuffer: CVPixelBuffer, orientation: CGImagePropertyOrientation = .right) -> LocalDepthCueSignal? {
        guard let visionModel else {
            return nil
        }
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
               let cue = Self.depthCue(fromPixelBuffer: pixelBufferObservation.pixelBuffer) {
                return cue
            }
            if let feature = result as? VNCoreMLFeatureValueObservation,
               let array = feature.featureValue.multiArrayValue,
               let cue = Self.depthCue(fromMultiArray: array) {
                return cue
            }
        }
        return nil
    }

    private static func depthCue(fromPixelBuffer pixelBuffer: CVPixelBuffer) -> LocalDepthCueSignal? {
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }
        guard CVPixelBufferGetPixelFormatType(pixelBuffer) == kCVPixelFormatType_DepthFloat32,
              let base = CVPixelBufferGetBaseAddress(pixelBuffer)
        else {
            return nil
        }
        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)
        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let sample: (Int, Int) -> Float? = { x, y in
            guard x >= 0, x < width, y >= 0, y < height else { return nil }
            let row = base.advanced(by: y * bytesPerRow).assumingMemoryBound(to: Float32.self)
            let value = row[x]
            return value.isFinite ? value : nil
        }
        return relativeDepthCue(width: width, height: height, sample: sample)
    }

    private static func depthCue(fromMultiArray array: MLMultiArray) -> LocalDepthCueSignal? {
        let shape = array.shape.map { $0.intValue }
        guard shape.count >= 2 else {
            return nil
        }
        let height = shape[shape.count - 2]
        let width = shape[shape.count - 1]
        guard width > 8, height > 8 else {
            return nil
        }
        let strides = array.strides.map { $0.intValue }
        let baseOffset = 0
        let hStride = strides[strides.count - 2]
        let wStride = strides[strides.count - 1]
        let sample: (Int, Int) -> Float? = { x, y in
            guard x >= 0, x < width, y >= 0, y < height else { return nil }
            let index = baseOffset + y * hStride + x * wStride
            let value = array[index].floatValue
            return value.isFinite ? value : nil
        }
        return relativeDepthCue(width: width, height: height, sample: sample)
    }

    /// Produces a conservative cue from relative depth. Depth Anything is not a
    /// safety-certified metric depth sensor; this only looks for a strong local
    /// near-region contrast in the lower half. It never emits a "safe" signal.
    private static func relativeDepthCue(
        width: Int,
        height: Int,
        sample: (Int, Int) -> Float?
    ) -> LocalDepthCueSignal? {
        var allValues: [Float] = []
        var leftValues: [Float] = []
        var centerValues: [Float] = []
        var rightValues: [Float] = []

        let yStart = Int(Double(height) * 0.48)
        let yEnd = Int(Double(height) * 0.92)
        let xStart = Int(Double(width) * 0.08)
        let xEnd = Int(Double(width) * 0.92)
        let stepY = max(1, (yEnd - yStart) / 8)
        let stepX = max(1, (xEnd - xStart) / 12)

        for y in stride(from: yStart, to: yEnd, by: stepY) {
            for x in stride(from: xStart, to: xEnd, by: stepX) {
                guard let value = sample(x, y) else { continue }
                allValues.append(value)
                let ratio = Double(x) / Double(width)
                if ratio < 0.33 {
                    leftValues.append(value)
                } else if ratio > 0.67 {
                    rightValues.append(value)
                } else {
                    centerValues.append(value)
                }
            }
        }

        guard allValues.count >= 24 else {
            return nil
        }
        let sorted = allValues.sorted()
        let low = sorted[Int(Double(sorted.count - 1) * 0.20)]
        let high = sorted[Int(Double(sorted.count - 1) * 0.80)]
        guard high - low > 0.02 else {
            return nil
        }

        // Treat lower relative depth values as nearer. If a future model version
        // is inverted, diagnostics will expose poor cues and we can flip here.
        let threshold = low + (high - low) * 0.28
        func nearCount(_ values: [Float]) -> Int {
            values.filter { $0 <= threshold }.count
        }
        let left = nearCount(leftValues)
        let center = nearCount(centerValues)
        let right = nearCount(rightValues)
        let strongest = max(left, center, right)
        guard strongest >= 3 else {
            return nil
        }
        let direction: LocalVisionDirection
        if center >= left && center >= right {
            direction = .center
        } else if left >= right {
            direction = .left
        } else {
            direction = .right
        }
        return LocalDepthCueSignal(nearDrop: .possible, nearestObstacleDirection: direction)
    }
}
