import Foundation
import simd

nonisolated struct SpatialImageMatch: Codable, Sendable, Equatable {
    var sourcePixel: [Float]
    var targetPixel: [Float]
    var cycleErrorPixels: Float
}

/// Experimental visible-surface evidence; never an object centre or accuracy guarantee.
nonisolated struct SpatialSurfaceSupport: Codable, Sendable, Equatable {
    var sourcePixel: [Float]
    var targetPixel: [Float]
    var position: SpatialPoint
    var reprojectionErrorPixels: Float
    var parallaxDegrees: Float
    var raySeparationMeters: Float
    var cycleErrorPixels: Float
}

nonisolated enum SpatialTriangulationFailure: String, Codable, Sendable {
    case sourceEpoch, invalidCycle, invalidCamera, lowParallax, negativeDepth, range, raySeparation, projectionBehind, reprojection
}
nonisolated struct SpatialTriangulationDiagnostic: Sendable {
    var support: SpatialSurfaceSupport?
    var failure: SpatialTriangulationFailure?
}

nonisolated enum SpatialObservationGeometry {
    static let maximumDepthMeters: Float = 20
    struct PreparedCamera: Sendable {
        let k: simd_float3x3
        let inverseK: simd_float3x3
        let inverseTransform: simd_float4x4
        let transform: simd_float4x4
        let width: Float
        let height: Float
    }

    static func prepareCamera(frame: SpatialKeyframe) -> PreparedCamera? {
        guard frame.width > 0, frame.height > 0, frame.intrinsics.count == 9,
              frame.cameraToWorld.count == 16,
              frame.intrinsics.allSatisfy(\.isFinite), frame.cameraToWorld.allSatisfy(\.isFinite),
              frame.pose.epoch >= 0 else { return nil }
        let a = frame.intrinsics, t = frame.cameraToWorld
        let k = simd_float3x3(columns: (SIMD3(a[0],a[1],a[2]), SIMD3(a[3],a[4],a[5]), SIMD3(a[6],a[7],a[8])))
        guard k[0,0] > 0, k[1,1] > 0, abs(k[0,2]) < 0.00001,
              abs(k[1,2]) < 0.00001, abs(k[2,2]-1) < 0.00001,
              abs(simd_determinant(k)) > 0.000001 else { return nil }
        let transform = simd_float4x4(columns: (SIMD4(t[0],t[1],t[2],t[3]), SIMD4(t[4],t[5],t[6],t[7]), SIMD4(t[8],t[9],t[10],t[11]), SIMD4(t[12],t[13],t[14],t[15])))
        let rotation = simd_float3x3(columns: (SIMD3(t[0],t[1],t[2]), SIMD3(t[4],t[5],t[6]), SIMD3(t[8],t[9],t[10])))
        guard abs(t[3]) < 0.00001, abs(t[7]) < 0.00001, abs(t[11]) < 0.00001,
              abs(t[15]-1) < 0.00001, abs(simd_determinant(rotation)-1) < 0.0001 else { return nil }
        let unit = rotation.transpose * rotation
        for i in 0..<3 { for j in 0..<3 {
            guard abs(unit[i,j] - (i == j ? 1 : 0)) < 0.0001 else { return nil }
        }}
        return PreparedCamera(k:k,inverseK:k.inverse,inverseTransform:transform.inverse,transform:transform, width: Float(frame.width), height: Float(frame.height))
    }

    private static func vector(_ point: SpatialPoint) -> SIMD3<Float> { SIMD3(point.x,point.y,point.z) }
    private static func point(_ value: SIMD3<Float>) -> SpatialPoint { SpatialPoint(x:value.x,y:value.y,z:value.z) }
    private static func valid(_ pixel: [Float]) -> Bool {
        pixel.count == 2 && pixel.allSatisfy { $0.isFinite && $0 >= 0 && $0 <= 1 }
    }

    static func bearing(pixel: [Float], frame: SpatialKeyframe) -> (origin: SpatialPoint, direction: SpatialPoint)? {
        guard let cam = prepareCamera(frame:frame) else { return nil }
        return bearing(pixel:pixel,camera:cam)
    }

    static func bearing(pixel:[Float],camera cam:PreparedCamera) -> (origin:SpatialPoint,direction:SpatialPoint)? {
        guard valid(pixel) else { return nil }
        let cv = cam.inverseK * SIMD3(pixel[0]*cam.width,pixel[1]*cam.height,1)
        let world = cam.transform * SIMD4(cv.x,-cv.y,-cv.z,0)
        let direction = simd_normalize(SIMD3(world.x,world.y,world.z))
        guard direction.x.isFinite, direction.y.isFinite, direction.z.isFinite else { return nil }
        let o = cam.transform.columns.3
        return (point(SIMD3(o.x,o.y,o.z)), point(direction))
    }

    /// Maps source-native pixel-index rays to target-native rays using rotation only.
    /// Translational parallax is deliberately left for correspondence and triangulation.
    static func rotationHomography(source: SpatialKeyframe, target: SpatialKeyframe) -> simd_float3x3? {
        guard source.pose.epoch == target.pose.epoch, let a = prepareCamera(frame:source), let b = prepareCamera(frame:target) else { return nil }
        func rotation(_ t: simd_float4x4) -> simd_float3x3 {
            simd_float3x3(columns:(SIMD3(t[0].x,t[0].y,t[0].z),SIMD3(t[1].x,t[1].y,t[1].z),SIMD3(t[2].x,t[2].y,t[2].z)))
        }
        let c = simd_float3x3(diagonal:SIMD3<Float>(1,-1,-1))
        return b.k * c * rotation(b.transform).transpose * rotation(a.transform) * c * a.k.inverse
    }

    static func resizePixelTransform(nativeWidth: Int, nativeHeight: Int, width: Int, height: Int) -> simd_float3x3? {
        guard nativeWidth > 0, nativeHeight > 0, width > 0, height > 0 else { return nil }
        let x = Float(width)/Float(nativeWidth), y = Float(height)/Float(nativeHeight)
        return simd_float3x3(columns:(SIMD3(x,0,0),SIMD3(0,y,0),SIMD3((x-1)/2,(y-1)/2,1)))
    }

    static func mapPixel(_ pixel: SIMD2<Float>, homography: simd_float3x3) -> SIMD2<Float>? {
        let q = homography * SIMD3(pixel.x,pixel.y,1)
        guard q.x.isFinite, q.y.isFinite, q.z.isFinite, q.z > 0.000001 else { return nil }
        return SIMD2(q.x/q.z,q.y/q.z)
    }

    static func project(position: SpatialPoint, frame: SpatialKeyframe) -> (pixel: [Float], depth: Float)? {
        guard let cam = prepareCamera(frame:frame) else { return nil }
        return project(position:position,camera:cam)
    }

    /// Inverse of `project` for one normalized pixel. `depthMeters` is camera-forward depth, the same value `project` returns.
    static func position(depthMeters: Float, pixel: [Float], frame: SpatialKeyframe) -> SpatialPoint? {
        guard depthMeters.isFinite, depthMeters > 0, depthMeters <= maximumDepthMeters,
              let cam = prepareCamera(frame:frame), valid(pixel) else { return nil }
        let cv = cam.inverseK * SIMD3(pixel[0]*cam.width, pixel[1]*cam.height, 1)
        guard cv.z.isFinite, cv.z > 0 else { return nil }
        let scale = depthMeters / cv.z
        let world = cam.transform * SIMD4(cv.x*scale, -cv.y*scale, -cv.z*scale, 1)
        let position = point(SIMD3(world.x,world.y,world.z))
        guard position.isFinite else { return nil }
        return position
    }

    static func project(position:SpatialPoint,camera cam:PreparedCamera) -> (pixel:[Float],depth:Float)? {
        guard position.isFinite else { return nil }
        let local = cam.inverseTransform * SIMD4(position.x,position.y,position.z,1)
        guard -local.z > 0 else { return nil }
        let p = cam.k * SIMD3(local.x,-local.y,-local.z)
        let pixel = [p.x/p.z/cam.width,p.y/p.z/cam.height]
        guard pixel.allSatisfy(\.isFinite) else { return nil }
        return (pixel,-local.z)
    }

    private static func error(_ position: SIMD3<Float>, pixel: [Float], camera: PreparedCamera) -> Float? {
        let local = camera.inverseTransform * SIMD4(position.x,position.y,position.z,1)
        guard -local.z > 0 else { return nil }
        let p = camera.k * SIMD3(local.x,-local.y,-local.z)
        let residual = SIMD2(p.x/p.z-pixel[0]*camera.width,p.y/p.z-pixel[1]*camera.height)
        let error = simd_length(residual)
        return error.isFinite ? error : nil
    }

    /// Fixed pilot gates, not calibrated reliability or metric-accuracy claims.
    static func triangulate(_ match: SpatialImageMatch, source: SpatialKeyframe, target: SpatialKeyframe) -> SpatialSurfaceSupport? {
        diagnoseTriangulation(match,source:source,target:target).support
    }

    static func diagnoseTriangulation(_ match: SpatialImageMatch, source: SpatialKeyframe, target: SpatialKeyframe) -> SpatialTriangulationDiagnostic {
        func reject(_ reason: SpatialTriangulationFailure) -> SpatialTriangulationDiagnostic { .init(support:nil,failure:reason) }
        guard source.pose.epoch == target.pose.epoch else { return reject(.sourceEpoch) }
        guard match.cycleErrorPixels.isFinite, match.cycleErrorPixels >= 0, match.cycleErrorPixels <= 1.5 else { return reject(.invalidCycle) }
        guard let a = bearing(pixel: match.sourcePixel, frame: source),
              let b = bearing(pixel: match.targetPixel, frame: target),
              let ca = prepareCamera(frame:source), let cb = prepareCamera(frame:target) else { return reject(.invalidCamera) }
        let o = vector(a.origin), p = vector(b.origin), u = vector(a.direction), v = vector(b.direction)
        let cosine = min(1, max(-1,simd_dot(u,v)))
        let angle = acos(cosine)*180/Float.pi
        let denominator = 1-cosine*cosine
        guard angle >= 1.5, denominator > 0.000001 else { return reject(.lowParallax) }
        let w = o-p, d = simd_dot(u,w), e = simd_dot(v,w)
        let s = (cosine*e-d)/denominator, t = (e-cosine*d)/denominator
        guard s > 0, t > 0 else { return reject(.negativeDepth) }
        guard s <= maximumDepthMeters, t <= maximumDepthMeters else { return reject(.range) }
        let first = o+s*u, second = p+t*v
        let separation = simd_distance(first,second), position = (first+second)/2
        guard separation.isFinite, separation <= 0.05 else { return reject(.raySeparation) }
        guard simd_distance(position,o) <= maximumDepthMeters, simd_distance(position,p) <= maximumDepthMeters else { return reject(.range) }
        guard let ea = error(position,pixel:match.sourcePixel,camera:ca),
              let eb = error(position,pixel:match.targetPixel,camera:cb) else { return reject(.projectionBehind) }
        guard max(ea,eb) <= 2 else { return reject(.reprojection) }
        return SpatialTriangulationDiagnostic(support:SpatialSurfaceSupport(sourcePixel:match.sourcePixel,targetPixel:match.targetPixel,position:point(position),reprojectionErrorPixels:max(ea,eb),parallaxDegrees:angle,raySeparationMeters:separation,cycleErrorPixels:match.cycleErrorPixels),failure:nil)
    }
}
