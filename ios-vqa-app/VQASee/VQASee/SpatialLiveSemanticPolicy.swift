import Foundation

nonisolated enum SpatialLiveSemanticPolicy {
    static func sameScene(_ a: [SpatialSurface], _ b: [SpatialSurface]) -> Bool {
        guard a.count == b.count else { return false }
        let left = a.sorted { $0.id < $1.id }, right = b.sorted { $0.id < $1.id }
        return zip(left,right).allSatisfy { x,y in
            x.id == y.id && x.kind == y.kind && x.epoch == y.epoch && x.observed == y.observed && x.vertices == y.vertices
        }
    }

    static func window(rows: [SpatialIndoorSemanticObservation], epoch: Int) -> [SpatialIndoorSemanticObservation] {
        Array(rows.filter { $0.epoch == epoch && $0.status == .processed && $0.taxonomy != nil
            && $0.width == 128 && $0.height == 128 && $0.labels.count == 128*128 && $0.confidence.count == 128*128 }
            .sorted { $0.frameTime > $1.frameTime }.prefix(4))
    }

    /// Keep only results belonging to the still-current archived analysis attempt.
    static func retainAttempts(computed: [SpatialIndoorSemanticObservation], current: [SpatialIndoorSemanticObservation]) -> [SpatialIndoorSemanticObservation] {
        computed.filter { candidate in
            current.contains { $0.attemptID == candidate.attemptID && $0.frameID == candidate.frameID && $0.epoch == candidate.epoch && $0.modelID == candidate.modelID && $0.taxonomy == candidate.taxonomy }
        }
    }

}

nonisolated struct SpatialLiveSemanticSnapshot {
    var scanID: UUID
    var epoch: Int
    var surfaces: [SpatialSurface]
    var rows: [SpatialIndoorSemanticObservation]
}

/// Separate from neural inference: bounded whole-scene reprojection preserves nearest-surface occlusion.
actor SpatialLiveSemanticReprojector {
    func reconcile(rows: [SpatialIndoorSemanticObservation], frames: [SpatialKeyframe], surfaces: [SpatialSurface]) -> [SpatialIndoorSemanticObservation] {
        let framesByID = Dictionary(uniqueKeysWithValues: frames.map { ($0.id,$0) })
        return rows.map { original in
            var row = original
            guard !Task.isCancelled, let frame = framesByID[row.frameID], frame.pose.epoch == row.epoch, let mask = row.mask else { return row }
            row.records = SpatialSurfaceSemanticEvidence.evaluateAll(surfaces: surfaces.filter { $0.epoch == row.epoch }, frame: frame, mask: mask)
            return row
        }
    }
}
