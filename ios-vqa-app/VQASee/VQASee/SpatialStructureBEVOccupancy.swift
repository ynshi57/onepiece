import Foundation
import simd

/// MapTR-style BEV occupancy from posed floor pixels, then vector polylines.
/// ARKit wall meshes are not required. Furniture edges stay unpublished unless they pass the 0.7 / two-frame wall gate.
nonisolated enum SpatialStructureBEVOccupancy {
    static let cellMeters = SpatialPlaneGrouping.offsetLimit
    static let simplifyMeters = SpatialPlaneGrouping.offsetLimit * 2

    struct Result: Sendable, Equatable {
        var segments: [SpatialStructureMap.WallSegment]
        var gaps: [SpatialStructureMap.Gap]
        var tracks: [SpatialPlaneTrackAssociation.Track]
        var rejections: [String: Int]
    }

    static func publish(
        frames: [SpatialKeyframe],
        views: [SpatialStructureWallLineSupport.View],
        floor: SpatialPlaneTrackAssociation.Track,
        existing: [SpatialStructureMap.WallSegment],
        startIndex: Int
    ) -> Result {
        var rejections: [String: Int] = [:]
        let points = floorHits(frames: frames, views: views, floor: floor)
        guard points.count >= 8 else {
            rejections["occupancy:insufficientFloorHits"] = 1
            return .init(segments: [], gaps: [], tracks: [], rejections: rejections)
        }
        let contour = simplify(walk(largest(grid(points)), floorHeight: floor.anchor.y), epsilon: simplifyMeters)
        guard contour.count >= 3 else {
            rejections["occupancy:noContour"] = 1
            return .init(segments: [], gaps: [], tracks: [], rejections: rejections)
        }
        let up = floor.normal.y >= 0 ? floor.normal : -floor.normal
        var segments: [SpatialStructureMap.WallSegment] = []
        var gaps: [SpatialStructureMap.Gap] = []
        var tracks: [SpatialPlaneTrackAssociation.Track] = []
        let closed = contour + [contour[0]]
        for i in 0..<(closed.count - 1) {
            let a = closed[i], b = closed[i + 1]
            let chord = b - a
            let length = simd_length(chord)
            guard length > cellMeters else { continue }
            let direction = chord / length
            var normal = simd_normalize(simd_cross(up, direction))
            if !normal.x.isFinite { continue }
            let origin = a
            let supported = SpatialStructureWallLineSupport.intervals(
                origin: origin, direction: direction, up: up, lo: 0, hi: length, views: views
            )
            if supported.isEmpty {
                rejections["occupancy:noLocalWallSupport", default: 0] += 1
                continue
            }
            let trackID = "occupancy-\(tracks.count)"
            let unexplained = supported.compactMap { interval -> SpatialStructureWallLineSupport.Interval? in
                let lo = interval.lo, hi = interval.hi
                let samples = max(2, Int(ceil((hi - lo) / cellMeters)))
                var keep: [(Float, Bool)] = []
                for s in 0..<samples {
                    let t = lo + (hi - lo) * (Float(s) + 0.5) / Float(samples)
                    keep.append((t, explained(origin + direction * t, existing)))
                }
                let run = keep.filter { !$0.1 }
                guard let first = run.first, let last = run.last, last.0 - first.0 > cellMeters else { return nil }
                return .init(lo: first.0, hi: last.0, frameIDs: interval.frameIDs)
            }
            guard !unexplained.isEmpty else {
                rejections["occupancy:explainedByExisting", default: 0] += 1
                continue
            }
            let mid = origin + direction * ((unexplained.first!.lo + unexplained.last!.hi) / 2)
            tracks.append(.init(
                id: trackID, kind: "vertical", normal: normal, anchor: mid,
                memberSurfaceIDs: [], epoch: floor.epoch
            ))
            for (j, range) in unexplained.enumerated() {
                let start = origin + direction * range.lo
                let end = origin + direction * range.hi
                segments.append(.init(
                    id: "wall-p0-\(startIndex + tracks.count - 1)-\(j)",
                    verticalTrackID: trackID,
                    start: point(start), end: point(end),
                    startProvenance: .candidate, endProvenance: .candidate,
                    lengthMeters: range.hi - range.lo,
                    sourceSurfaceIDs: [], frameIDs: range.frameIDs
                ))
            }
            for j in 1..<unexplained.count {
                gaps.append(.init(
                    verticalTrackID: trackID,
                    start: point(origin + direction * unexplained[j - 1].hi),
                    end: point(origin + direction * unexplained[j].lo),
                    sourceSurfaceIDs: [],
                    frameIDs: Array(Set(unexplained[j - 1].frameIDs + unexplained[j].frameIDs)).sorted()
                ))
            }
        }
        return .init(segments: segments, gaps: gaps, tracks: tracks, rejections: rejections)
    }

    static func floorHits(
        frames: [SpatialKeyframe],
        views: [SpatialStructureWallLineSupport.View],
        floor: SpatialPlaneTrackAssociation.Track
    ) -> [SIMD3<Float>] {
        let byID = Dictionary(uniqueKeysWithValues: frames.map { ($0.id, $0) })
        var points: [SIMD3<Float>] = []
        for view in views {
            guard let frame = byID[view.frameID] else { continue }
            for y in stride(from: 2, to: view.height - 2, by: 4) {
                for x in stride(from: 2, to: view.width - 2, by: 4) {
                    let index = y * view.width + x
                    guard index < view.labels.count, index < view.confidence.count,
                          Float(view.confidence[index]) / 255 >= SpatialSurfaceSemanticEvidence.minimumScore,
                          view.labels[index] == view.floorLabel else { continue }
                    let pixel: [Float] = [(Float(x) + 0.5) / Float(view.width), (Float(y) + 0.5) / Float(view.height)]
                    guard let ray = SpatialObservationGeometry.bearing(pixel: pixel, frame: frame) else { continue }
                    let origin = SIMD3(ray.origin.x, ray.origin.y, ray.origin.z)
                    let direction = SIMD3(ray.direction.x, ray.direction.y, ray.direction.z)
                    let denominator = simd_dot(floor.normal, direction)
                    guard abs(denominator) > 0.000001 else { continue }
                    let t = simd_dot(floor.normal, floor.anchor - origin) / denominator
                    guard t > 0.3, t <= 6 else { continue }
                    let p = origin + direction * t
                    guard abs(simd_dot(p - floor.anchor, floor.normal)) <= SpatialPlaneGrouping.offsetLimit else { continue }
                    points.append(p)
                }
            }
        }
        return points
    }

    static func grid(_ points: [SIMD3<Float>]) -> (origin: SIMD2<Float>, width: Int, height: Int, mask: [Bool]) {
        let xs = points.map(\.x), zs = points.map(\.z)
        let minX = (xs.min() ?? 0) - cellMeters * 4, maxX = (xs.max() ?? 0) + cellMeters * 4
        let minZ = (zs.min() ?? 0) - cellMeters * 4, maxZ = (zs.max() ?? 0) + cellMeters * 4
        let width = max(3, Int(ceil((maxX - minX) / cellMeters)))
        let height = max(3, Int(ceil((maxZ - minZ) / cellMeters)))
        var counts = Array(repeating: 0, count: width * height)
        for p in points {
            let j = Int((p.x - minX) / cellMeters), i = Int((p.z - minZ) / cellMeters)
            if i >= 0, j >= 0, i < height, j < width { counts[i * width + j] += 1 }
        }
        return (SIMD2(minX, minZ), width, height, counts.map { $0 >= 2 })
    }

    static func largest(_ grid: (origin: SIMD2<Float>, width: Int, height: Int, mask: [Bool])) -> (origin: SIMD2<Float>, width: Int, height: Int, mask: [Bool]) {
        let width = grid.width, height = grid.height
        var seen = Array(repeating: false, count: width * height)
        var best: [Int] = []
        for seed in grid.mask.indices where grid.mask[seed] && !seen[seed] {
            var stack = [seed], cells: [Int] = []
            seen[seed] = true
            while let i = stack.popLast() {
                cells.append(i)
                let y = i / width, x = i % width
                for (dx, dy) in [(1, 0), (-1, 0), (0, 1), (0, -1)] {
                    let nx = x + dx, ny = y + dy, ni = ny * width + nx
                    if nx >= 0, ny >= 0, nx < width, ny < height, grid.mask[ni], !seen[ni] {
                        seen[ni] = true; stack.append(ni)
                    }
                }
            }
            if cells.count > best.count { best = cells }
        }
        var mask = Array(repeating: false, count: width * height)
        for i in best { mask[i] = true }
        return (grid.origin, width, height, mask)
    }

    static func walk(_ grid: (origin: SIMD2<Float>, width: Int, height: Int, mask: [Bool]), floorHeight: Float) -> [SIMD3<Float>] {
        let width = grid.width
        let rows = grid.height
        func on(_ x: Int, _ y: Int) -> Bool {
            x >= 0 && y >= 0 && x < width && y < rows && grid.mask[y * width + x]
        }
        var start: (Int, Int)?
        for y in 0..<rows where start == nil {
            for x in 0..<width where on(x, y) { start = (x, y); break }
        }
        guard let start else { return [] }
        let nb = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
        var back = 4, x = start.0, y = start.1
        var contour = [start]
        for _ in 0..<(width * rows * 2) {
            var hit: Int?
            for t in 1...8 {
                let k = (back + t) % 8
                if on(x + nb[k].0, y + nb[k].1) { hit = k; break }
            }
            guard let hit else { break }
            x += nb[hit].0
            y += nb[hit].1
            if (x, y) == start, contour.count > 8 { break }
            contour.append((x, y))
            back = (hit + 4) % 8
        }
        let origin = grid.origin
        return contour.map { cell in
            SIMD3(origin.x + (Float(cell.0) + 0.5) * cellMeters, floorHeight, origin.y + (Float(cell.1) + 0.5) * cellMeters)
        }
    }

    static func simplify(_ points: [SIMD3<Float>], epsilon: Float) -> [SIMD3<Float>] {
        guard points.count >= 3 else { return points }
        func perpendicular(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ p: SIMD3<Float>) -> Float {
            let ab = b - a, length = simd_length(ab)
            if length < 0.000001 { return simd_length(p - a) }
            return simd_length(simd_cross(ab, p - a)) / length
        }
        func rec(_ pts: [SIMD3<Float>]) -> [SIMD3<Float>] {
            guard pts.count >= 3 else { return pts }
            var maxD: Float = -1, index = 0
            for i in 1..<(pts.count - 1) {
                let d = perpendicular(pts[0], pts[pts.count - 1], pts[i])
                if d > maxD { maxD = d; index = i }
            }
            if maxD > epsilon {
                let left = rec(Array(pts[...index])), right = rec(Array(pts[index...]))
                return left.dropLast() + right
            }
            return [pts[0], pts[pts.count - 1]]
        }
        return rec(points)
    }

    private static func explained(_ point: SIMD3<Float>, _ existing: [SpatialStructureMap.WallSegment]) -> Bool {
        existing.contains { segment in
            let start = vector(segment.start), d = vector(segment.end) - start
            let denom = simd_dot(d, d)
            let t = denom > 0 ? max(0, min(1, simd_dot(point - start, d) / denom)) : 0
            return simd_distance(point, start + t * d) <= simplifyMeters
        }
    }

    private static func vector(_ p: SpatialPoint) -> SIMD3<Float> { SIMD3(p.x, p.y, p.z) }
    private static func point(_ p: SIMD3<Float>) -> SpatialPoint { SpatialPoint(x: p.x, y: p.y, z: p.z) }
}
