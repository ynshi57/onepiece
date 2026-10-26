import Foundation
import simd

/// iPhone 17 has no LiDAR. Wall–floor pixels in two posed RGB views are triangulated
/// with the existing 1.5° / 5 cm / 2 px gates. Depth Anything affine and sceneDepth stay off.
nonisolated enum SpatialStructureSemanticStereo {
    static let stepMeters = SpatialPlaneGrouping.offsetLimit
    static let minimumBaseline: Float = 0.25
    static let maximumBaseline: Float = 2.5
    static let minimumHeight: Float = 0.15
    static let maximumHeight: Float = 2.5
    static let sampledViewLimit = 12
    static let maximumPoints = 120
    static let searchRadius = 2

    struct Result: Sendable, Equatable {
        var segments: [SpatialStructureMap.WallSegment]
        var gaps: [SpatialStructureMap.Gap]
        var tracks: [SpatialPlaneTrackAssociation.Track]
        var rejections: [String: Int]
        var pointCount: Int
    }

    static func publish(
        frames: [SpatialKeyframe],
        views: [SpatialStructureWallLineSupport.View],
        floor: SpatialPlaneTrackAssociation.Track,
        existing: [SpatialStructureMap.WallSegment],
        startIndex: Int
    ) -> Result {
        var rejections: [String: Int] = [:]
        let points = wallPoints(frames: frames, views: views, floor: floor)
        guard points.count >= 8 else {
            rejections["stereo:insufficientWallPoints"] = 1
            return .init(segments: [], gaps: [], tracks: [], rejections: rejections, pointCount: points.count)
        }
        let planes = ransacPlanes(points, floor: floor)
        if planes.isEmpty {
            rejections["stereo:noVerticalPlane"] = 1
            return .init(segments: [], gaps: [], tracks: [], rejections: rejections, pointCount: points.count)
        }
        var segments: [SpatialStructureMap.WallSegment] = []
        var tracks: [SpatialPlaneTrackAssociation.Track] = []
        let up = floor.normal.y >= 0 ? floor.normal : -floor.normal
        for plane in planes {
            let direction = simd_normalize(simd_cross(up, plane.normal))
            guard direction.x.isFinite else { continue }
            let origin = plane.anchor - simd_dot(plane.anchor - floor.anchor, floor.normal) * floor.normal
            let ts = plane.inliers.map { simd_dot($0 - origin, direction) }.sorted()
            guard let lo = ts.first, let hi = ts.last, hi - lo > stepMeters else {
                rejections["stereo:shortSpan", default: 0] += 1
                continue
            }
            let start = origin + direction * lo
            let end = origin + direction * hi
            if explained(start, existing) && explained(end, existing) {
                rejections["stereo:explainedByExisting", default: 0] += 1
                continue
            }
            let trackID = "stereo-\(tracks.count)"
            tracks.append(.init(id: trackID, kind: "vertical", normal: plane.normal, anchor: origin, memberSurfaceIDs: [], epoch: floor.epoch))
            segments.append(.init(
                id: "wall-p0-\(startIndex + tracks.count - 1)-0",
                verticalTrackID: trackID,
                start: point(start), end: point(end),
                startProvenance: .candidate, endProvenance: .candidate,
                lengthMeters: hi - lo, sourceSurfaceIDs: [], frameIDs: []
            ))
        }
        return .init(segments: segments, gaps: [], tracks: tracks, rejections: rejections, pointCount: points.count)
    }

    static func wallPoints(
        frames: [SpatialKeyframe],
        views: [SpatialStructureWallLineSupport.View],
        floor: SpatialPlaneTrackAssociation.Track
    ) -> [SIMD3<Float>] {
        let byID = Dictionary(uniqueKeysWithValues: frames.map { ($0.id, $0) })
        let allOrigins: [(String, SIMD3<Float>)] = views.compactMap { view in
            guard let frame = byID[view.frameID], let cam = SpatialObservationGeometry.prepareCamera(frame: frame) else { return nil }
            let o = cam.transform.columns.3
            return (view.frameID, SIMD3(o.x, o.y, o.z))
        }
        let sampled = sample(views, origins: allOrigins)
        let origins = allOrigins.filter { origin in sampled.contains { $0.frameID == origin.0 } }
        let masks = Dictionary(uniqueKeysWithValues: sampled.map {
            ($0.frameID, dilated(boundaryMask($0), width: $0.width, height: $0.height))
        })
        var points: [SIMD3<Float>] = []
        for view in sampled {
            guard points.count < maximumPoints,
                  let source = byID[view.frameID],
                  let origin = origins.first(where: { $0.0 == view.frameID })?.1,
                  let mask = masks[view.frameID] else { continue }
            let partners = sampled.filter { other in
                guard other.frameID != view.frameID,
                      let target = origins.first(where: { $0.0 == other.frameID }) else { return false }
                let d = simd_distance(target.1, origin)
                return d >= minimumBaseline && d <= maximumBaseline
            }.prefix(2)
            guard !partners.isEmpty else { continue }
            let sources = mask.indices.filter { mask[$0] }
            let pixelStep = max(1, sources.count / 40)
            for k in Swift.stride(from: 0, to: sources.count, by: pixelStep) {
                guard points.count < maximumPoints else { break }
                let index = sources[k]
                let x = index % view.width
                let y = index / view.width
                guard x >= 4, y >= 4, x < view.width - 4, y < view.height - 4 else { continue }
                let sourcePixel: [Float] = [(Float(x) + 0.5) / Float(view.width), (Float(y) + 0.5) / Float(view.height)]
                guard let ray = SpatialObservationGeometry.bearing(pixel: sourcePixel, frame: source) else { continue }
                let o = SIMD3(ray.origin.x, ray.origin.y, ray.origin.z)
                let d = SIMD3(ray.direction.x, ray.direction.y, ray.direction.z)
                for other in partners {
                    guard let target = byID[other.frameID], let otherMask = masks[other.frameID] else { continue }
                    var hits: [(t: Float, pixel: [Float])] = []
                    var t: Float = 0.4
                    while t <= 6 {
                        let p = o + d * t
                        let height = simd_dot(p - floor.anchor, floor.normal.y >= 0 ? floor.normal : -floor.normal)
                        if height >= minimumHeight, height <= maximumHeight,
                           let pixel = boundaryPixel(p, view: other, mask: otherMask) {
                            hits.append((t, pixel))
                        }
                        t += stepMeters / 2
                    }
                    guard hits.count >= 1 else { continue }
                    let hit = hits[hits.count / 2]
                    let match = SpatialImageMatch(sourcePixel: sourcePixel, targetPixel: hit.pixel, cycleErrorPixels: 0)
                    if let support = SpatialObservationGeometry.triangulate(match, source: source, target: target) {
                        points.append(SIMD3(support.position.x, support.position.y, support.position.z))
                        break
                    }
                }
            }
        }
        return points.sorted { lhs, rhs in
            if lhs.x != rhs.x { return lhs.x < rhs.x }
            if lhs.z != rhs.z { return lhs.z < rhs.z }
            return lhs.y < rhs.y
        }
    }

    static func ransacPlanes(_ points: [SIMD3<Float>], floor: SpatialPlaneTrackAssociation.Track, maxPlanes: Int = 6) -> [Plane] {
        let up = floor.normal.y >= 0 ? floor.normal : -floor.normal
        var remaining = points
        var planes: [Plane] = []
        for _ in 0..<maxPlanes {
            guard remaining.count >= 8 else { break }
            var best: Plane?
            let strideN = max(1, remaining.count / 12)
            for i in Swift.stride(from: 0, to: remaining.count - 1, by: strideN) {
                for j in Swift.stride(from: i + strideN, to: remaining.count, by: strideN) {
                    let v = remaining[j] - remaining[i]
                    var tangent = v - simd_dot(v, up) * up
                    guard simd_length(tangent) > 0.3 else { continue }
                    tangent = simd_normalize(tangent)
                    var normal = simd_normalize(simd_cross(up, tangent))
                    guard normal.x.isFinite else { continue }
                    if simd_dot(normal, remaining[i] - floor.anchor) < 0 { normal = -normal }
                    let inliers = remaining.filter { abs(simd_dot(normal, $0 - remaining[i])) <= SpatialPlaneGrouping.offsetLimit }
                    if inliers.count >= 8, inliers.count > (best?.inliers.count ?? 0) {
                        let anchor = inliers.reduce(SIMD3<Float>.zero, +) / Float(inliers.count)
                        best = Plane(normal: normal, anchor: anchor, inliers: inliers)
                    }
                }
            }
            guard let plane = best else { break }
            planes.append(plane)
            remaining.removeAll { point in plane.inliers.contains { simd_distance($0, point) < 0.001 } }
        }
        return planes
    }

    struct Plane: Sendable {
        var normal: SIMD3<Float>
        var anchor: SIMD3<Float>
        var inliers: [SIMD3<Float>]
    }

    static func sample(_ views: [SpatialStructureWallLineSupport.View], origins: [(String, SIMD3<Float>)] = []) -> [SpatialStructureWallLineSupport.View] {
        guard views.count > sampledViewLimit else { return views }
        let byID = Dictionary(uniqueKeysWithValues: origins)
        guard !byID.isEmpty else {
            let step = max(1, views.count / sampledViewLimit)
            return Swift.stride(from: 0, to: views.count, by: step).prefix(sampledViewLimit).map { views[$0] }
        }
        var picked: [SpatialStructureWallLineSupport.View] = [views[0]]
        while picked.count < sampledViewLimit {
            var best: (Int, Float) = (0, -1)
            for (index, view) in views.enumerated() {
                guard let origin = byID[view.frameID], !picked.contains(where: { $0.frameID == view.frameID }) else { continue }
                let nearest = picked.compactMap { byID[$0.frameID].map { simd_distance($0, origin) } }.min() ?? 0
                if nearest > best.1 { best = (index, nearest) }
            }
            if best.1 < minimumBaseline { break }
            picked.append(views[best.0])
        }
        return picked
    }

    static func boundaryMask(_ view: SpatialStructureWallLineSupport.View) -> [Bool] {
        var mask = Array(repeating: false, count: view.width * view.height)
        func wall(_ x: Int, _ y: Int) -> Bool {
            let index = y * view.width + x
            guard index < view.labels.count, index < view.confidence.count else { return false }
            if Float(view.confidence[index]) / 255 < SpatialSurfaceSemanticEvidence.minimumScore { return false }
            return view.labels[index] == view.wallLabel
        }
        for y in 1..<(view.height - 1) {
            for x in 1..<(view.width - 1) {
                guard wall(x, y) else { continue }
                let adjacent = [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)].contains { nx, ny in
                    guard nx >= 0, ny >= 0, nx < view.width, ny < view.height else { return false }
                    let ni = ny * view.width + nx
                    guard ni < view.labels.count, ni < view.confidence.count else { return false }
                    if Float(view.confidence[ni]) / 255 < SpatialSurfaceSemanticEvidence.minimumScore { return false }
                    return view.labels[ni] != view.wallLabel
                }
                if adjacent { mask[y * view.width + x] = true }
            }
        }
        return mask
    }

    static func dilated(_ mask: [Bool], width: Int? = nil, height: Int? = nil) -> [Bool] {
        guard let width, let height, mask.count == width * height else {
            return mask
        }
        var out = mask
        for y in 1..<(height - 1) {
            for x in 1..<(width - 1) {
                if mask[y * width + x] {
                    for dy in -1...1 {
                        for dx in -1...1 { out[(y + dy) * width + (x + dx)] = true }
                    }
                }
            }
        }
        return out
    }

    private static func boundaryPixel(_ point: SIMD3<Float>, view: SpatialStructureWallLineSupport.View, mask: [Bool]) -> [Float]? {
        guard let projection = SpatialObservationGeometry.project(
            position: SpatialPoint(x: point.x, y: point.y, z: point.z), camera: view.camera
        ), projection.pixel.count == 2, projection.depth > 0, projection.depth <= SpatialObservationGeometry.maximumDepthMeters,
           projection.pixel.allSatisfy({ $0.isFinite && $0 >= 0 && $0 <= 1 }) else { return nil }
        let cx = Int(projection.pixel[0] * Float(view.width))
        let cy = Int(projection.pixel[1] * Float(view.height))
        for dy in -searchRadius...searchRadius {
            for dx in -searchRadius...searchRadius {
                let x = cx + dx, y = cy + dy
                guard x >= 0, y >= 0, x < view.width, y < view.height else { continue }
                let index = y * view.width + x
                if index < mask.count, mask[index] { return projection.pixel }
            }
        }
        return nil
    }

    private static func explained(_ point: SIMD3<Float>, _ existing: [SpatialStructureMap.WallSegment]) -> Bool {
        existing.contains { segment in
            let start = SIMD3(segment.start.x, segment.start.y, segment.start.z)
            let d = SIMD3(segment.end.x, segment.end.y, segment.end.z) - start
            let denom = simd_dot(d, d)
            let t = denom > 0 ? max(0, min(1, simd_dot(point - start, d) / denom)) : 0
            return simd_distance(point, start + t * d) <= SpatialPlaneGrouping.offsetLimit
        }
    }

    private static func point(_ p: SIMD3<Float>) -> SpatialPoint { SpatialPoint(x: p.x, y: p.y, z: p.z) }
}
