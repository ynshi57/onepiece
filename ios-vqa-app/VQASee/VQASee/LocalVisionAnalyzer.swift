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
    static let sceneChangeThreshold = 0.18
    static let heartbeatMs: Double = 6_000

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
        if signal.hasHuman {
            return .send("本地检测到疑似人形")
        }
        if signal.isLikelyCovered || signal.isTooDark {
            return .send("本地检测到画面质量风险")
        }
        if signal.sceneChangeScore >= sceneChangeThreshold {
            return .send("画面变化明显")
        }
        guard let elapsed = millisecondsSinceLastBackendFrame else {
            return .send("行走模式首帧")
        }
        if elapsed >= heartbeatMs {
            return .send("行走模式安全心跳")
        }
        return .skip("画面稳定，等待变化或心跳")
    }
}

/// Apple Vision based local fast analyzer. It currently uses built-in Vision
/// human rectangles plus a tiny luminance fingerprint. No custom Core ML model is
/// bundled yet.
final class LocalVisionAnalyzer {
    private var previousFingerprint: [Double]?

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

        let luminance = Self.luminanceFingerprint(pixelBuffer: pixelBuffer)
        let brightness = luminance.average
        let previous = previousFingerprint
        previousFingerprint = luminance.fingerprint
        let sceneChangeScore = Self.changeScore(current: luminance.fingerprint, previous: previous)
        let human = Self.detectHuman(pixelBuffer: pixelBuffer)
        let isLikelyCovered = brightness < 0.035 || brightness > 0.97
        let isTooDark = brightness < 0.12

        return LocalVisionSignal(
            hasHuman: human.hasHuman,
            humanDirection: human.direction,
            brightness: brightness,
            sceneChangeScore: sceneChangeScore,
            isTooDark: isTooDark,
            isLikelyCovered: isLikelyCovered,
            analyzerFailed: false
        )
    }

    private static func detectHuman(pixelBuffer: CVPixelBuffer) -> (hasHuman: Bool, direction: LocalVisionDirection) {
        let request = VNDetectHumanRectanglesRequest()
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: .right, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return (false, .unknown)
        }

        let observations = request.results ?? []
        guard let strongest = observations.max(by: { $0.confidence < $1.confidence }) else {
            return (false, .unknown)
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
        return (true, direction)
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
