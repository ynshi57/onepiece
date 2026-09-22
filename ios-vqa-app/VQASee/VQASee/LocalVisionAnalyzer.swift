import AVFoundation
import CoreVideo
import Foundation
import Vision

/// Direction buckets for fast local observations. These are intentionally coarse:
/// they are trigger hints for backend/Qwen, not user-facing navigation guarantees.
enum LocalVisionDirection: String, Sendable, Equatable {
    case left
    case center
    case right
    case unknown

    var chineseLabel: String {
        switch self {
        case .left:
            return "左侧"
        case .center:
            return "正前方"
        case .right:
            return "右侧"
        case .unknown:
            return "方向不明"
        }
    }
}

/// Small, local-only signal extracted from the camera frame before Qwen sees it.
/// It must stay conservative: it can trigger more backend checks, but it must not
/// claim a path is safe or replace model/human judgement.
struct LocalVisionSignal: Sendable, Equatable {
    let hasHuman: Bool
    let humanDirection: LocalVisionDirection
    let brightness: Double
    let sceneChangeScore: Double
    let isTooDark: Bool
    let isLikelyCovered: Bool
    let analyzerFailed: Bool
    let perception: LocalPerceptionSignal

    init(
        hasHuman: Bool,
        humanDirection: LocalVisionDirection,
        brightness: Double,
        sceneChangeScore: Double,
        isTooDark: Bool,
        isLikelyCovered: Bool,
        analyzerFailed: Bool,
        perception: LocalPerceptionSignal = .empty
    ) {
        self.hasHuman = hasHuman
        self.humanDirection = humanDirection
        self.brightness = brightness
        self.sceneChangeScore = sceneChangeScore
        self.isTooDark = isTooDark
        self.isLikelyCovered = isLikelyCovered
        self.analyzerFailed = analyzerFailed
        self.perception = perception
    }

    var backendContext: String {
        var parts: [String] = []
        if analyzerFailed {
            parts.append("本地快速感知失败")
        }
        if hasHuman {
            parts.append("疑似有人在\(humanDirection.chineseLabel)")
        }
        if isLikelyCovered {
            parts.append("镜头可能被遮挡或画面几乎无信息")
        } else if isTooDark {
            parts.append("画面偏暗")
        }
        if sceneChangeScore >= WalkingFrameSendPolicy.sceneChangeThreshold {
            parts.append("画面变化明显")
        }
        let perceptionText = perception.backendContext
        if !perceptionText.isEmpty {
            parts.append(perceptionText)
        }
        if parts.isEmpty {
            parts.append("画面稳定，未发现本地快速风险信号")
        }
        return parts.joined(separator: "；")
    }
}

/// Pure walking-mode decision policy. Vision/Core ML signals are only a trigger
/// layer; fail open and send a heartbeat so safety-relevant changes are not hidden.
enum WalkingFrameSendDecision: Equatable {
    case send(String)
    case skip(String)
}

enum WalkingFrameSendPolicy {
    static let sceneChangeThreshold = 0.22
    static let significantSceneChangeThreshold = 0.36
    static let minimumQwenIntervalMs: Double = 8_000
    static let heartbeatMs: Double = 12_000

    static func decide(
        mode: AssistanceMode,
        signal: LocalVisionSignal?,
        hasQuestion: Bool,
        pendingSingleShot: Bool,
        millisecondsSinceLastBackendFrame: Double?
    ) -> WalkingFrameSendDecision {
        guard mode == .walking else {
            return .send("非行走模式保持原发送策略")
        }
        if hasQuestion {
            return .send("用户主动提问")
        }
        if pendingSingleShot {
            return .send("单次识别请求")
        }
        guard let signal else {
            return .send("无本地视觉信号，安全起见发送")
        }
        if signal.analyzerFailed {
            return .send("本地快速感知失败，安全起见发送")
        }
        guard let elapsed = millisecondsSinceLastBackendFrame else {
            return .send("行走模式首帧")
        }
        if signal.perception.hasPriorityRiskObject {
            return .send("本地感知检测到风险物体")
        }
        if signal.hasHuman {
            return .send("本地检测到疑似人形")
        }
        if signal.isLikelyCovered || signal.isTooDark {
            return elapsed >= minimumQwenIntervalMs ? .send("本地检测到画面质量风险") : .skip("画面质量风险已本地提示，等待低频复核")
        }
        if signal.sceneChangeScore >= significantSceneChangeThreshold && elapsed >= minimumQwenIntervalMs {
            return .send("画面变化显著，低频复核")
        }
        if signal.sceneChangeScore >= sceneChangeThreshold {
            return elapsed >= minimumQwenIntervalMs ? .send("画面变化明显，低频复核") : .skip("画面变化已记录，等待低频复核")
        }
        if elapsed >= heartbeatMs {
            return .send("行走模式安全心跳")
        }
        return .skip("画面稳定，等待变化或心跳")
    }
}

/// Per-frame wall-clock latency of each on-device perception stage (milliseconds).
/// nil for a stage that did not run this frame (e.g. depth only runs when native
/// depth is unavailable; lane only runs when a lane runner is injected). This is a
/// harness/telemetry read-out — it is NOT wired into any product decision — so a
/// consolidated on-device perception budget (p50/p95) can be reported honestly
/// instead of only per-model numbers.
struct PerceptionFrameTimings: Sendable, Equatable {
    var yoloMs: Double?
    var segmentationMs: Double?
    var depthMs: Double?
    var laneMs: Double?
    var totalMs: Double?
}

/// Apple Vision based local fast analyzer. It currently uses built-in Vision
/// human rectangles plus a tiny luminance fingerprint. No custom Core ML model is
/// bundled yet.
final class LocalVisionAnalyzer {
    /// Latency of the LAST `analyze` call, per stage. Read by the offline harness
    /// to print a consolidated perception latency budget. Updated every frame.
    private(set) var lastTimings = PerceptionFrameTimings()
    private var previousFingerprint: [Double]?
    private let perceptionRunner: LocalPerceptionCoreMLRunner
    private let monocularDepthRunner: LocalMonocularDepthRunner
    private let roadBackend: RoadSurfaceBackend?
    /// Dedicated CamVid lane-pixel model. Skipped when TwinLiteNet already
    /// provides a lane map (one adaptor, one lane source).
    let laneRunner: LocalLaneSegmentationRunner?
    /// Product lane geometry runner (UFLDv2). Kept injected/staged because current
    /// UFLDv2 weights are large; harness can evaluate them before live rollout.
    let lanePolylineRunner: LocalLanePolylineRunner?
    private var config: PerceptionConfig
    /// Off on the live device path (real-time frame budget); the offline evaluation
    /// harness turns it on to export the full traversable region raster.
    private let emitTraversableGrid: Bool

    /// Default init keeps the shipping behavior: all runners load from the main
    /// app bundle and the built-in default perception config is used.
    init(config: PerceptionConfig = .default) {
        self.perceptionRunner = LocalPerceptionCoreMLRunner()
        self.monocularDepthRunner = LocalMonocularDepthRunner()
        self.roadBackend = RoadSurfaceBackends.make(config: config)
        let skipExtraLane = config.roadBackend == .twinlite
        self.laneRunner = (!skipExtraLane && config.useLaneSegmentation) ? LocalLaneSegmentationRunner() : nil
        self.lanePolylineRunner = nil
        self.config = config
        self.emitTraversableGrid = false
    }

    /// Inject a model bundle so the offline macOS evaluation harness can load the
    /// exact same Core ML models the app ships, without duplicating perception
    /// logic. iOS callers keep using the default init unchanged. Pass
    /// `emitTraversableGrid: true` on the harness to export the walkable-region
    /// raster for closed-loop region scoring (kept off on-device for perf).
    init(
        modelBundle: Bundle,
        config: PerceptionConfig = .default,
        emitTraversableGrid: Bool = false,
        laneRunner: LocalLaneSegmentationRunner? = nil,
        lanePolylineRunner: LocalLanePolylineRunner? = nil,
        segmentationRunner: LocalTraversabilitySegmentationRunner? = nil
    ) {
        self.perceptionRunner = LocalPerceptionCoreMLRunner(bundle: modelBundle)
        self.monocularDepthRunner = LocalMonocularDepthRunner(bundle: modelBundle)
        self.roadBackend = RoadSurfaceBackends.make(
            config: config,
            bundle: modelBundle,
            mc5Runner: segmentationRunner
        )
        self.laneRunner = laneRunner
        self.lanePolylineRunner = lanePolylineRunner
        self.config = config
        self.emitTraversableGrid = emitTraversableGrid
    }

    /// Apply a new perception config at runtime (e.g. after an OTA config fetch).
    /// ROI, thresholds and `role` are consumed per-frame, so they take effect
    /// immediately (a walker→driver role switch re-derives the region live).
    /// `roadBackend` / `useMulticlassSegmentation` select which Core ML model is
    /// LOADED, decided at analyzer construction (app launch / config load). A
    /// runtime flip only takes effect next launch — surfaced here so it is not a
    /// silent no-op; the OTA path persists the config and the app rebuilds the
    /// analyzer on next start.
    func apply(config: PerceptionConfig) {
        self.config = config
    }

    func reset() {
        previousFingerprint = nil
    }

    func analyze(sampleBuffer: CMSampleBuffer) -> LocalVisionSignal {
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
            return LocalVisionSignal(
                hasHuman: false,
                humanDirection: .unknown,
                brightness: 0,
                sceneChangeScore: 1,
                isTooDark: false,
                isLikelyCovered: false,
                analyzerFailed: true
            )
        }
        return analyze(pixelBuffer: pixelBuffer)
    }

    func analyze(
        pixelBuffer: CVPixelBuffer,
        orientation: CGImagePropertyOrientation = .right,
        depthCues: LocalDepthCueSignal = LocalDepthCueSignal(),
        depthCapability: LocalPathCapability = LocalDepthCapabilityDetector.currentDepthCapability()
    ) -> LocalVisionSignal {
        let frameStart = DispatchTime.now()
        var timings = PerceptionFrameTimings()
        let luminance = Self.luminanceFingerprint(pixelBuffer: pixelBuffer)
        let brightness = luminance.average
        let previous = previousFingerprint
        previousFingerprint = luminance.fingerprint
        let sceneChangeScore = Self.changeScore(current: luminance.fingerprint, previous: previous)
        let human = Self.detectHuman(pixelBuffer: pixelBuffer, orientation: orientation)
        let yoloStart = DispatchTime.now()
        var perception = perceptionRunner
            .analyze(pixelBuffer: pixelBuffer, orientation: orientation)
            .merging(visionHuman: human)
        timings.yoloMs = Self.elapsedMs(since: yoloStart)
        if let roadBackend {
            let segStart = DispatchTime.now()
            if let maps = roadBackend.infer(
                pixelBuffer: pixelBuffer,
                orientation: orientation,
                config: config,
                emitGrid: emitTraversableGrid || roadBackend.id == .twinlite
            ) {
                perception.guidancePath = maps.guidancePath
                if let grid = maps.traversableGrid {
                    perception.traversableGrid = grid
                }
                if let lane = maps.laneGrid {
                    perception.laneGrid = lane
                }
                if let cues = maps.segmentationCues {
                    perception.segmentationCues = cues
                }
                timings.segmentationMs = roadBackend.lastInferenceMs ?? Self.elapsedMs(since: segStart)
            }
        }
        // Extra CamVid lane pixels only when the adaptor did not already emit lanes.
        if (laneRunner != nil || lanePolylineRunner != nil), perception.laneGrid == nil {
            let laneStart = DispatchTime.now()
            if let laneGrid = laneRunner?.analyze(pixelBuffer: pixelBuffer, orientation: orientation) {
                perception.laneGrid = laneGrid
            }
            if let polylines = lanePolylineRunner?.analyze(pixelBuffer: pixelBuffer, orientation: orientation) {
                perception.lanePolylines = polylines
            }
            timings.laneMs = Self.elapsedMs(since: laneStart)
        }
        var resolvedDepthCues = depthCues
        var resolvedDepthCapability = depthCapability
        if resolvedDepthCapability != .active {
            let depthStart = DispatchTime.now()
            if let monocularCue = monocularDepthRunner.analyze(pixelBuffer: pixelBuffer, orientation: orientation) {
                resolvedDepthCues = monocularCue
                resolvedDepthCapability = .active
            }
            timings.depthMs = Self.elapsedMs(since: depthStart)
        }
        if resolvedDepthCues.nearDrop != .unknown || resolvedDepthCues.nearestObstacleDirection != .unknown {
            perception.depthCues = resolvedDepthCues
        }
        let isLikelyCovered = brightness < 0.035 || brightness > 0.97
        let isTooDark = brightness < 0.12
        perception.pathGuidance = LocalPathGuidanceEngine.evaluate(
            perception: perception,
            isTooDark: isTooDark,
            isLikelyCovered: isLikelyCovered,
            depthCapability: resolvedDepthCapability,
            segmentationCues: perception.segmentationCues,
            config: config
        )

        timings.totalMs = Self.elapsedMs(since: frameStart)
        lastTimings = timings

        return LocalVisionSignal(
            hasHuman: human.hasHuman,
            humanDirection: human.direction,
            brightness: brightness,
            sceneChangeScore: sceneChangeScore,
            isTooDark: isTooDark,
            isLikelyCovered: isLikelyCovered,
            analyzerFailed: false,
            perception: perception
        )
    }

    private static func elapsedMs(since start: DispatchTime) -> Double {
        Double(DispatchTime.now().uptimeNanoseconds - start.uptimeNanoseconds) / 1_000_000.0
    }

    private static func detectHuman(pixelBuffer: CVPixelBuffer, orientation: CGImagePropertyOrientation = .right) -> (hasHuman: Bool, direction: LocalVisionDirection, boundingBox: CGRect?, confidence: Double) {
        let request = VNDetectHumanRectanglesRequest()
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: orientation, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return (false, .unknown, nil, 0)
        }

        let observations = request.results ?? []
        guard let strongest = observations.max(by: { $0.confidence < $1.confidence }) else {
            return (false, .unknown, nil, 0)
        }
        let centerX = strongest.boundingBox.midX
        let direction: LocalVisionDirection
        if centerX < 0.33 {
            direction = .left
        } else if centerX > 0.67 {
            direction = .right
        } else {
            direction = .center
        }
        return (true, direction, strongest.boundingBox, Double(strongest.confidence))
    }

    private static func changeScore(current: [Double], previous: [Double]?) -> Double {
        guard let previous, previous.count == current.count, !current.isEmpty else {
            // First frame should be treated as changed so the backend builds a baseline.
            return 1.0
        }
        let total = zip(current, previous).reduce(0.0) { partial, pair in
            partial + abs(pair.0 - pair.1)
        }
        return total / Double(current.count)
    }

    private static func luminanceFingerprint(pixelBuffer: CVPixelBuffer) -> (fingerprint: [Double], average: Double) {
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }

        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)
        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        guard let baseAddress = CVPixelBufferGetBaseAddress(pixelBuffer), width > 0, height > 0 else {
            return (Array(repeating: 0, count: 64), 0)
        }

        let raw = baseAddress.assumingMemoryBound(to: UInt8.self)
        let grid = 8
        var values: [Double] = []
        values.reserveCapacity(grid * grid)
        var sum = 0.0

        for gy in 0..<grid {
            for gx in 0..<grid {
                let x = min(width - 1, max(0, Int((Double(gx) + 0.5) * Double(width) / Double(grid))))
                let y = min(height - 1, max(0, Int((Double(gy) + 0.5) * Double(height) / Double(grid))))
                let offset = y * bytesPerRow + x * 4
                // Capture output is configured as kCVPixelFormatType_32BGRA.
                let b = Double(raw[offset])
                let g = Double(raw[offset + 1])
                let r = Double(raw[offset + 2])
                let luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
                values.append(luminance)
                sum += luminance
            }
        }
        return (values, sum / Double(values.count))
    }
}
