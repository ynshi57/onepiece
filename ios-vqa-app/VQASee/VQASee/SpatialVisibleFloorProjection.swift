import Foundation

/// Semantic record alignment for plane classification — not floor tile projection.
nonisolated enum SpatialVisibleFloorProjection {
    static func validatedRecords(frames: [SpatialKeyframe], rows: [SpatialIndoorSemanticObservation]) -> [SpatialSurfaceSemanticRecord] {
        rows.filter { $0.status == .processed }.flatMap(\.records).filter { record in
            let sources = frames.filter { $0.id == record.frameID }
            guard sources.count == 1, let source = sources.first else { return false }
            return source.time == record.frameTime && source.pose.epoch == record.epoch && validFrame(source)
        }
    }

    static func validFrame(_ frame: SpatialKeyframe) -> Bool {
        guard frame.time.isFinite, frame.pose.time.isFinite, abs(frame.time - frame.pose.time) < 0.000001,
              frame.pose.position.isFinite, frame.pose.forward.isFinite, frame.pose.epoch >= 0,
              frame.cameraToWorld.count == 16 else { return false }
        return abs(frame.cameraToWorld[12] - frame.pose.position.x) < 0.001
            && abs(frame.cameraToWorld[13] - frame.pose.position.y) < 0.001
            && abs(frame.cameraToWorld[14] - frame.pose.position.z) < 0.001
            && SpatialObservationGeometry.bearing(pixel: [0.5, 0.5], frame: frame) != nil
    }
}
