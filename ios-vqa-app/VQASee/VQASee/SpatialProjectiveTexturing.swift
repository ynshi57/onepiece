import Foundation
import simd
import SceneKit
import UIKit
import Metal

/// Project a saved JPEG onto an observed ARKit plane. This is not a complete
/// room texture atlas, not 3DGS, and not an object cut-out.
nonisolated enum SpatialProjectiveTexturing {
    struct Mapping: Equatable, Sendable {
        var imageFile: String
        var worldToNormalized: [Float] = []
        /// Native-image diagnostics (top-left); not interpolated by the GPU.
        var uvs: [SIMD2<Float>]
        var visibleFraction: Float
        /// 1 = looking straight at the plane; 0 = grazing.
        var meanIncidence: Float
    }

    static func score(_ mapping: Mapping) -> Float {
        mapping.visibleFraction * (0.25 + 0.75 * mapping.meanIncidence)
    }

    struct Candidate: Equatable, Sendable {
        let imageFile: String
        var state: String
    }
    struct Selection { var mapping: Mapping?; var candidates: [Candidate] }
    static func selection(surface: SpatialSurface, keyframes: [SpatialKeyframe]) -> Selection {
        guard surface.observed,surface.vertices.count>=3,surface.vertices.allSatisfy(\.isFinite) else {return Selection(mapping:nil,candidates:keyframes.map {Candidate(imageFile:$0.imageFile,state:"unobserved_or_invalid_surface")})}
        var best: Mapping?, candidates: [Candidate] = []
        for frame in keyframes {
            guard frame.pose.epoch == surface.epoch else { candidates.append(Candidate(imageFile:frame.imageFile,state:"different_epoch"));continue }
            guard let candidate = mapping(surface:surface,keyframe:frame) else {
                let reason: String
                if Camera(frame) == nil { reason="invalid_camera" }
                else if surface.vertices.contains(where: { Camera(frame)?.project(SIMD3($0.x,$0.y,$0.z)) == nil }) { reason="behind_or_invalid_projection" }
                else { reason="insufficient_coverage_or_degenerate_plane" }
                candidates.append(Candidate(imageFile:frame.imageFile,state:reason));continue
            }
            candidates.append(Candidate(imageFile:frame.imageFile,state:"eligible"))
            if best.map({ score(candidate)>score($0) }) ?? true {best=candidate}
        }
        if let best, let index=candidates.firstIndex(where: {$0.imageFile==best.imageFile}) {candidates[index].state="selected"}
        return Selection(mapping:best,candidates:candidates)
    }
    static func mapping(surface: SpatialSurface, keyframes: [SpatialKeyframe]) -> Mapping? { selection(surface:surface,keyframes:keyframes).mapping }

    static func mapping(surface: SpatialSurface, keyframe: SpatialKeyframe) -> Mapping? {
        guard surface.observed,surface.vertices.count>=3,surface.vertices.allSatisfy(\.isFinite) else {return nil}
        guard let camera = Camera(keyframe) else { return nil }
        let points = surface.vertices.map { SIMD3<Float>($0.x, $0.y, $0.z) }
        let uvs = points.compactMap { camera.project($0) }.map { SIMD2($0.x,$0.y) }
        guard let visible = camera.coverage(points), visible>0,
              let incidence = incidence(points:points,cameraPosition:camera.position) else {return nil}
        return Mapping(imageFile: keyframe.imageFile, worldToNormalized: camera.normalizedMatrix, uvs: uvs, visibleFraction: visible, meanIncidence: incidence)
    }

    /// Shared production/test material factory. Texture projection is evaluated
    /// per fragment, before division, so an oblique surface is not warped by
    /// interpolating already-divided vertex UV coordinates.
    @MainActor static func material(mapping: Mapping, image: CGImage, kind: String, liveOverlay: Bool) -> SCNMaterial {
        makeMaterial(mapping:mapping,contents:image,kind:kind,liveOverlay:liveOverlay)
    }
    @MainActor static func material(mapping:Mapping,texture:MTLTexture,kind:String,liveOverlay:Bool) -> SCNMaterial {
        makeMaterial(mapping:mapping,contents:texture,kind:kind,liveOverlay:liveOverlay)
    }
    @MainActor private static func makeMaterial(mapping:Mapping,contents:Any,kind:String,liveOverlay:Bool) -> SCNMaterial {
        let material = SCNMaterial()
        material.isDoubleSided = true; material.lightingModel = .constant
        material.diffuse.contents = contents
        material.transparency = liveOverlay ? 0.28 : 1
        material.blendMode = .alpha
        material.diffuse.wrapS = .clamp; material.diffuse.wrapT = .clamp
        material.diffuse.minificationFilter = .linear; material.diffuse.magnificationFilter = .linear; material.diffuse.mipFilter = .none
        let m = mapping.worldToNormalized
        precondition(m.count == 16)
        let matrix = simd_float4x4(columns:(SIMD4(m[0],m[1],m[2],m[3]), SIMD4(m[4],m[5],m[6],m[7]), SIMD4(m[8],m[9],m[10],m[11]), SIMD4(m[12],m[13],m[14],m[15])))
        material.setValue(NSValue(scnMatrix4:SCNMatrix4(matrix)), forKey:"sourceWorldToNormalized")
        let color = kind == "vertical" ? UIColor.systemPurple : UIColor.systemTeal
        var r:CGFloat=0,g:CGFloat=0,b:CGFloat=0,a:CGFloat=0
        color.getRed(&r,green:&g,blue:&b,alpha:&a)
        material.setValue(NSValue(scnVector4:SCNVector4(Float(r),Float(g),Float(b),kind == "vertical" ? 0.14 : 0.08)),forKey:"unknownRGBA")
        material.shaderModifiers = [.surface: surfaceShader]
        return material
    }

    static let surfaceShader = """
    #pragma arguments
    float4x4 sourceWorldToNormalized;
    float4 unknownRGBA;
    #pragma transparent
    #pragma body
    float4 world = scn_frame.inverseViewTransform * float4(_surface.position, 1.0);
    float4 projected = sourceWorldToNormalized * world;
    float2 uv = projected.xy / max(projected.z, 0.000001);
    if (projected.w > 0.05 && projected.z > 0.0 && uv.x >= 0.0 && uv.x <= 1.0 && uv.y >= 0.0 && uv.y <= 1.0) {
        _surface.diffuse = u_diffuseTexture.sample(u_diffuseTextureSampler, float2(uv.x, uv.y));
    } else {
        _surface.diffuse = unknownRGBA;
    }
    """

    private static func incidence(points: [SIMD3<Float>], cameraPosition: SIMD3<Float>) -> Float? {
        let center = points.reduce(SIMD3<Float>.zero, +) / Float(points.count)
        var normal = SIMD3<Float>.zero
        for index in points.indices {
            normal += cross(points[index] - center, points[(index + 1) % points.count] - center)
        }
        let length = simd_length(normal)
        let view = cameraPosition - center
        let viewLength = simd_length(view)
        guard length > 1e-4, viewLength > 1e-4 else { return nil }
        return abs(simd_dot(normal / length, view / viewLength))
    }

    struct Camera {
        var k: simd_float3x3
        var worldToCamera: simd_float4x4
        var position: SIMD3<Float>
        var width: Float
        var height: Float

        init?(_ keyframe: SpatialKeyframe) {
            guard keyframe.intrinsics.count == 9, keyframe.intrinsics.allSatisfy(\.isFinite),
                  keyframe.cameraToWorld.count == 16, keyframe.cameraToWorld.allSatisfy(\.isFinite),
                  keyframe.width > 0, keyframe.height > 0 else { return nil }
            let values = keyframe.intrinsics
            k = simd_float3x3(columns: (
                SIMD3(values[0], values[1], values[2]),
                SIMD3(values[3], values[4], values[5]),
                SIMD3(values[6], values[7], values[8])
            ))
            guard k.columns.0.x > 0, k.columns.1.y > 0, abs(k.columns.0.z)<1e-6,abs(k.columns.1.z)<1e-6,abs(k.columns.2.z-1)<1e-6 else { return nil }
            let t = keyframe.cameraToWorld
            let cameraToWorld = simd_float4x4(columns: (
                SIMD4(t[0], t[1], t[2], t[3]),
                SIMD4(t[4], t[5], t[6], t[7]),
                SIMD4(t[8], t[9], t[10], t[11]),
                SIMD4(t[12], t[13], t[14], t[15])
            ))
            guard abs(t[3])<1e-6,abs(t[7])<1e-6,abs(t[11])<1e-6,abs(t[15]-1)<1e-6 else {return nil}
            let axes = simd_float4x4(diagonal: SIMD4(1, -1, -1, 1))
            worldToCamera = axes * cameraToWorld.inverse
            guard [worldToCamera.columns.0,worldToCamera.columns.1,worldToCamera.columns.2,worldToCamera.columns.3].allSatisfy({$0.x.isFinite && $0.y.isFinite && $0.z.isFinite && $0.w.isFinite}) else { return nil }
            position = SIMD3(cameraToWorld.columns.3.x, cameraToWorld.columns.3.y, cameraToWorld.columns.3.z)
            width = Float(keyframe.width)
            height = Float(keyframe.height)
        }

        /// Clip the source-camera polygon, not just its corners. A large plane
        /// enclosing the photograph has useful interior support even when all
        /// original vertices lie outside the image.
        func coverage(_ points:[SIMD3<Float>]) -> Float? {
            var local=points.map {worldToCamera * SIMD4($0.x,$0.y,$0.z,1)}
            guard local.allSatisfy({$0.x.isFinite && $0.y.isFinite && $0.z.isFinite}) else {return nil}
            var near:[SIMD4<Float>]=[]
            for index in local.indices {
                let a=local[index],b=local[(index+1)%local.count],insideA=a.z>=0.05,insideB=b.z>=0.05
                if insideA {near.append(a)}
                if insideA != insideB {near.append(a+(b-a)*((0.05-a.z)/(b.z-a.z)))}
            }
            local=near
            guard local.count>=3 else {return nil}
            var polygon=local.map {p -> SIMD2<Float> in
                let q=k*SIMD3(p.x,p.y,p.z)
                return SIMD2(q.x/q.z/width,q.y/q.z/height)
            }
            guard polygon.allSatisfy({$0.x.isFinite && $0.y.isFinite}) else {return nil}
            func area(_ p:[SIMD2<Float>]) -> Float {
                guard p.count>=3 else {return 0}
                return abs(p.indices.reduce(Float(0)) {sum,i in
                    let a=p[i],b=p[(i+1)%p.count];return sum+a.x*b.y-a.y*b.x
                })/2
            }
            let total=area(polygon)
            guard total.isFinite,total>1e-8 else {return nil}
            for (axis,bound,greater) in [(0,Float(0),true),(0,Float(1),false),(1,Float(0),true),(1,Float(1),false)] {
                var clipped:[SIMD2<Float>]=[]
                guard !polygon.isEmpty else {return nil}
                for index in polygon.indices {
                    let a=polygon[index],b=polygon[(index+1)%polygon.count]
                    let ia=greater ? a[axis]>=bound:a[axis]<=bound,ib=greater ? b[axis]>=bound:b[axis]<=bound
                    if ia {clipped.append(a)}
                    if ia != ib {clipped.append(a+(b-a)*((bound-a[axis])/(b[axis]-a[axis])))}
                }
                polygon=clipped
            }
            let visible=area(polygon)
            return visible.isFinite && visible>1e-8 ? min(1,visible/total):nil
        }

        var normalizedMatrix: [Float] {
            // xy / z are normalized native-image coordinates; w is physical
            // source-camera depth, retained separately for behind-camera tests.
            let projection = simd_float4x4(columns: (
                SIMD4(k.columns.0.x/width, k.columns.0.y/height, k.columns.0.z, 0),
                SIMD4(k.columns.1.x/width, k.columns.1.y/height, k.columns.1.z, 0),
                SIMD4(k.columns.2.x/width, k.columns.2.y/height, k.columns.2.z, 1),
                SIMD4<Float>.zero)) * worldToCamera
            return [projection.columns.0,projection.columns.1,projection.columns.2,projection.columns.3].flatMap { [$0.x,$0.y,$0.z,$0.w] }
        }

        struct Projection { var x: Float; var y: Float; var depth: Float }

        /// Where the pixel's ray meets a known floor height. Nil when the hit is behind the camera.
        func floorHit(pixelX: Float, pixelY: Float, floorY: Float) -> SIMD3<Float>? {
            let fx = k.columns.0.x
            let fy = k.columns.1.y
            guard fx > 1e-4, fy > 1e-4 else { return nil }
            let local = SIMD3((pixelX - k.columns.2.x) / fx, (pixelY - k.columns.2.y) / fy, 1)
            let worldDir = worldToCamera.inverse * SIMD4(local.x, local.y, local.z, 0)
            var direction = SIMD3(worldDir.x, worldDir.y, worldDir.z)
            let length = simd_length(direction)
            guard length > 1e-6 else { return nil }
            direction /= length
            guard abs(direction.y) > 1e-6 else { return nil }
            let t = (floorY - position.y) / direction.y
            guard t > 0.05, t < 12 else { return nil }
            let hit = position + direction * t
            guard hit.x.isFinite, hit.y.isFinite, hit.z.isFinite, let seen = project(hit), seen.depth > 0.05 else { return nil }
            return hit
        }

        /// Normalized native JPEG coordinates, origin top-left, matching saved pixels.
        func project(_ world: SIMD3<Float>) -> Projection? {
            let local = worldToCamera * SIMD4(world.x, world.y, world.z, 1)
            guard local.z > 0.05, local.x.isFinite, local.y.isFinite, local.z.isFinite else { return nil }
            let pixel = k * SIMD3(local.x, local.y, local.z)
            let x = pixel.x / pixel.z / width
            let y = pixel.y / pixel.z / height
            guard x.isFinite, y.isFinite else { return nil }
            return Projection(x: x, y: y, depth: local.z)
        }
    }
}
