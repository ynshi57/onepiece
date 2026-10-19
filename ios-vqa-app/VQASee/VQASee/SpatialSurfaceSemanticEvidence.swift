import Foundation
import simd

/// The caller must bind this mapping to the actual model's checked configuration.
nonisolated struct SpatialSurfaceSemanticTaxonomy: Codable, Sendable, Equatable {
    var configSHA256: String
    var wallLabelID: Int
    var floorLabelID: Int
    var verified: Bool
}

/// Raster axes match native archived JPEG axes. A smaller raster uses pixel-centre resize.
/// occluded is optional independent geometry evidence, never inferred from another class label.
nonisolated struct SpatialSurfaceSemanticMask: Sendable {
    var frameID: String
    var epoch: Int
    var modelID: String
    var taxonomy: SpatialSurfaceSemanticTaxonomy
    var width: Int
    var height: Int
    var labels: [Int]
    var scores: [Float]
    var occluded: [Bool]? = nil
}

nonisolated struct SpatialSurfaceSemanticRecord: Codable, Sendable, Equatable {
    enum Status: String, Codable, Sendable { case sampled, unavailable, failed }
    var status: Status = .failed
    var elapsedSeconds: Double = 0
    var method: String = "plane-ray-native-mask-batch24x24-nearest-v2"
    var surfaceID: String
    var surfaceVertices: [SpatialPoint]
    var epoch: Int
    var frameID: String
    var frameTime: Double
    var modelID: String
    var configSHA256: String
    var wallSupport = 0
    var floorSupport = 0
    var otherOpposition = 0
    var uncertain = 0
    var occluded = 0
    var outsideBoundary = 0
    var unprojectable = 0
    var totalSamples = 0
    var failure: String? = nil
}

nonisolated enum SpatialSurfaceSemanticEvidence {
    enum Semantic: String, Codable, Sendable { case wall, floor, other, unknown }
    // Fixed conservative evidence gates, not calibrated accuracy guarantees.
    static let minimumScore: Float = 0.6
    static let minimumSamples = 8
    static let minimumAgreement: Float = 0.7

    static func evaluate(surface: SpatialSurface, frame: SpatialKeyframe, mask: SpatialSurfaceSemanticMask) -> SpatialSurfaceSemanticRecord {
        evaluateAll(surfaces:[surface],frame:frame,mask:mask)[0]
    }

    private struct PreparedSurface {
        var origin: SIMD3<Float>
        var normal: SIMD3<Float>
        var axis: Int
        var polygon: [SIMD2<Float>]
        func flat(_ p: SIMD3<Float>) -> SIMD2<Float> { axis == 0 ? SIMD2(p.y,p.z) : axis == 1 ? SIMD2(p.x,p.z) : SIMD2(p.x,p.y) }
        func intersection(origin o: SIMD3<Float>, direction d: SIMD3<Float>) -> Float? {
            let denominator = simd_dot(d,normal)
            guard abs(denominator) > 0.000001 else { return nil }
            let distance = simd_dot(origin-o,normal)/denominator
            guard distance.isFinite, distance > 0 else { return nil }
            let p = flat(o+distance*d)
            var inside = false
            for i in polygon.indices {
                let a = polygon[i], b = polygon[(i+1)%polygon.count]
                if (a.y > p.y) != (b.y > p.y), p.x < (b.x-a.x)*(p.y-a.y)/(b.y-a.y)+a.x { inside.toggle() }
            }
            return inside ? distance : nil
        }
    }

    /// Mask validation, camera calibration and image rays are shared across all observed surfaces.
    /// A nearer existing plane is geometric occlusion evidence, not depth inferred from segmentation.
    static func evaluateAll(surfaces: [SpatialSurface], frame: SpatialKeyframe, mask: SpatialSurfaceSemanticMask) -> [SpatialSurfaceSemanticRecord] {
        let began = ProcessInfo.processInfo.systemUptime
        var records = surfaces.map { SpatialSurfaceSemanticRecord(surfaceID:$0.id,surfaceVertices:$0.vertices,
            epoch:$0.epoch,frameID:frame.id,frameTime:frame.time,modelID:mask.modelID,configSHA256:mask.taxonomy.configSHA256) }
        func failed(_ reason: String) -> [SpatialSurfaceSemanticRecord] {
            records.map { original in var r = original; r.failure = reason; r.status = .failed; r.elapsedSeconds = ProcessInfo.processInfo.systemUptime-began; return r }
        }
        guard mask.epoch == frame.pose.epoch, mask.frameID == frame.id,
              !mask.modelID.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty,
              frame.time.isFinite, frame.width > 0, frame.height > 0 else { return failed("source_mismatch") }
        guard mask.taxonomy.verified, mask.taxonomy.configSHA256.count == 64,
              mask.taxonomy.configSHA256.allSatisfy(\.isHexDigit), mask.taxonomy.wallLabelID >= 0,
              mask.taxonomy.floorLabelID >= 0, mask.taxonomy.wallLabelID != mask.taxonomy.floorLabelID else { return failed("unverified_taxonomy") }
        guard mask.width > 0, mask.height > 0, mask.width <= Int.max/mask.height else { return failed("invalid_mask_size") }
        let count = mask.width*mask.height
        guard mask.labels.count == count, mask.scores.count == count, mask.occluded == nil || mask.occluded?.count == count,
              mask.labels.allSatisfy({$0 >= 0}), mask.scores.allSatisfy({$0.isFinite && (0...1).contains($0)}) else { return failed("invalid_mask") }
        // The shared geometry gate validates finite/invertible K and a rigid camera transform once.
        guard let referenceRay = SpatialObservationGeometry.bearing(pixel:[0.5,0.5],frame:frame) else { return failed("invalid_camera") }
        let k = frame.intrinsics, t = frame.cameraToWorld
        let inverseK = simd_float3x3(columns:(SIMD3(k[0],k[1],k[2]),SIMD3(k[3],k[4],k[5]),SIMD3(k[6],k[7],k[8]))).inverse
        let rotation = simd_float3x3(columns:(SIMD3(t[0],t[1],t[2]),SIMD3(t[4],t[5],t[6]),SIMD3(t[8],t[9],t[10])))
        let cameraOrigin = SIMD3(referenceRay.origin.x,referenceRay.origin.y,referenceRay.origin.z)
        var prepared: [PreparedSurface?] = []
        for (index,surface) in surfaces.enumerated() {
            var failure: String?
            if !surface.observed || surface.epoch != frame.pose.epoch { failure = "source_mismatch" }
            else if surface.vertices.count < 3 || !surface.vertices.allSatisfy(\.isFinite) { failure = "invalid_surface" }
            if let failure { records[index].failure = failure; prepared.append(nil); continue }
            let vertices = surface.vertices.map {SIMD3($0.x,$0.y,$0.z)}, origin = SIMD3(surface.vertices[0].x,surface.vertices[0].y,surface.vertices[0].z)
            var normal = SIMD3<Float>.zero
            for i in vertices.indices { normal += simd_cross(vertices[i]-origin,vertices[(i+1)%vertices.count]-origin) }
            guard simd_length(normal) > 0.000001 else { records[index].failure = "degenerate_surface"; prepared.append(nil); continue }
            normal = simd_normalize(normal)
            guard vertices.allSatisfy({abs(simd_dot($0-origin,normal)) <= 0.03}) else { records[index].failure = "nonplanar_surface"; prepared.append(nil); continue }
            let axis = abs(normal.x) > abs(normal.y) ? (abs(normal.x) > abs(normal.z) ? 0 : 2) : (abs(normal.y) > abs(normal.z) ? 1 : 2)
            func flat(_ p: SIMD3<Float>) -> SIMD2<Float> {axis == 0 ? SIMD2(p.y,p.z) : axis == 1 ? SIMD2(p.x,p.z) : SIMD2(p.x,p.y)}
            prepared.append(PreparedSurface(origin:origin,normal:normal,axis:axis,polygon:vertices.map(flat)))
        }
        let columns = min(24,mask.width), rows = min(24,mask.height)
        for row in 0..<rows { for column in 0..<columns {
            let mx = min(mask.width-1,Int((Double(column)+0.5)*Double(mask.width)/Double(columns)))
            let my = min(mask.height-1,Int((Double(row)+0.5)*Double(mask.height)/Double(rows)))
            let nx = (Float(mx)+0.5)*Float(frame.width)/Float(mask.width)-0.5
            let ny = (Float(my)+0.5)*Float(frame.height)/Float(mask.height)-0.5
            let cv = inverseK*SIMD3(nx,ny,1)
            let direction = simd_normalize(rotation*SIMD3(cv.x,-cv.y,-cv.z))
            let distances = prepared.map {$0?.intersection(origin:cameraOrigin,direction:direction)}
            let nearest = distances.compactMap {$0}.min()
            for index in surfaces.indices where prepared[index] != nil {
                records[index].totalSamples += 1
                guard direction.x.isFinite, direction.y.isFinite, direction.z.isFinite else {records[index].unprojectable += 1; continue}
                guard let distance = distances[index] else {
                    // No visible polygon hit, including behind-camera/parallel rays.
                    let denominator = simd_dot(direction,prepared[index]!.normal)
                    let depth = abs(denominator) > 0.000001 ? simd_dot(prepared[index]!.origin-cameraOrigin,prepared[index]!.normal)/denominator : -1
                    if depth.isFinite && depth > 0 {records[index].outsideBoundary += 1} else {records[index].unprojectable += 1}
                    continue
                }
                let pixel = my*mask.width+mx
                let behindKnownPlane = nearest.map {distance-$0 > max(0.03,$0*0.01)} ?? false
                if behindKnownPlane || mask.occluded?[pixel] == true {records[index].occluded += 1}
                else if mask.scores[pixel] < minimumScore {records[index].uncertain += 1}
                else if mask.labels[pixel] == mask.taxonomy.wallLabelID {records[index].wallSupport += 1}
                else if mask.labels[pixel] == mask.taxonomy.floorLabelID {records[index].floorSupport += 1}
                else {records[index].otherOpposition += 1}
            }
        }}
        for i in records.indices {
            guard records[i].failure == nil else {records[i].elapsedSeconds = ProcessInfo.processInfo.systemUptime-began; continue}
            records[i].status = records[i].wallSupport+records[i].floorSupport+records[i].otherOpposition+records[i].uncertain+records[i].occluded > 0 ? .sampled : .unavailable
            // Batch elapsed is shared, not additive across surfaces.
            records[i].elapsedSeconds = ProcessInfo.processInfo.systemUptime-began
        }
        return records
    }

    static func vote(_ record: SpatialSurfaceSemanticRecord) -> Semantic {
        guard record.failure == nil, record.status == .sampled else { return .unknown }
        let count = record.wallSupport+record.floorSupport+record.otherOpposition+record.uncertain+record.occluded
        guard count >= minimumSamples else { return .unknown }
        if Float(record.wallSupport)/Float(count) >= minimumAgreement { return .wall }
        if Float(record.floorSupport)/Float(count) >= minimumAgreement { return .floor }
        if Float(record.otherOpposition)/Float(count) >= minimumAgreement { return .other }
        return .unknown
    }

    /// Distinct source frame IDs, same unchanged geometry/model/taxonomy/epoch.
    /// Distinct IDs alone do not establish independent camera viewpoints.
    /// Any contradictory confident frame leaves semantics unknown. No geometry is removed.
    static func resolve(surface: SpatialSurface, records: [SpatialSurfaceSemanticRecord]) -> Semantic {
        let valid = records.filter { $0.surfaceID == surface.id && $0.epoch == surface.epoch && $0.surfaceVertices == surface.vertices && $0.failure == nil }
        guard let first = valid.first else { return .unknown }
        guard valid.allSatisfy({ $0.modelID == first.modelID && $0.configSHA256 == first.configSHA256 && $0.method == first.method }) else { return .unknown }
        var byFrame: [String: Semantic] = [:]
        for record in valid {
            let value = vote(record)
            if let old = byFrame[record.frameID], old != value { return .unknown }
            byFrame[record.frameID] = value
        }
        let decisive = byFrame.values.filter { $0 != .unknown }
        guard decisive.count >= 2, let candidate = decisive.first, decisive.allSatisfy({ $0 == candidate }) else { return .unknown }
        if candidate == .wall && surface.kind != "vertical" { return .unknown }
        if candidate == .floor && surface.kind != "horizontal" { return .unknown }
        return candidate
    }
}
