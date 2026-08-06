import CoreGraphics
import CoreML
import CoreVideo
import Foundation
import Vision

enum LocalPerceptionObjectKind: String, Sendable, Equatable {
    case person
    case car
    case truck
    case bus
    case motorcycle
    case bicycle
    case dog
    case trafficLight
    case sign
    case obstacle
    case stairs
    case pothole
    case curb
    case crosswalk
    case laneMarking
    case unknown

    var chineseLabel: String {
        switch self {
        case .person:
            return "人"
        case .car:
            return "车辆"
        case .truck:
            return "卡车"
        case .bus:
            return "公交车"
        case .motorcycle:
            return "摩托车"
        case .bicycle:
            return "自行车"
        case .dog:
            return "动物"
        case .trafficLight:
            return "交通灯"
        case .sign:
            return "标志牌"
        case .obstacle:
            return "障碍物"
        case .stairs:
            return "台阶"
        case .pothole:
            return "坑洞"
        case .curb:
            return "路沿"
        case .crosswalk:
            return "人行横道"
        case .laneMarking:
            return "车道线"
        case .unknown:
            return "未知物体"
        }
    }

    var isPriorityRisk: Bool {
        switch self {
        case .person, .car, .truck, .bus, .motorcycle, .bicycle, .dog, .obstacle, .stairs, .pothole, .curb:
            return true
        case .trafficLight, .sign, .crosswalk, .laneMarking, .unknown:
            return false
        }
    }

    static func from(label: String) -> LocalPerceptionObjectKind {
        let text = label.lowercased().replacingOccurrences(of: "_", with: " ")
        if text.contains("person") || text.contains("human") || text.contains("pedestrian") {
            return .person
        }
        if text.contains("truck") {
            return .truck
        }
        if text.contains("bus") {
            return .bus
        }
        if text.contains("motorcycle") || text.contains("motorbike") {
            return .motorcycle
        }
        if text.contains("bicycle") || text.contains("bike") || text.contains("cyclist") {
            return .bicycle
        }
        if text.contains("car") || text.contains("vehicle") || text.contains("taxi") {
            return .car
        }
        if text.contains("dog") || text.contains("animal") || text.contains("pet") {
            return .dog
        }
        if text.contains("traffic light") || text.contains("trafficlight") {
            return .trafficLight
        }
        if text.contains("sign") || text.contains("stop sign") {
            return .sign
        }
        if text.contains("stair") || text.contains("step") || text.contains("stairs") {
            return .stairs
        }
        if text.contains("pothole") || text.contains("hole") || text.contains("pit") {
            return .pothole
        }
        if text.contains("curb") || text.contains("kerb") || text.contains("sidewalk edge") {
            return .curb
        }
        if text.contains("crosswalk") || text.contains("zebra") || text.contains("pedestrian crossing") {
            return .crosswalk
        }
        if text.contains("lane") || text.contains("road marking") || text.contains("line marking") {
            return .laneMarking
        }
        if text.contains("obstacle") || text.contains("barrier") || text.contains("cone") || text.contains("box") {
            return .obstacle
        }
        return .unknown
    }
}

struct LocalPerceptionObject: Sendable, Equatable {
    let kind: LocalPerceptionObjectKind
    let direction: LocalVisionDirection
    let confidence: Double
    /// Vision-style normalized bounding box: x/y/width/height in 0...1,
    /// origin at lower-left. Used only for visual overlay; never as a safety guarantee.
    let normalizedBoundingBox: CGRect?

    init(
        kind: LocalPerceptionObjectKind,
        direction: LocalVisionDirection,
        confidence: Double,
        normalizedBoundingBox: CGRect? = nil
    ) {
        self.kind = kind
        self.direction = direction
        self.confidence = confidence
        self.normalizedBoundingBox = normalizedBoundingBox
    }

    var backendText: String {
        let percent = Int((confidence * 100).rounded())
        return "\(direction.chineseLabel)疑似\(kind.chineseLabel)(\(percent)%)"
    }
}

enum LocalCueState: String, Sendable, Equatable {
    case unknown
    case possible
    case unlikely

    var chineseLabel: String {
        switch self {
        case .unknown:
            return "不确定"
        case .possible:
            return "疑似"
        case .unlikely:
            return "未见明显"
        }
    }
}

struct LocalRoadCueSignal: Sendable, Equatable {
    var crosswalk: LocalCueState = .unknown
    var laneMarking: LocalCueState = .unknown
    var curb: LocalCueState = .unknown

    var backendParts: [String] {
        var parts: [String] = []
        if crosswalk == .possible {
            parts.append("疑似人行横道")
        }
        if laneMarking == .possible {
            parts.append("疑似车道线")
        }
        if curb == .possible {
            parts.append("疑似路沿/边界")
        }
        return parts
    }
}

struct LocalDepthCueSignal: Sendable, Equatable {
    var nearDrop: LocalCueState = .unknown
    var nearestObstacleDirection: LocalVisionDirection = .unknown

    var backendParts: [String] {
        var parts: [String] = []
        if nearDrop == .possible {
            parts.append("近处疑似落差")
        }
        if nearestObstacleDirection != .unknown {
            parts.append("最近障碍方向：\(nearestObstacleDirection.chineseLabel)")
        }
        return parts
    }
}

enum LocalPerceptionModelStatus: String, Sendable, Equatable {
    case unavailable
    case loaded
    case failed
}

struct LocalPerceptionSignal: Sendable, Equatable {
    var objects: [LocalPerceptionObject] = []
    var roadCues = LocalRoadCueSignal()
    var depthCues = LocalDepthCueSignal()
    var modelStatus: LocalPerceptionModelStatus = .unavailable

    static let empty = LocalPerceptionSignal()

    var hasPriorityRiskObject: Bool {
        objects.contains { $0.kind.isPriorityRisk }
    }

    var primaryRiskObject: LocalPerceptionObject? {
        objects
            .filter { $0.kind.isPriorityRisk }
            .sorted { lhs, rhs in
                if lhs.direction == .center && rhs.direction != .center {
                    return true
                }
                if lhs.direction != .center && rhs.direction == .center {
                    return false
                }
                return lhs.confidence > rhs.confidence
            }
            .first
    }

    var hasRoadOrDepthCue: Bool {
        !roadCues.backendParts.isEmpty || !depthCues.backendParts.isEmpty
    }

    var backendContext: String {
        var parts: [String] = []
        if !objects.isEmpty {
            let objectText = objects.prefix(6).map(\.backendText).joined(separator: "、")
            parts.append("本地模型检测：\(objectText)")
        }
        parts.append(contentsOf: roadCues.backendParts)
        parts.append(contentsOf: depthCues.backendParts)
        return parts.joined(separator: "；")
    }

    func merging(visionHuman: (hasHuman: Bool, direction: LocalVisionDirection, boundingBox: CGRect?, confidence: Double)) -> LocalPerceptionSignal {
        guard visionHuman.hasHuman else {
            return self
        }
        if objects.contains(where: { $0.kind == .person }) {
            return self
        }
        var copy = self
        copy.objects.insert(
            LocalPerceptionObject(
                kind: .person,
                direction: visionHuman.direction,
                confidence: visionHuman.confidence,
                normalizedBoundingBox: visionHuman.boundingBox
            ),
            at: 0
        )
        return copy
    }
}

/// Optional Core ML detector runner. It first looks for `YOLO11nObject.mlmodelc`,
/// a YOLO11n detection model exported with Core ML NMS so Vision can return
/// `VNRecognizedObjectObservation` boxes for people / vehicles / bicycles. It
/// then falls back to `YOLO11nSeg.mlmodelc` for future segmentation experiments.
/// If no model is present or the output shape is unsupported, the runner fails
/// open and leaves existing Apple Vision / Qwen paths intact.
final class LocalPerceptionCoreMLRunner {
    private let visionModel: VNCoreMLModel?
    private let minimumConfidence: VNConfidence

    init(
        bundle: Bundle = .main,
        modelNames: [String] = ["YOLO11nObject", "YOLO11nSeg"],
        minimumConfidence: VNConfidence = 0.35
    ) {
        self.minimumConfidence = minimumConfidence
        var loadedModel: VNCoreMLModel?
        for modelName in modelNames {
            guard let modelURL = bundle.url(forResource: modelName, withExtension: "mlmodelc"),
                  let mlModel = try? MLModel(contentsOf: modelURL),
                  let visionModel = try? VNCoreMLModel(for: mlModel)
            else {
                continue
            }
            loadedModel = visionModel
            break
        }
        self.visionModel = loadedModel
    }

    func analyze(pixelBuffer: CVPixelBuffer, orientation: CGImagePropertyOrientation = .right) -> LocalPerceptionSignal {
        guard let visionModel else {
            return .empty
        }

        let request = VNCoreMLRequest(model: visionModel)
        request.imageCropAndScaleOption = .scaleFill
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: orientation, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return LocalPerceptionSignal(modelStatus: .failed)
        }

        var signal = LocalPerceptionSignal(modelStatus: .loaded)
        for observation in request.results ?? [] {
            guard let recognized = observation as? VNRecognizedObjectObservation,
                  let label = recognized.labels.first,
                  label.confidence >= minimumConfidence
            else {
                continue
            }
            apply(label: label.identifier, confidence: Double(label.confidence), boundingBox: recognized.boundingBox, to: &signal)
        }
        signal.objects = Array(signal.objects.prefix(12))
        return signal
    }

    private func apply(
        label rawLabel: String,
        confidence: Double,
        boundingBox: CGRect,
        to signal: inout LocalPerceptionSignal
    ) {
        let label = rawLabel.lowercased().replacingOccurrences(of: "_", with: " ")
        if label.contains("crosswalk") || label.contains("zebra") || label.contains("pedestrian crossing") {
            signal.roadCues.crosswalk = .possible
        }
        if label.contains("lane") || label.contains("road marking") || label.contains("line marking") {
            signal.roadCues.laneMarking = .possible
        }
        if label.contains("curb") || label.contains("kerb") || label.contains("sidewalk edge") {
            signal.roadCues.curb = .possible
        }
        if label.contains("pothole") || label.contains("hole") || label.contains("pit") || label.contains("stair") || label.contains("step") {
            signal.depthCues.nearDrop = .possible
        }

        let kind = LocalPerceptionObjectKind.from(label: label)
        guard kind != .unknown || label.contains("obstacle") else {
            return
        }
        signal.objects.append(
            LocalPerceptionObject(
                kind: kind == .unknown ? .obstacle : kind,
                direction: Self.direction(for: boundingBox),
                confidence: confidence,
                normalizedBoundingBox: boundingBox
            )
        )
    }

    private static func direction(for boundingBox: CGRect) -> LocalVisionDirection {
        let centerX = boundingBox.midX
        if centerX < 0.33 {
            return .left
        }
        if centerX > 0.67 {
            return .right
        }
        return .center
    }
}
