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

nonisolated enum SpatialObservationGeometry {
    private struct Camera {
        var k: simd_float3x3
        var transform: simd_float4x4
        var width: Float
        var height: Float
    }

    private static func camera(_ frame: SpatialKeyframe) -> Camera? {
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
        return Camera(k: k, transform: transform, width: Float(frame.width), height: Float(frame.height))
    }

    private static func vector(_ point: SpatialPoint) -> SIMD3<Float> { SIMD3(point.x,point.y,point.z) }
    private static func point(_ value: SIMD3<Float>) -> SpatialPoint { SpatialPoint(x:value.x,y:value.y,z:value.z) }
    private static func valid(_ pixel: [Float]) -> Bool {
        pixel.count == 2 && pixel.allSatisfy { $0.isFinite && $0 >= 0 && $0 <= 1 }
    }

    static func bearing(pixel: [Float], frame: SpatialKeyframe) -> (origin: SpatialPoint, direction: SpatialPoint)? {
        guard valid(pixel), let cam = camera(frame) else { return nil }
        let cv = cam.k.inverse * SIMD3(pixel[0]*cam.width,pixel[1]*cam.height,1)
        let world = cam.transform * SIMD4(cv.x,-cv.y,-cv.z,0)
        let direction = simd_normalize(SIMD3(world.x,world.y,world.z))
        guard direction.x.isFinite, direction.y.isFinite, direction.z.isFinite else { return nil }
        let o = cam.transform.columns.3
        return (point(SIMD3(o.x,o.y,o.z)), point(direction))
    }

    private static func error(_ position: SIMD3<Float>, pixel: [Float], camera: Camera) -> Float? {
        let local = camera.transform.inverse * SIMD4(position.x,position.y,position.z,1)
        guard -local.z > 0 else { return nil }
        let p = camera.k * SIMD3(local.x,-local.y,-local.z)
        let residual = SIMD2(p.x/p.z-pixel[0]*camera.width,p.y/p.z-pixel[1]*camera.height)
        let error = simd_length(residual)
        return error.isFinite ? error : nil
    }

    /// Fixed pilot gates, not calibrated reliability or metric-accuracy claims.
    static func triangulate(_ match: SpatialImageMatch, source: SpatialKeyframe, target: SpatialKeyframe) -> SpatialSurfaceSupport? {
        guard source.pose.epoch == target.pose.epoch,
              match.cycleErrorPixels.isFinite, match.cycleErrorPixels >= 0, match.cycleErrorPixels <= 1.5,
              let a = bearing(pixel: match.sourcePixel, frame: source),
              let b = bearing(pixel: match.targetPixel, frame: target),
              let ca = camera(source), let cb = camera(target) else { return nil }
        let o = vector(a.origin), p = vector(b.origin), u = vector(a.direction), v = vector(b.direction)
        let cosine = min(1, max(-1,simd_dot(u,v)))
        let angle = acos(cosine)*180/Float.pi
        let denominator = 1-cosine*cosine
        guard angle >= 1.5, denominator > 0.000001 else { return nil }
        let w = o-p, d = simd_dot(u,w), e = simd_dot(v,w)
        let s = (cosine*e-d)/denominator, t = (e-cosine*d)/denominator
        guard s > 0, t > 0, s <= 20, t <= 20 else { return nil }
        let first = o+s*u, second = p+t*v
        let separation = simd_distance(first,second), position = (first+second)/2
        guard separation.isFinite, separation <= 0.05,
              simd_distance(position,o) <= 20, simd_distance(position,p) <= 20,
              let ea = error(position,pixel:match.sourcePixel,camera:ca),
              let eb = error(position,pixel:match.targetPixel,camera:cb), max(ea,eb) <= 2 else { return nil }
        return SpatialSurfaceSupport(sourcePixel:match.sourcePixel,targetPixel:match.targetPixel,position:point(position),reprojectionErrorPixels:max(ea,eb),parallaxDegrees:angle,raySeparationMeters:separation,cycleErrorPixels:match.cycleErrorPixels)
    }
}
