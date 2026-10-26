import Foundation
import simd

/// Three-plane intersection for P0 corners (two walls + floor). Planes in ax+by+cz+d=0 form.
nonisolated enum SpatialPlaneCornerSolver {
    struct Plane: Sendable, Equatable {
        var normal: SIMD3<Float>
        var offset: Float
        init(normal: SIMD3<Float>, offset: Float) {
            let len = simd_length(normal)
            guard len > 1e-6 else { self.normal = normal; self.offset = offset; return }
            self.normal = normal / len
            self.offset = offset / len
        }
        func signedDistance(_ p: SIMD3<Float>) -> Float { simd_dot(normal, p) + offset }
    }

    enum Failure: Error, Equatable { case parallel, illConditioned }

    /// Returns world-space corner if three planes meet at one point.
    static func intersect(_ p0: Plane, _ p1: Plane, _ p2: Plane) throws -> SpatialPoint {
        let n0 = p0.normal, n1 = p1.normal, n2 = p2.normal
        let det = simd_dot(n0, simd_cross(n1, n2))
        guard abs(det) > 1e-4 else { throw Failure.parallel }
        let rhs = SIMD3<Float>(-p0.offset, -p1.offset, -p2.offset)
        let point = (
            rhs.x * simd_cross(n1, n2) + rhs.y * simd_cross(n2, n0) + rhs.z * simd_cross(n0, n1)
        ) / det
        let residual = max(abs(p0.signedDistance(point)), abs(p1.signedDistance(point)), abs(p2.signedDistance(point)))
        guard residual < 0.05 else { throw Failure.illConditioned }
        return SpatialPoint(x: point.x, y: point.y, z: point.z)
    }
}
