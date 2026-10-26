import Foundation
import simd

/// Groups ARKit surface observations into stable plane tracks (P0 structure building block).
nonisolated enum SpatialPlaneTrackAssociation {
    struct PlaneObservation: Sendable, Equatable {
        var surfaceID: String
        var kind: String
        var normal: SIMD3<Float>
        var centroid: SIMD3<Float>
        var epoch: Int
    }

    struct Track: Sendable, Equatable {
        var id: String
        var kind: String
        var normal: SIMD3<Float>
        var anchor: SIMD3<Float>
        var memberSurfaceIDs: [String]
        var epoch: Int
    }

    struct Config: Sendable, Equatable {
        var maximumNormalAngleDegrees: Float = 8
        /// Point-to-plane offset. Coplanar wall patches merge across furniture gaps; centroid radius does not.
        var maximumPlaneOffsetMeters: Float = SpatialPlaneGrouping.offsetLimit
    }

    static func observations(from surfaces: [SpatialSurface]) -> [PlaneObservation] {
        surfaces.compactMap { surface in
            guard surface.observed, surface.vertices.count >= 3, surface.vertices.allSatisfy(\.isFinite),
                  let frame = planeFrame(vertices: surface.vertices) else { return nil }
            return PlaneObservation(
                surfaceID: surface.id,
                kind: surface.kind,
                normal: frame.normal,
                centroid: frame.centroid,
                epoch: surface.epoch
            )
        }
    }

    /// Greedy clustering within each (epoch, kind) bucket by plane equation, not centroid radius.
    static func associate(_ observations: [PlaneObservation], config: Config = Config()) -> [Track] {
        let cosLimit = Self.cosLimit(forDegrees: config.maximumNormalAngleDegrees)
        let offsetLimit = config.maximumPlaneOffsetMeters
        var tracks: [Track] = []
        let buckets = Dictionary(grouping: observations) { "\($0.epoch)|\($0.kind)" }
        for key in buckets.keys.sorted() {
            guard let bucket = buckets[key] else { continue }
            for obs in bucket.sorted(by: { $0.surfaceID < $1.surfaceID }) {
                if let index = tracks.firstIndex(where: { samePlane($0, obs, cosLimit: cosLimit, offsetLimit: offsetLimit) }) {
                    tracks[index].memberSurfaceIDs.append(obs.surfaceID)
                    let n = tracks[index].memberSurfaceIDs.count
                    tracks[index].anchor = (tracks[index].anchor * Float(n - 1) + obs.centroid) / Float(n)
                    tracks[index].normal = simd_normalize(tracks[index].normal + obs.normal)
                } else {
                    tracks.append(Track(
                        id: "track-\(tracks.count)",
                        kind: obs.kind,
                        normal: obs.normal,
                        anchor: obs.centroid,
                        memberSurfaceIDs: [obs.surfaceID],
                        epoch: obs.epoch
                    ))
                }
            }
        }
        return tracks
    }

    static func associate(surfaces: [SpatialSurface], config: Config = Config()) -> [Track] {
        associate(observations(from: surfaces), config: config)
    }

    private static func cosLimit(forDegrees degrees: Float) -> Float {
        cos(degrees * .pi / 180)
    }

    /// Same identity: kind, normal within 8°, and both centroids lie on the other's plane within 8 cm.
    /// Opposite walls have opposite normals and stay separate. Parallel walls with the same facing stay separate by offset.
    private static func samePlane(_ track: Track, _ obs: PlaneObservation, cosLimit: Float, offsetLimit: Float) -> Bool {
        guard track.epoch == obs.epoch, track.kind == obs.kind else { return false }
        guard simd_dot(track.normal, obs.normal) >= cosLimit else { return false }
        let forward = abs(simd_dot(track.normal, obs.centroid - track.anchor))
        let reverse = abs(simd_dot(obs.normal, track.anchor - obs.centroid))
        return forward <= offsetLimit && reverse <= offsetLimit
    }

    private static func planeFrame(vertices: [SpatialPoint]) -> (normal: SIMD3<Float>, centroid: SIMD3<Float>)? {
        let pts = vertices.map { SIMD3<Float>($0.x, $0.y, $0.z) }
        let centroid = pts.reduce(SIMD3<Float>(repeating: 0), +) / Float(pts.count)
        guard pts.count >= 3 else { return nil }
        var normal = simd_cross(pts[1] - pts[0], pts[2] - pts[0])
        guard simd_length(normal) > 1e-4 else { return nil }
        normal = simd_normalize(normal)
        if normal.y < 0 { normal = -normal }
        return (normal, centroid)
    }
}
