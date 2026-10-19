import Foundation
import CoreML
import Vision
import ImageIO

/// Native JPEG coordinates are persisted; presentation rotation never changes camera intrinsics.
nonisolated enum SpatialObjectCoordinates {
    static func turns(_ frame: SpatialKeyframe) -> Int {
        let m = frame.cameraToWorld
        guard m.count == 16 else { return 0 }
        let x = -m[1], y = -m[5]
        guard hypot(x, y) >= 0.1 else { return 0 }
        return (Int((atan2(-x, -y) / (.pi / 2)).rounded()) % 4 + 4) % 4
    }
    static func visibleBox(_ box: [Float]) -> [Float]? {
        guard box.count == 4, box.allSatisfy(\.isFinite), box[0] < box[2], box[1] < box[3] else { return nil }
        let visible = [max(0,box[0]),max(0,box[1]),min(1,box[2]),min(1,box[3])]
        guard visible[0] < visible[2], visible[1] < visible[3] else { return nil }
        return visible
    }
    static func nativeBox(_ box: [Float], turns: Int) -> [Float] {
        let points = [(box[0],box[1]),(box[2],box[1]),(box[0],box[3]),(box[2],box[3])].map { x,y -> (Float,Float) in
            switch turns {
            case 1: return (1-y,x)
            case 2: return (1-x,1-y)
            case 3: return (y,1-x)
            default: return (x,y)
            }
        }
        return [points.map(\.0).min()!,points.map(\.1).min()!,points.map(\.0).max()!,points.map(\.1).max()!]
    }
}

/// Serial actor: no frame queue, no network, no main-thread inference.
actor SpatialObjectAnalyzer {
    private var model: VNCoreMLModel?
    private let modelURL: URL?
    init(modelURL: URL?) { self.modelURL = modelURL }

    func analyze(_ frame: SpatialKeyframe, directory: URL) -> SpatialObjectObservation {
        let began = ProcessInfo.processInfo.systemUptime
        var result = SpatialObjectObservation(attemptID: UUID(), frameID: frame.id, epoch: frame.pose.epoch, frameTime: frame.time,
            status: .failed, elapsedSeconds: 0, detections: [], modelID: "YOLO11nObject/CoreML/scaleFill/bundled-NMS")
        do {
            if model == nil {
                guard let modelURL else { throw Failure.missingModel }
                // iPhone uses the system's default compute configuration (including ANE).
                let loaded = try MLModel(contentsOf: modelURL, configuration: MLModelConfiguration())
                model = try VNCoreMLModel(for: loaded)
            }
            guard let model else { throw Failure.missingModel }
            let imageURL = directory.appendingPathComponent(frame.imageFile)
            guard let source = CGImageSourceCreateWithURL(imageURL as CFURL, nil),
                  let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
                  (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue == frame.width,
                  (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue == frame.height else { throw Failure.invalidImageDimensions }
            let request = VNCoreMLRequest(model: model)
            request.imageCropAndScaleOption = .scaleFill
            let turns = SpatialObjectCoordinates.turns(frame)
            let orientation: CGImagePropertyOrientation = [.up,.left,.down,.right][turns]
            try VNImageRequestHandler(url: directory.appendingPathComponent(frame.imageFile), orientation: orientation).perform([request])
            guard let observations = request.results else { throw Failure.missingResult }
            var detections: [SpatialObjectDetection] = []
            var failures: [String] = []
            for (index, observation) in observations.enumerated() {
                do {
                    guard let object = observation as? VNRecognizedObjectObservation else { throw Failure.invalidObservationType }
                    guard let label = object.labels.first, !label.identifier.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw Failure.missingLabel }
                    let b = object.boundingBox
                    let box = SpatialObjectCoordinates.nativeBox([Float(b.minX),Float(1-b.maxY),Float(b.maxX),Float(1-b.minY)], turns: turns)
                    guard let visible = SpatialObjectCoordinates.visibleBox(box) else { throw Failure.invalidBox(box) }
                    guard label.confidence.isFinite, label.confidence >= 0, label.confidence <= 1 else { throw Failure.invalidScore(label.confidence) }
                    detections.append(SpatialObjectDetection(label: label.identifier, score: label.confidence,
                        boundingBox: visible, rawBoundingBox: box))
                } catch { failures.append("候选\(index + 1)：\(String(describing: error))") }
            }
            result.detections = detections
            if failures.isEmpty { result.status = .processed }
            else {
                result.status = detections.isEmpty ? .failed : .partial
                result.failure = "有\(failures.count)个候选无法处理，原始错误已保留"
                result.detectionFailures = failures
            }
        } catch { result.failure = String(describing: error) }
        result.elapsedSeconds = ProcessInfo.processInfo.systemUptime - began
        return result
    }
    private enum Failure: Error { case missingModel, missingResult, invalidObservationType, missingLabel, invalidImageDimensions
        case invalidBox([Float]), invalidScore(Float) }
}
