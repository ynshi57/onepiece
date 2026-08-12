import CoreGraphics
import ARKit
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



enum LocalPathStatus: String, Sendable, Equatable {
    case candidateOpen
    case caution
    case blocked
    case unknown

    var chineseLabel: String {
        switch self {
        case .candidateOpen:
            return "通行候选区"
        case .caution:
            return "需要注意"
        case .blocked:
            return "疑似被占用"
        case .unknown:
            return "信息不足"
        }
    }
}

enum LocalPathReason: String, Sendable, Equatable {
    case objectInNearPath
    case objectInLeftFront
    case objectInRightFront
    case lowLight
    case likelyCovered
    case depthNearObstacle
    case depthUnsupported
    case depthHardwareAvailableButInactive
    case segmentationUnsupported
    case segmentationActive
    case segmentationNearBlocked
    case yoloOnly
}

enum LocalPathCapability: String, Sendable, Equatable {
    case unsupported
    case hardwareAvailableButInactive
    case active
}

enum LocalDepthCapabilityDetector {
    static func currentDepthCapability() -> LocalPathCapability {
        guard ARWorldTrackingConfiguration.isSupported else {
            return .unsupported
        }
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)
            || ARWorldTrackingConfiguration.supportsFrameSemantics(.smoothedSceneDepth) {
            // The current VQASee camera pipeline uses AVCaptureSession, not ARSession,
            // so depth-capable hardware is detected but depth is not yet active.
            return .hardwareAvailableButInactive
        }
        return .unsupported
    }
}

struct LocalSegmentationCueSignal: Sendable, Equatable {
    var nearPathTraversableRatio: Double? = nil
    var leftFrontTraversableRatio: Double? = nil
    var rightFrontTraversableRatio: Double? = nil

    var hasCoverage: Bool {
        nearPathTraversableRatio != nil || leftFrontTraversableRatio != nil || rightFrontTraversableRatio != nil
    }
}

struct LocalPathGuidanceSignal: Sendable, Equatable {
    var nearPathStatus: LocalPathStatus = .unknown
    var leftFrontStatus: LocalPathStatus = .unknown
    var rightFrontStatus: LocalPathStatus = .unknown
    var focusDirection: LocalVisionDirection = .unknown
    var confidence: Double = 0
    /// Normalized Vision-style rects (origin lower-left). They are candidates,
    /// not navigation permissions.
    var guidanceCorridor: CGRect? = nil
    var blockedRegions: [CGRect] = []
    var uncertainRegions: [CGRect] = []
    var reasons: [LocalPathReason] = []
    var depthCapability: LocalPathCapability = .unsupported
    var segmentationCapability: LocalPathCapability = .unsupported
    var segmentationCues = LocalSegmentationCueSignal()

    static let empty = LocalPathGuidanceSignal(
        nearPathStatus: .unknown,
        leftFrontStatus: .unknown,
        rightFrontStatus: .unknown,
        focusDirection: .unknown,
        confidence: 0,
        guidanceCorridor: nil,
        blockedRegions: [],
        uncertainRegions: [],
        reasons: [.depthUnsupported, .segmentationUnsupported],
        depthCapability: .unsupported,
        segmentationCapability: .unsupported,
        segmentationCues: LocalSegmentationCueSignal()
    )
}

enum LocalPathGuidanceEngine {
    static let nearPathROI = CGRect(x: 0.25, y: 0.00, width: 0.50, height: 0.58)
    static let leftFrontROI = CGRect(x: 0.00, y: 0.05, width: 0.42, height: 0.62)
    static let rightFrontROI = CGRect(x: 0.58, y: 0.05, width: 0.42, height: 0.62)

    static func evaluate(
        perception: LocalPerceptionSignal,
        isTooDark: Bool,
        isLikelyCovered: Bool,
        depthCapability: LocalPathCapability = LocalDepthCapabilityDetector.currentDepthCapability(),
        segmentationCues: LocalSegmentationCueSignal = LocalSegmentationCueSignal()
    ) -> LocalPathGuidanceSignal {
        let segmentationCapability: LocalPathCapability = segmentationCues.hasCoverage ? .active : .unsupported
        var reasons: [LocalPathReason] = [
            depthCapability == .hardwareAvailableButInactive ? .depthHardwareAvailableButInactive : .depthUnsupported,
            segmentationCapability == .active ? .segmentationActive : .segmentationUnsupported,
            .yoloOnly,
        ]
        if isLikelyCovered {
            reasons.append(.likelyCovered)
            return LocalPathGuidanceSignal(
                nearPathStatus: .unknown,
                leftFrontStatus: .unknown,
                rightFrontStatus: .unknown,
                focusDirection: .unknown,
                confidence: 0.2,
                guidanceCorridor: nearPathROI,
                blockedRegions: [],
                uncertainRegions: [nearPathROI],
                reasons: reasons,
                depthCapability: depthCapability,
                segmentationCapability: segmentationCapability,
                segmentationCues: segmentationCues
            )
        }
        if isTooDark {
            reasons.append(.lowLight)
            return LocalPathGuidanceSignal(
                nearPathStatus: .unknown,
                leftFrontStatus: .unknown,
                rightFrontStatus: .unknown,
                focusDirection: .unknown,
                confidence: 0.28,
                guidanceCorridor: nearPathROI,
                blockedRegions: [],
                uncertainRegions: [nearPathROI],
                reasons: reasons,
                depthCapability: depthCapability,
                segmentationCapability: segmentationCapability,
                segmentationCues: segmentationCues
            )
        }

        let riskObjects = perception.objects.filter { $0.kind.isPriorityRisk }
        let nearObjects = riskObjects.filter { intersects($0.normalizedBoundingBox, nearPathROI) }
        let leftObjects = riskObjects.filter { intersects($0.normalizedBoundingBox, leftFrontROI) }
        let rightObjects = riskObjects.filter { intersects($0.normalizedBoundingBox, rightFrontROI) }

        if !nearObjects.isEmpty { reasons.append(.objectInNearPath) }
        if !leftObjects.isEmpty { reasons.append(.objectInLeftFront) }
        if !rightObjects.isEmpty { reasons.append(.objectInRightFront) }
        if perception.depthCues.nearDrop == .possible || perception.depthCues.nearestObstacleDirection != .unknown {
            reasons.append(.depthNearObstacle)
        }

        var nearStatus = status(for: nearObjects, blockedThreshold: 0.82)
        var leftStatus = status(for: leftObjects, blockedThreshold: 0.86)
        var rightStatus = status(for: rightObjects, blockedThreshold: 0.86)
        if perception.depthCues.nearDrop == .possible || perception.depthCues.nearestObstacleDirection == .center {
            nearStatus = maxSeverity(nearStatus, .caution)
        }
        if perception.depthCues.nearestObstacleDirection == .left {
            leftStatus = maxSeverity(leftStatus, .caution)
        }
        if perception.depthCues.nearestObstacleDirection == .right {
            rightStatus = maxSeverity(rightStatus, .caution)
        }
        if let ratio = segmentationCues.nearPathTraversableRatio, ratio < 0.35 {
            nearStatus = maxSeverity(nearStatus, .caution)
            reasons.append(.segmentationNearBlocked)
        }
        if let ratio = segmentationCues.leftFrontTraversableRatio, ratio < 0.30 {
            leftStatus = maxSeverity(leftStatus, .caution)
        }
        if let ratio = segmentationCues.rightFrontTraversableRatio, ratio < 0.30 {
            rightStatus = maxSeverity(rightStatus, .caution)
        }
        let focus = focusDirection(near: nearObjects, left: leftObjects, right: rightObjects, depth: perception.depthCues.nearestObstacleDirection)
        let confidence = max(
            riskObjects.map(\.confidence).max() ?? 0.55,
            nearStatus == .candidateOpen ? 0.55 : 0
        )
        let blocked = riskObjects.compactMap(\.normalizedBoundingBox)

        return LocalPathGuidanceSignal(
            nearPathStatus: nearStatus,
            leftFrontStatus: leftStatus,
            rightFrontStatus: rightStatus,
            focusDirection: focus,
            confidence: min(confidence, 1.0),
            guidanceCorridor: nearPathROI,
            blockedRegions: blocked,
            uncertainRegions: [],
            reasons: Array(reasons.prefix(8)),
            depthCapability: depthCapability,
            segmentationCapability: segmentationCapability,
            segmentationCues: segmentationCues
        )
    }

    private static func status(for objects: [LocalPerceptionObject], blockedThreshold: Double) -> LocalPathStatus {
        guard !objects.isEmpty else {
            return .candidateOpen
        }
        if objects.contains(where: { object in
            object.confidence >= blockedThreshold && (object.normalizedBoundingBox?.area ?? 0.04) >= 0.018
        }) {
            return .blocked
        }
        return .caution
    }

    private static func maxSeverity(_ lhs: LocalPathStatus, _ rhs: LocalPathStatus) -> LocalPathStatus {
        func rank(_ status: LocalPathStatus) -> Int {
            switch status {
            case .candidateOpen: return 0
            case .unknown: return 1
            case .caution: return 2
            case .blocked: return 3
            }
        }
        return rank(lhs) >= rank(rhs) ? lhs : rhs
    }

    private static func focusDirection(
        near: [LocalPerceptionObject],
        left: [LocalPerceptionObject],
        right: [LocalPerceptionObject],
        depth: LocalVisionDirection
    ) -> LocalVisionDirection {
        if !near.isEmpty { return .center }
        if depth != .unknown { return depth }
        let leftScore = left.map(\.confidence).max() ?? 0
        let rightScore = right.map(\.confidence).max() ?? 0
        if leftScore == 0 && rightScore == 0 { return .unknown }
        return leftScore >= rightScore ? .left : .right
    }

    private static func intersects(_ rect: CGRect?, _ roi: CGRect) -> Bool {
        guard let rect else { return false }
        return rect.intersection(roi).area > 0.006 || roi.contains(CGPoint(x: rect.midX, y: rect.midY))
    }
}

private extension CGRect {
    var area: Double {
        max(0, Double(width)) * max(0, Double(height))
    }
}


struct LocalPerceptionSignal: Sendable, Equatable {
    var objects: [LocalPerceptionObject] = []
    var roadCues = LocalRoadCueSignal()
    var depthCues = LocalDepthCueSignal()
    var modelStatus: LocalPerceptionModelStatus = .unavailable
    var segmentationCues = LocalSegmentationCueSignal()
    var pathGuidance = LocalPathGuidanceSignal.empty

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
        let pathText = pathGuidance.backendContext
        if !pathText.isEmpty {
            parts.append(pathText)
        }
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

extension LocalPathGuidanceSignal {
    var backendContext: String {
        guard nearPathStatus != .unknown || !reasons.isEmpty else {
            return ""
        }
        var parts = ["本地通行区域：近处\(nearPathStatus.chineseLabel)"]
        if focusDirection != .unknown {
            parts.append("关注\(focusDirection.chineseLabel)")
        }
        switch depthCapability {
        case .unsupported:
            parts.append("深度硬件不支持")
        case .hardwareAvailableButInactive:
            parts.append("深度硬件可用但未启用")
        case .active:
            break
        }
        if segmentationCapability == .unsupported {
            parts.append("地面分割不可用")
        }
        return parts.joined(separator: "，")
    }
}

enum LocalPerceptionPostProcessor {
    static func adjustedDetection(
        kind: LocalPerceptionObjectKind,
        confidence: Double,
        boundingBox: CGRect
    ) -> (kind: LocalPerceptionObjectKind, confidence: Double)? {
        if shouldSuppressBottomEdgePerson(kind: kind, boundingBox: boundingBox) {
            return nil
        }
        if shouldDowngradeVehicleCandidate(kind: kind, confidence: confidence, boundingBox: boundingBox) {
            return (.obstacle, min(confidence, 0.72))
        }
        return (kind, confidence)
    }

    private static func shouldSuppressBottomEdgePerson(
        kind: LocalPerceptionObjectKind,
        boundingBox: CGRect
    ) -> Bool {
        guard kind == .person else {
            return false
        }
        let area = boundingBox.area
        let touchesBottom = boundingBox.minY <= 0.02
        let veryThin = boundingBox.width <= 0.055
        let veryShort = boundingBox.height <= 0.12
        let touchesSide = boundingBox.minX <= 0.01 || boundingBox.maxX >= 0.99
        return (touchesBottom && veryShort) || (touchesSide && veryThin && area < 0.025)
    }

    private static func shouldDowngradeVehicleCandidate(
        kind: LocalPerceptionObjectKind,
        confidence: Double,
        boundingBox: CGRect
    ) -> Bool {
        guard kind == .car || kind == .truck || kind == .bus || kind == .motorcycle || kind == .bicycle else {
            return false
        }
        let area = boundingBox.area
        let aspect = Double(boundingBox.width / max(boundingBox.height, 0.001))
        let smallOrMedium = area < 0.08
        let notWideVehicleShape = aspect < 1.15
        let edgeCandidate = boundingBox.minX <= 0.02 || boundingBox.maxX >= 0.98

        // COCO vehicle classes are noisy indoors. Without scene/depth confirmation,
        // small/vertical/edge vehicle detections are safer as generic object
        // candidates than as user-facing "车辆/摩托车" facts.
        return confidence < 0.97 && (smallOrMedium || notWideVehicleShape || edgeCandidate)
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

        let rawKind = LocalPerceptionObjectKind.from(label: label)
        guard rawKind != .unknown || label.contains("obstacle") else {
            return
        }
        let normalizedKind = rawKind == .unknown ? .obstacle : rawKind
        guard let adjusted = LocalPerceptionPostProcessor.adjustedDetection(
            kind: normalizedKind,
            confidence: confidence,
            boundingBox: boundingBox
        ) else {
            return
        }
        signal.objects.append(
            LocalPerceptionObject(
                kind: adjusted.kind,
                direction: Self.direction(for: boundingBox),
                confidence: adjusted.confidence,
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
