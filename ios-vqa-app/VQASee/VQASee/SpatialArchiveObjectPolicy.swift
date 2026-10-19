import Foundation

nonisolated enum SpatialArchiveObjectPolicy {
    /// Only actual saved keyframes participate. A failed observation remains accounted for,
    /// and can be retried explicitly without creating an automatic retry loop.
    static func nextFrame(in document: SpatialScanDocument) -> SpatialKeyframe? {
        let accounted = Set((document.objectObservations ?? []).map(\.frameID))
        return document.keyframes.first { !accounted.contains($0.id) }
    }
}
