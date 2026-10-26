import Foundation

/// Visible-surface candidate for one detection. Not an object centre, identity, or measured size.
nonisolated struct SpatialObjectLocationCandidate: Sendable, Equatable, Identifiable {
    var id: String
    var label: String
    var sourceFrameID: String
    var sourceAttemptID: UUID
    var detectionIndex: Int
    var targetFrameID: String
    var surfacePosition: SpatialPoint
    var widthStart: SpatialPoint
    var widthEnd: SpatialPoint
    var widthMeters: Float
    var heightMeters: Float
    var spreadMeters: Float
    var supportCount: Int
    var isClipped: Bool
    var reason: String
}

/// Builds per-detection candidates from already-triangulated in-box supports.
/// Thickness / object centre / cross-frame identity are out of scope.
nonisolated enum SpatialObjectLocationBuilder {
    static let minimumSupports = 3

    static func candidates(document: SpatialScanDocument) -> [SpatialObjectLocationCandidate] {
        let frames = Dictionary(uniqueKeysWithValues: document.keyframes.map { ($0.id, $0) })
        var current: [UUID: SpatialObjectObservation] = [:]
        for row in document.objectObservations ?? [] {
            if let id = row.attemptID { current[id] = row }
        }
        var latest: [String: SpatialObjectSupportAttempt] = [:]
        for attempt in document.objectSupportAttempts ?? [] {
            latest["\(attempt.sourceAttemptID.uuidString)-\(attempt.detectionIndex)"] = attempt
        }
        return latest.values.compactMap { attempt in
            guard attempt.status == .measured,
                  let source = frames[attempt.sourceFrameID],
                  let targetID = attempt.targetFrameID, frames[targetID] != nil,
                  let row = current[attempt.sourceAttemptID],
                  row.frameID == attempt.sourceFrameID,
                  row.detections.indices.contains(attempt.detectionIndex)
            else { return nil }
            return candidate(attempt: attempt, detection: row.detections[attempt.detectionIndex], source: source)
        }.sorted { lhs, rhs in
            if lhs.sourceFrameID != rhs.sourceFrameID { return lhs.sourceFrameID < rhs.sourceFrameID }
            return lhs.detectionIndex < rhs.detectionIndex
        }
    }

    static func candidate(attempt: SpatialObjectSupportAttempt, detection: SpatialObjectDetection,
                          source: SpatialKeyframe) -> SpatialObjectLocationCandidate? {
        let supports = attempt.supports
        guard supports.count >= minimumSupports, let targetID = attempt.targetFrameID else { return nil }
        let positions = supports.map(\.position)
        guard let median = medianPoint(positions) else { return nil }
        let spread = medianValue(positions.map { SpatialScanGeometry.distance($0, median) })
        guard let spread, spread.isFinite else { return nil }
        let depths = supports.compactMap { SpatialObservationGeometry.project(position: $0.position, frame: source)?.depth }
        guard depths.count == supports.count, let depth = medianValue(depths), depth > 0 else { return nil }
        let box = detection.boundingBox
        guard box.count == 4 else { return nil }
        let midX = (box[0] + box[2]) * 0.5, midY = (box[1] + box[3]) * 0.5
        guard let left = SpatialObservationGeometry.position(depthMeters: depth, pixel: [box[0], midY], frame: source),
              let right = SpatialObservationGeometry.position(depthMeters: depth, pixel: [box[2], midY], frame: source),
              let top = SpatialObservationGeometry.position(depthMeters: depth, pixel: [midX, box[1]], frame: source),
              let bottom = SpatialObservationGeometry.position(depthMeters: depth, pixel: [midX, box[3]], frame: source)
        else { return nil }
        let width = SpatialScanGeometry.distance(left, right)
        let height = SpatialScanGeometry.distance(top, bottom)
        guard width.isFinite, height.isFinite, width > 0, height > 0 else { return nil }
        return SpatialObjectLocationCandidate(
            id: "\(attempt.sourceAttemptID.uuidString)-\(attempt.detectionIndex)",
            label: detection.label,
            sourceFrameID: attempt.sourceFrameID,
            sourceAttemptID: attempt.sourceAttemptID,
            detectionIndex: attempt.detectionIndex,
            targetFrameID: targetID,
            surfacePosition: median,
            widthStart: left,
            widthEnd: right,
            widthMeters: width,
            heightMeters: height,
            spreadMeters: spread,
            supportCount: supports.count,
            isClipped: detection.isClipped,
            reason: "可见面候选，进深未知；可能是背景；不是物品中心"
        )
    }

    private static func medianPoint(_ points: [SpatialPoint]) -> SpatialPoint? {
        guard let x = medianValue(points.map(\.x)), let y = medianValue(points.map(\.y)),
              let z = medianValue(points.map(\.z)) else { return nil }
        return SpatialPoint(x: x, y: y, z: z)
    }

    private static func medianValue(_ values: [Float]) -> Float? {
        guard !values.isEmpty else { return nil }
        let sorted = values.sorted()
        let mid = sorted.count / 2
        if sorted.count % 2 == 0 { return (sorted[mid - 1] + sorted[mid]) / 2 }
        return sorted[mid]
    }
}
