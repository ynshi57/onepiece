import CoreGraphics
// ARKit ships a module on the macOS SDK too, but ARWorldTrackingConfiguration is
// iOS-only — so `canImport(ARKit)` alone is TRUE on the macOS harness and pulls in
// symbols that don't exist there. Gate on the iOS platform, which is the real
// intent (device depth capability), so the harness compiles the camera-only path.
#if canImport(ARKit) && os(iOS)
import ARKit
#endif
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
        #if canImport(ARKit) && os(iOS)
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
        #else
        // Non-iOS builds (e.g. the macOS offline evaluation harness) have no
        // ARKit and no LiDAR: report unsupported so the camera-only path runs.
        return .unsupported
        #endif
    }
}

struct LocalSegmentationCueSignal: Sendable, Equatable {
    /// Whole-frame traversable sample ratio. Not a three-zone ROI product signal.
    var traversableRatio: Double? = nil

    var hasCoverage: Bool {
        traversableRatio != nil
    }
}

/// Coarse raster of what on-device segmentation considers traversable ("the green
/// walkable region the iPhone perceives"). Row-major, `cells[row * cols + col]`,
/// row 0 = TOP of the image (image space, top-left origin, aligned to the frame
/// image and to the CamVid ground-truth mask). `1` = the device considers the
/// cell traversable (segmentation prob >= config threshold). This is the SAME
/// signal that feeds the coarse ROI cue and the guidance line — surfaced whole so
/// the closed loop can score region IoU instead of only 3-box agreement.
///
/// It is computed only on the offline harness / evaluation path (opt-in), never on
/// the device's real-time frame budget, which deliberately samples only ROIs +
/// ≤16 centerline rows instead of materializing the full grid.
struct TraversableGrid: Sendable, Equatable {
    var cols: Int
    var rows: Int
    var cells: [Int]

    func toWire() -> [String: Any] {
        ["cols": cols, "rows": rows, "cells": cells]
    }
}

/// Combined output of one segmentation inference: the coarse ROI cue, the
/// traversable guidance line, and (harness only) the traversable region grid — all
/// derived from the same per-pixel map.
struct LocalSegmentationResult: Sendable, Equatable {
    var cue: LocalSegmentationCueSignal?
    var guidancePath: GuidancePath?
    var traversableGrid: TraversableGrid? = nil
}

/// Coarse raster of LANE-MARKING pixels the on-device dedicated lane segmenter
/// (`VQASeeLaneSegmentation`, [1,2,H,W] logits, ch1 = lane) perceives. Row-major,
/// `cells[row * cols + col]`, row 0 = TOP of the image (image space, top-left
/// origin), `1` = the model calls the cell a lane marking (2-class softmax prob of
/// the lane class >= threshold). It is a DISTINCT signal from `TraversableGrid`
/// (lane ≠ walkable), and finer-grained because lane markings are thin: a coarse
/// 64×48 grid would erase them.
///
/// Honest scope: this is lane-marking PIXEL segmentation, NOT lane geometry /
/// polylines / ego-lane (that needs CULane-style instance labels + a row-anchor
/// model; deferred). No route is ever declared safe from this.
struct LaneGrid: Sendable, Equatable {
    var cols: Int
    var rows: Int
    var cells: [Int]

    func toWire() -> [String: Any] {
        ["cols": cols, "rows": rows, "cells": cells]
    }
}

struct LocalPathGuidanceSignal: Sendable, Equatable {
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
    static func evaluate(
        perception: LocalPerceptionSignal,
        isTooDark: Bool,
        isLikelyCovered: Bool,
        depthCapability: LocalPathCapability = LocalDepthCapabilityDetector.currentDepthCapability(),
        segmentationCues: LocalSegmentationCueSignal = LocalSegmentationCueSignal(),
        config _: PerceptionConfig = .default
    ) -> LocalPathGuidanceSignal {
        let segmentationCapability: LocalPathCapability = segmentationCues.hasCoverage ? .active : .unsupported
        var reasons: [LocalPathReason] = [
            depthCapability == .hardwareAvailableButInactive ? .depthHardwareAvailableButInactive : .depthUnsupported,
            segmentationCapability == .active ? .segmentationActive : .segmentationUnsupported,
            .yoloOnly,
        ]
        let riskObjects = perception.objects.filter { $0.kind.isPriorityRisk }
        let blocked = riskObjects.compactMap(\.normalizedBoundingBox)
        if isLikelyCovered {
            reasons.append(.likelyCovered)
            return LocalPathGuidanceSignal(
                confidence: 0.2,
                uncertainRegions: blocked.isEmpty ? [CGRect(x: 0.15, y: 0.0, width: 0.70, height: 0.55)] : [],
                reasons: reasons,
                depthCapability: depthCapability,
                segmentationCapability: segmentationCapability,
                segmentationCues: segmentationCues
            )
        }
        if isTooDark {
            reasons.append(.lowLight)
            return LocalPathGuidanceSignal(
                confidence: 0.28,
                uncertainRegions: [CGRect(x: 0.15, y: 0.0, width: 0.70, height: 0.55)],
                reasons: reasons,
                depthCapability: depthCapability,
                segmentationCapability: segmentationCapability,
                segmentationCues: segmentationCues
            )
        }
        if !riskObjects.isEmpty { reasons.append(.objectInNearPath) }
        if perception.depthCues.nearDrop == .possible || perception.depthCues.nearestObstacleDirection != .unknown {
            reasons.append(.depthNearObstacle)
        }
        let confidence = max(riskObjects.map(\.confidence).max() ?? 0.55, blocked.isEmpty ? 0.55 : 0)
        return LocalPathGuidanceSignal(
            confidence: min(confidence, 1.0),
            blockedRegions: blocked,
            reasons: Array(reasons.prefix(8)),
            depthCapability: depthCapability,
            segmentationCapability: segmentationCapability,
            segmentationCues: segmentationCues
        )
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
    /// Predicted traversable guidance line(s) from on-device segmentation. nil
    /// when no segmentation model is available; status=insufficient when free
    /// space is too broken to trace a line (explicit degrade, never fabricated).
    var guidancePath: GuidancePath? = nil
    /// Coarse traversable / drivable region raster. Live TwinLiteNet emits it so
    /// the camera overlay can tint DA; the harness also exports it for IoU.
    var traversableGrid: TraversableGrid? = nil
    /// Lane-marking raster from the dedicated lane segmenter (T4). nil when no lane
    /// model is loaded (default on the live device path until a latency budget is
    /// signed off); populated on the harness / when a lane runner is injected.
    var laneGrid: LaneGrid? = nil
    /// Product lane-line geometry from UFLDv2-style row/column anchors. Empty means
    /// no geometry channel or no lane found; the app/platform must not fabricate
    /// fallback lines from the old pixel grid.
    var lanePolylines: [LanePolyline] = []

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
        guard !blockedRegions.isEmpty || !reasons.isEmpty else {
            return ""
        }
        var parts: [String] = []
        if !blockedRegions.isEmpty {
            parts.append("本地障碍框 \(blockedRegions.count)")
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
                  let visionModel = CoreMLPlatformLoader.visionModel(at: modelURL)
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
