import Foundation

/// Geometric evidence only. Neither equal depth nor lost segmentation proves identity.
enum IndoorLiveObservation: String {
    case unavailable, photoOnly, outsideView, observing, noDepth, referenceOnly
    case surfaceConsistent, obstructionEvidence, originalSurfaceNotObserved
    var message: String {
        switch self {
        case .unavailable: return "正在找回原位置，暂时无法检查变化"
        case .photoOnly: return "只记住了照片，尚未取得可指回的空间位置"
        case .outsideView: return "原位置在画面外，请按方向转动手机"
        case .observing: return "正在连续观察原位置…"
        case .noDepth: return "当前没有可靠深度，暂时无法自动判断遮挡或移走"
        case .referenceOnly: return "仅有平面参考位置，暂时无法自动判断物品变化"
        case .surfaceConsistent: return "原位置仍有表面；是否同一物品尚未确认"
        case .obstructionEvidence: return "原位置前方出现遮挡线索，暂时无法确认物品"
        case .originalSurfaceNotObserved: return "原表面未测到，可能移走；尚未确认物品消失"
        }
    }
}

struct IndoorDepthObservationFilter {
    private var candidate: IndoorLiveObservation?
    private var since: TimeInterval = 0
    private var last: TimeInterval = -.infinity
    private var count = 0
    private var output: IndoorLiveObservation = .observing
    mutating func reset() { self = Self() }
    mutating func offer(expected: Float, samples: [Float], timestamp: TimeInterval) -> IndoorLiveObservation {
        if timestamp == last { return output }
        guard timestamp.isFinite, timestamp > last, expected.isFinite, (0.15...3).contains(expected),
              samples.count >= 7, samples.allSatisfy({ $0.isFinite && (0.15...5).contains($0) }) else {
            reset(); return .noDepth
        }
        let sorted = samples.sorted(), median = sorted[sorted.count/2]
        // Conservative geometry margins, experimental and not calibrated identity thresholds.
        guard sorted[sorted.count-2] - sorted[1] <= 0.08 else { reset(); return .noDepth }
        let delta = median - expected
        let next: IndoorLiveObservation = delta < -0.15 ? .obstructionEvidence : delta > 0.15 ? .originalSurfaceNotObserved : .surfaceConsistent
        if next != candidate || timestamp-last > 0.6 { candidate = next; since = timestamp; count = 0 }
        count += 1; last = timestamp
        output = count >= 4 && timestamp-since >= 0.8 ? next : .observing
        return output
    }
}
