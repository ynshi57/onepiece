import Foundation
import CoreGraphics
import simd

/// Coordinates belong to exactly one AR tracking generation; names are user supplied.
struct IndoorMemoryRecord: Identifiable {
    let id: UUID
    let name: String
    let recordedAt: Date
    let sessionID: UUID
    let photo: Data
    var target: IndoorTargetEvidence? = nil
    var hasSpatialAnchor: Bool = false
    var spatialSource: String? = nil
    var lastObservation: IndoorMemoryObservation? = nil
}

struct IndoorMemoryObservation: Codable {
    let observedAt: Date
    let confirmedAt: Date
    let photo: Data
    let present: Bool
    // Presence/absence is explicitly user confirmed, not an automatic model result.
}

struct IndoorRecordMetadata: Codable {
    var target: IndoorTargetEvidence?
    var hasSpatialAnchor: Bool
    var spatialSource: String? = nil
    var lastObservation: IndoorMemoryObservation? = nil
}

enum IndoorMemoryPolicy {
    static let capacity = 10
    static let maximumFrameAge: TimeInterval = 0.25

    static func restorationExpired(startedAt: TimeInterval?, now: TimeInterval) -> Bool {
        guard let startedAt else { return false }
        return now - startedAt >= 15
    }

    static func validName(_ input: String) -> String? {
        let name = input.trimmingCharacters(in: .whitespacesAndNewlines)
        return name.isEmpty || name.count > 30 ? nil : name
    }

    static func canProject(recordSession: UUID, currentSession: UUID,
                           trackingNormal: Bool, frameAge: TimeInterval) -> Bool {
        recordSession == currentSession && trackingNormal && frameAge >= 0
            && frameAge <= maximumFrameAge
    }

    /// Input is UI-oriented projection, not sensor axes (which rotate in portrait).
    static func turnHint(isBehind: Bool, projected: CGPoint, visibleRect: CGRect) -> String {
        if isBehind { return "记录位置在身后，请原地转动手机" }
        guard projected.x.isFinite, projected.y.isFinite else { return "请缓慢转动手机寻找记录位置" }
        let dx = projected.x - visibleRect.midX
        let dy = projected.y - visibleRect.midY
        if abs(dx) > abs(dy) {
            return dx > 0 ? "向右转动手机" : "向左转动手机"
        }
        return dy < 0 ? "抬高手机" : "放低手机"
    }
}
