import Foundation
import Vision
import CoreML
import CoreVideo
import ImageIO

nonisolated struct SpatialLiveObjectCounters: Sendable {
    var received = 0
    var submitted = 0
    var busy = 0
    var failed = 0
    var invalidated = 0
    var trackingUnavailable = 0
}

nonisolated struct SpatialLiveObjectMeasurement: Sendable {
    var scanID: UUID
    var runID: UUID
    var capturedAt: Double
    var completedAt: Double
    var elapsedSeconds: Double
    var invalidated: Bool
    var failure: String?
}

nonisolated struct SpatialLiveObjectResult: Sendable {
    var scanID: UUID
    var epoch: Int
    var capturedAt: Double
    var completedAt: Double
    var elapsedSeconds: Double
    var frameWidth: Int
    var frameHeight: Int
    var detections: [SpatialObjectDetection]
    var failure: String?
    var detectionFailures: [String]
    var nativeToViewport: [Float]?
    var viewportSize: [Float]?
}

/// A single read-only retained AR image; controller admits at most one request.
nonisolated struct SpatialLiveObjectInput: @unchecked Sendable {
    let image: CVPixelBuffer
    let scanID: UUID
    let epoch: Int
    let capturedAt: Double
    let cameraToWorld: [Float]
    let nativeToViewport: [Float]?
    let viewportSize: [Float]?
}

/// No JPEG, network, disk writes, depth or object identity. Model stays resident.
actor SpatialLiveObjectDetector {
    private var model: VNCoreMLModel?
    private let modelURL: URL?
    init(modelURL: URL?) { self.modelURL = modelURL }

    func analyze(_ input: SpatialLiveObjectInput) -> SpatialLiveObjectResult {
        let began = ProcessInfo.processInfo.systemUptime
        let width = CVPixelBufferGetWidth(input.image), height = CVPixelBufferGetHeight(input.image)
        var result = SpatialLiveObjectResult(scanID: input.scanID, epoch: input.epoch,
            capturedAt: input.capturedAt, completedAt: began, elapsedSeconds: 0,
            frameWidth: width, frameHeight: height, detections: [], failure: nil,
            detectionFailures: [], nativeToViewport: input.nativeToViewport, viewportSize: input.viewportSize)
        do {
            try autoreleasepool {
                if model == nil {
                    guard let modelURL else { throw Failure.missingModel }
                    model = try VNCoreMLModel(for: MLModel(contentsOf: modelURL, configuration: MLModelConfiguration()))
                }
                guard let model else { throw Failure.missingModel }
                let frame = SpatialKeyframe(id: "live-only", time: input.capturedAt,
                    pose: SpatialPose(time: input.capturedAt, position: SpatialPoint(x: 0,y: 0,z: 0),
                        forward: SpatialPoint(x: 0,y: 0,z: -1), epoch: input.epoch),
                    intrinsics: [], width: width, height: height, imageFile: "", cameraToWorld: input.cameraToWorld)
                let turns = SpatialObjectCoordinates.turns(frame)
                let orientation: CGImagePropertyOrientation = [.up,.left,.down,.right][turns]
                let request = VNCoreMLRequest(model: model)
                request.imageCropAndScaleOption = .scaleFill
                try VNImageRequestHandler(cvPixelBuffer: input.image, orientation: orientation, options: [:]).perform([request])
                guard let rows = request.results else { throw Failure.missingResult }
                for (index,row) in rows.enumerated() {
                    do {
                        guard let object = row as? VNRecognizedObjectObservation,
                              let label = object.labels.first,
                              !label.identifier.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw Failure.invalidCandidate }
                        let b = object.boundingBox
                        let raw = SpatialObjectCoordinates.nativeBox([Float(b.minX),Float(1-b.maxY),Float(b.maxX),Float(1-b.minY)], turns: turns)
                        guard let visible = SpatialObjectCoordinates.visibleBox(raw) else { throw Failure.invalidBox(raw) }
                        guard label.confidence.isFinite, (0...1).contains(label.confidence) else { throw Failure.invalidScore }
                        result.detections.append(SpatialObjectDetection(label: label.identifier, score: label.confidence,
                            boundingBox: visible, rawBoundingBox: raw))
                    } catch { result.detectionFailures.append("候选\(index+1)：\(error)") }
                }
                if !result.detectionFailures.isEmpty { result.failure = "部分候选识别失败" }
            }
        } catch { result.failure = "本地物品识别失败：\(error)" }
        result.completedAt = ProcessInfo.processInfo.systemUptime
        result.elapsedSeconds = result.completedAt-began
        return result
    }
    private enum Failure: Error { case missingModel, missingResult, invalidCandidate, invalidBox([Float]), invalidScore }
}
