import Foundation
import simd

/// Groups raw ARKit polygons that lie on nearly the same plane and almost touch.
/// A group is a coplanar candidate, not a semantic wall, floor, or piece of furniture.
nonisolated enum SpatialPlaneGrouping {
    struct Candidate: Equatable {
        var surfaceIDs: [String]
        var kind: String
        /// Vertical extent of at least 1.2 m. Short vertical patches stay unmarked.
        var tall: Bool
    }

    static let angleLimit: Float = 10 * .pi / 180
    static let offsetLimit: Float = 0.08
    static let gapLimit: Float = 0.25
    static let tallHeight: Float = 1.2
    /// Horizontal vertices farther than this from the walked path are not part of the vector layer.
    static let pathRadius: Float = 0.8

    struct VectorPiece: Equatable {
        var surfaceID: String
        /// wall, object, or support. None of these is a finished room.
        var role: String
        var chains: [[SpatialPoint]]
        /// True only when every vertex of a vertical patch is kept, so the ring may close.
        var closed: Bool
    }

    static func vectorPieces(surfaces: [SpatialSurface], poses: [SpatialPose]) -> [VectorPiece] {
        let walked = poses.filter { $0.position.isFinite }.map { (x: $0.position.x, z: $0.position.z) }
        return surfaces.compactMap { surface in
            guard surface.observed, surface.vertices.count >= 3, surface.vertices.allSatisfy(\.isFinite) else { return nil }
            if surface.kind == "vertical" {
                let role = isTallVertical(surface) ? "wall" : "object"
                return VectorPiece(surfaceID: surface.id, role: role, chains: [surface.vertices], closed: true)
            }
            guard surface.kind == "horizontal", !walked.isEmpty else { return nil }
            let runs = chains(surface.vertices) { nearPath($0, walked: walked) }
            let kept = runs.filter { $0.count >= 2 }
            guard !kept.isEmpty else { return nil }
            let complete = kept.count == 1 && kept[0].count == surface.vertices.count
            return VectorPiece(surfaceID: surface.id, role: "support", chains: kept, closed: complete)
        }
    }

    private static func nearPath(_ point: SpatialPoint, walked: [(x: Float, z: Float)]) -> Bool {
        walked.contains { hypot(point.x - $0.x, point.z - $0.z) <= pathRadius }
    }

    private static func chains(_ vertices: [SpatialPoint], near: (SpatialPoint) -> Bool) -> [[SpatialPoint]] {
        let flags = vertices.map(near)
        guard flags.contains(true) else { return [] }
        if flags.allSatisfy({ $0 }) { return [vertices] }
        var runs: [[SpatialPoint]] = []
        var run: [SpatialPoint] = []
        for (vertex, ok) in zip(vertices, flags) {
            if ok { run.append(vertex) }
            else if !run.isEmpty { runs.append(run); run = [] }
        }
        if !run.isEmpty { runs.append(run) }
        if flags[0], flags[vertices.count - 1], runs.count >= 2 {
            let tail = runs.removeLast()
            runs[0] = tail + runs[0]
        }
        return runs
    }

    /// A wall centerline in the top view. Longer gaps stay open; this is not a finished room.
    struct WallLine: Equatable {
        var surfaceIDs: [String]
        /// wall or object. Photo-inferred feet were removed.
        var role: String
        var start: SpatialPoint
        var end: SpatialPoint
    }

    /// Both walls must already aim at the intersection, and each extension stays under a doorway.
    static let cornerExtend: Float = 0.6
    /// An endpoint this close to another wall segment is the same corner.
    static let joinRadius: Float = 0.15

    static func wallLines(surfaces: [SpatialSurface]) -> [WallLine] {
        let verticals = surfaces.filter {
            $0.observed && $0.kind == "vertical" && $0.vertices.count >= 3 && $0.vertices.allSatisfy(\.isFinite)
        }
        let groups = candidates(verticals)
        var lines: [Footprint] = []
        for group in groups where group.tall {
            let members = verticals.filter { group.surfaceIDs.contains($0.id) }
            if let line = footprint(members) { lines.append(line) }
        }
        let sliverSource = lines
        lines.removeAll { line in
            guard line.span < gapLimit else { return false }
            let mid = (line.start + line.end) * 0.5
            return sliverSource.contains { other in
                other.span > line.span + 0.3 && aligned(line, other) && lineOffset(mid, other) <= offsetLimit
            }
        }
        extendCorners(&lines)
        snapEnds(&lines)
        trimOverhangs(&lines)
        var result = lines.map { line in
            WallLine(surfaceIDs: line.surfaceIDs, role: "wall", start: point(line.start), end: point(line.end))
        }
        for group in groups where !group.tall {
            let members = verticals.filter { group.surfaceIDs.contains($0.id) }
            guard let line = footprint(members) else { continue }
            let mid = (line.start + line.end) * 0.5
            let clear = lines.allSatisfy { closest(mid, $0).distance > gapLimit }
            if clear {
                result.append(WallLine(surfaceIDs: line.surfaceIDs, role: "object", start: point(line.start), end: point(line.end)))
            }
        }
        return result
    }

    private struct Footprint {
        var surfaceIDs: [String]
        var start: SIMD2<Float>
        var end: SIMD2<Float>
        var direction: SIMD2<Float>
        var span: Float { simd_dot(end - start, direction) }
    }

    private static func footprint(_ surfaces: [SpatialSurface]) -> Footprint? {
        let fitted = surfaces.compactMap { surface -> (SpatialSurface, SIMD3<Float>)? in
            guard let fitted = fit(surface.vertices) else { return nil }
            return (surface, fitted.0)
        }
        guard let anchor = fitted.max(by: { spanOf($0.0, normal: $0.1) < spanOf($1.0, normal: $1.1) }) else { return nil }
        var dx = -anchor.1.z
        var dz = anchor.1.x
        let length = hypot(dx, dz)
        guard length > 1e-4 else { return nil }
        dx /= length
        dz /= length
        let count = Float(anchor.0.vertices.count)
        let cx = anchor.0.vertices.reduce(Float(0)) { $0 + $1.x } / count
        let cz = anchor.0.vertices.reduce(Float(0)) { $0 + $1.z } / count
        let samples = surfaces.flatMap(\.vertices)
        let along = samples.map { ($0.x - cx) * dx + ($0.z - cz) * dz }
        guard let t0 = along.min(), let t1 = along.max(), t1 - t0 > 0.05 else { return nil }
        return Footprint(
            surfaceIDs: surfaces.map(\.id).sorted(),
            start: SIMD2(cx + t0 * dx, cz + t0 * dz),
            end: SIMD2(cx + t1 * dx, cz + t1 * dz),
            direction: SIMD2(dx, dz))
    }

    private static func spanOf(_ surface: SpatialSurface, normal: SIMD3<Float>) -> Float {
        var dx = -normal.z
        var dz = normal.x
        let length = hypot(dx, dz)
        guard length > 1e-4 else { return 0 }
        dx /= length
        dz /= length
        let along = surface.vertices.map { $0.x * dx + $0.z * dz }
        return (along.max() ?? 0) - (along.min() ?? 0)
    }

    private static func aligned(_ left: Footprint, _ right: Footprint) -> Bool {
        abs(left.direction.x * right.direction.y - left.direction.y * right.direction.x) <= sin(angleLimit)
    }

    private static func lineOffset(_ point: SIMD2<Float>, _ line: Footprint) -> Float {
        let normal = SIMD2(-line.direction.y, line.direction.x)
        return abs(simd_dot(point - line.start, normal))
    }

    private static func intersection(_ left: Footprint, _ right: Footprint) -> (SIMD2<Float>, Float, Float)? {
        let det = left.direction.x * right.direction.y - left.direction.y * right.direction.x
        guard abs(det) > 1e-4 else { return nil }
        let diff = right.start - left.start
        let t = (diff.x * right.direction.y - diff.y * right.direction.x) / det
        let u = (diff.x * left.direction.y - diff.y * left.direction.x) / det
        return (left.start + t * left.direction, t, u)
    }

    private static func closest(_ point: SIMD2<Float>, _ line: Footprint) -> (distance: Float, foot: SIMD2<Float>) {
        let span = max(line.span, 1e-4)
        let t = min(max(simd_dot(point - line.start, line.direction), 0), span)
        let foot = line.start + t * line.direction
        return (simd_length(point - foot), foot)
    }

    private static func reseat(_ line: inout Footprint) {
        var delta = line.end - line.start
        guard simd_length(delta) > 1e-4 else { return }
        if simd_dot(delta, line.direction) < 0 {
            swap(&line.start, &line.end)
            delta = line.end - line.start
        }
        line.direction = simd_normalize(delta)
    }

    private static func extendCorners(_ lines: inout [Footprint]) {
        var moves: [(Int, Bool, SIMD2<Float>)] = []
        for left in lines.indices {
            for right in lines.indices where right > left {
                guard let (point, t, u) = intersection(lines[left], lines[right]) else { continue }
                let leftSpan = lines[left].span
                let rightSpan = lines[right].span
                let leftExtend = t < 0 ? -t : (t > leftSpan ? t - leftSpan : 0)
                let rightExtend = u < 0 ? -u : (u > rightSpan ? u - rightSpan : 0)
                guard leftExtend > 0, leftExtend <= cornerExtend, rightExtend > 0, rightExtend <= cornerExtend else { continue }
                moves.append((left, t < 0, point))
                moves.append((right, u < 0, point))
            }
        }
        for (index, isStart, point) in moves {
            if isStart { lines[index].start = point } else { lines[index].end = point }
            reseat(&lines[index])
        }
    }

    private static func snapEnds(_ lines: inout [Footprint]) {
        for index in lines.indices {
            for isStart in [true, false] {
                let point = isStart ? lines[index].start : lines[index].end
                var best: (Float, SIMD2<Float>)?
                for other in lines.indices where other != index {
                    let hit = closest(point, lines[other])
                    if hit.distance <= joinRadius && (best == nil || hit.distance < best!.0) {
                        best = (hit.distance, hit.foot)
                    }
                }
                if let best {
                    if isStart { lines[index].start = best.1 } else { lines[index].end = best.1 }
                    reseat(&lines[index])
                }
            }
        }
    }

    private static func trimOverhangs(_ lines: inout [Footprint]) {
        for index in lines.indices {
            for other in lines.indices where other != index {
                guard let (point, t, u) = intersection(lines[index], lines[other]) else { continue }
                let span = lines[index].span
                let otherSpan = lines[other].span
                let onSelf = t >= 0 && t <= span
                let nearOtherEnd = min(abs(u), abs(u - otherSpan)) <= joinRadius
                guard onSelf, nearOtherEnd else { continue }
                if t <= cornerExtend {
                    lines[index].start = point
                } else if span - t <= cornerExtend {
                    lines[index].end = point
                } else {
                    continue
                }
                reseat(&lines[index])
            }
        }
    }

    private static func point(_ xz: SIMD2<Float>) -> SpatialPoint {
        SpatialPoint(x: xz.x, y: 0, z: xz.y)
    }

    static func isTallVertical(_ surface: SpatialSurface) -> Bool {
        guard surface.kind == "vertical", surface.vertices.count >= 2 else { return false }
        let ys = surface.vertices.map(\.y)
        return (ys.max() ?? 0) - (ys.min() ?? 0) >= tallHeight
    }

    static func candidates(_ surfaces: [SpatialSurface]) -> [Candidate] {
        let usable = surfaces.filter { $0.observed && $0.vertices.count >= 3 && $0.vertices.allSatisfy(\.isFinite) }
        let planes = usable.compactMap { surface -> (SpatialSurface, SIMD3<Float>, Float)? in
            guard let fitted = fit(surface.vertices) else { return nil }
            return (surface, fitted.0, fitted.1)
        }
        var parent = Array(planes.indices)
        func find(_ index: Int) -> Int {
            var index = index
            while parent[index] != index {
                parent[index] = parent[parent[index]]
                index = parent[index]
            }
            return index
        }
        for left in planes.indices {
            for right in planes.indices where right > left && samePlane(planes[left], planes[right]) {
                parent[find(left)] = find(right)
            }
        }
        var buckets: [Int: [Int]] = [:]
        for index in planes.indices { buckets[find(index), default: []].append(index) }
        return buckets.keys.sorted().map { key in
            let members = buckets[key] ?? []
            let ids = members.map { planes[$0].0.id }.sorted()
            let kind = planes[members[0]].0.kind
            let ys = members.flatMap { planes[$0].0.vertices.map(\.y) }
            let height = (ys.max() ?? 0) - (ys.min() ?? 0)
            return Candidate(surfaceIDs: ids, kind: kind, tall: kind == "vertical" && height >= tallHeight)
        }
    }

    private static func samePlane(_ left: (SpatialSurface, SIMD3<Float>, Float),
                                  _ right: (SpatialSurface, SIMD3<Float>, Float)) -> Bool {
        guard left.0.epoch == right.0.epoch, left.0.kind == right.0.kind else { return false }
        var rightNormal = right.1
        if simd_dot(left.1, rightNormal) < 0 { rightNormal = -rightNormal }
        guard simd_dot(left.1, rightNormal) >= cos(angleLimit) else { return false }
        let rightOffset = simd_dot(rightNormal, simd(right.0.vertices[0]))
        guard abs(left.2 - rightOffset) <= offsetLimit else { return false }
        var nearest = Float.greatestFiniteMagnitude
        for a in left.0.vertices {
            for b in right.0.vertices {
                nearest = min(nearest, SpatialScanGeometry.distance(a, b))
            }
        }
        return nearest <= gapLimit
    }

    private static func fit(_ vertices: [SpatialPoint]) -> (SIMD3<Float>, Float)? {
        var normal = SIMD3<Float>.zero
        for index in vertices.indices {
            let current = vertices[index]
            let next = vertices[(index + 1) % vertices.count]
            normal.x += (current.y - next.y) * (current.z + next.z)
            normal.y += (current.z - next.z) * (current.x + next.x)
            normal.z += (current.x - next.x) * (current.y + next.y)
        }
        let length = simd_length(normal)
        guard length > 1e-4 else { return nil }
        normal /= length
        return (normal, simd_dot(normal, simd(vertices[0])))
    }

    private static func simd(_ point: SpatialPoint) -> SIMD3<Float> {
        SIMD3(point.x, point.y, point.z)
    }
}
