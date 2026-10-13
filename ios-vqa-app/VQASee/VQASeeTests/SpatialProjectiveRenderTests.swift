import XCTest
import SceneKit
import Metal
import MetalKit
import UIKit
@testable import VQASee

/// Actual SceneKit/Metal pixel regression, not a CPU warp or a room-accuracy test.
@MainActor final class SpatialProjectiveRenderTests: XCTestCase {
    private let size = CGSize(width:256,height:192)
    private func photo() -> UIImage {
        let format = UIGraphicsImageRendererFormat(); format.scale = 1; format.opaque = true
        return UIGraphicsImageRenderer(size:size,format:format).image { context in
            UIColor.red.setFill(); context.fill(CGRect(x:0,y:0,width:128,height:96))
            UIColor.green.setFill(); context.fill(CGRect(x:128,y:0,width:128,height:96))
            UIColor.blue.setFill(); context.fill(CGRect(x:0,y:96,width:128,height:96))
            UIColor.yellow.setFill(); context.fill(CGRect(x:128,y:96,width:128,height:96))
            // An asymmetric ramp and small marker reveal perspective warping.
            for x in 0..<256 {
                UIColor(red:CGFloat(x)/255,green:0.25,blue:0.1,alpha:1).setFill()
                context.fill(CGRect(x:x,y:70,width:1,height:16))
            }
            UIColor.black.setFill(); context.fill(CGRect(x:15,y:12,width:11,height:8))
        }
    }
    private func frame() -> SpatialKeyframe {
        SpatialKeyframe(id:"render",time:1,pose:SpatialPose(time:1,position:SpatialPoint(x:0,y:0,z:0),forward:SpatialPoint(x:0,y:0,z:-1),epoch:1),intrinsics:[200,0,0,0,200,0,128,96,1],width:256,height:192,imageFile:"render.jpg",cameraToWorld:[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])
    }
    private func plane(slope:Float=0, expand:Float=1) -> SpatialSurface {
        let corners:[SIMD2<Float>] = [SIMD2(-0.64,-0.48),SIMD2(0.64,-0.48),SIMD2(0.64,0.48),SIMD2(-0.64,0.48)]
        let vertices=corners.map { p -> SpatialPoint in
            let ray = p*expand, distance = 3/(1-slope*ray.x)
            return SpatialPoint(x:ray.x*distance,y:ray.y*distance,z:-distance)
        }
        return SpatialSurface(id:"plane",kind:"vertical",vertices:vertices,epoch:1)
    }
    private func render(_ surface:SpatialSurface, legacy:Bool=false, wider:Bool=false, sourceFrame:SpatialKeyframe?=nil, gpuTexture:Bool=false, existingTexture:MTLTexture?=nil) throws -> UIImage {
        let device=try XCTUnwrap(MTLCreateSystemDefaultDevice(),"Actual GPU validation requires Metal")
        let renderer=SCNRenderer(device:device,options:nil), scene=SCNScene()
        scene.background.contents=UIColor.white
        let vertices=surface.vertices.map { SCNVector3($0.x,$0.y,$0.z) }
        let source=sourceFrame ?? frame()
        let mapping=try XCTUnwrap(SpatialProjectiveTexturing.mapping(surface:surface,keyframe:source))
        let sources=legacy ? [SCNGeometrySource(vertices:vertices),SCNGeometrySource(textureCoordinates:mapping.uvs.map {CGPoint(x:CGFloat($0.x),y:CGFloat($0.y))})] : [SCNGeometrySource(vertices:vertices)]
        let geometry=SCNGeometry(sources:sources,elements:[SCNGeometryElement(indices:[Int32(0),1,2,0,2,3],primitiveType:.triangles)])
        if legacy {
            let material=SCNMaterial();material.isDoubleSided=true;material.lightingModel = .constant;material.diffuse.contents=photo();geometry.materials=[material]
        } else {
            let material:SCNMaterial
            if let existingTexture {
                material=SpatialProjectiveTexturing.material(mapping:mapping,texture:existingTexture,kind:"vertical",liveOverlay:false)
            } else if gpuTexture {
                let texture=try MTKTextureLoader(device:device).newTexture(cgImage:try XCTUnwrap(photo().cgImage),options:[.SRGB:true,.origin:MTKTextureLoader.Origin.topLeft,.generateMipmaps:false])
                material=SpatialProjectiveTexturing.material(mapping:mapping,texture:texture,kind:"vertical",liveOverlay:false)
            } else {material=SpatialProjectiveTexturing.material(mapping:mapping,image:try XCTUnwrap(photo().cgImage),kind:"vertical",liveOverlay:false)}
            XCTAssertEqual(material.transparency,1)
            geometry.materials=[material]
        }
        scene.rootNode.addChildNode(SCNNode(geometry:geometry))
        let camera=SCNNode();camera.camera=SCNCamera();camera.camera?.zNear=0.01;camera.camera?.zFar=100
        camera.camera?.fieldOfView=Double(2*atan((wider ? 1.8 : 1)*96/200)*180/Double.pi)
        camera.camera?.wantsHDR=false
        let t=source.cameraToWorld
        camera.simdTransform=simd_float4x4(columns:(SIMD4(t[0],t[1],t[2],t[3]),SIMD4(t[4],t[5],t[6],t[7]),SIMD4(t[8],t[9],t[10],t[11]),SIMD4(t[12],t[13],t[14],t[15])))
        scene.rootNode.addChildNode(camera);renderer.scene=scene;renderer.pointOfView=camera
        return renderer.snapshot(atTime:0,with:size,antialiasingMode:.none)
    }
    private func pixels(_ image:UIImage) throws -> [UInt8] {
        let cg=try XCTUnwrap(image.cgImage), width=cg.width,height=cg.height
        var bytes=Array(repeating:UInt8(0),count:width*height*4)
        try bytes.withUnsafeMutableBytes { ptr in
            let ctx=try XCTUnwrap(CGContext(data:ptr.baseAddress,width:width,height:height,bitsPerComponent:8,bytesPerRow:width*4,space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue))
            ctx.draw(cg,in:CGRect(x:0,y:0,width:width,height:height))
        }
        return bytes
    }
    private func rgb(_ bytes:[UInt8],x:Int,y:Int) -> [Int] {let i=(y*256+x)*4;return bytes[i..<i+3].map(Int.init)}
    func testActualMetalFrontalTextureIsVisibleAndNativeVAxisMatchesUIImage() throws {
        let rendered=try render(plane()), native=photo(), out=try pixels(rendered), expected=try pixels(native)
        for (x,y) in [(40,30),(190,30),(40,150),(190,150)] {
            let a=rgb(out,x:x,y:y),b=rgb(expected,x:x,y:y)
            XCTAssertLessThan(a.enumerated().map {abs($0.element-b[$0.offset])}.max() ?? 255,20,"Native texture V-axis or opacity mismatch at \(x),\(y)")
        }
        let attachment=XCTAttachment(image:rendered);attachment.name="actual-metal-frontal";attachment.lifetime = .keepAlways;add(attachment)
    }
    func testActualObliqueGPUProjectionMatchesFrontalReferenceAndExposesLegacyWarp() throws {
        let referenceImage=try render(plane()),reference=try pixels(referenceImage), projectedImage=try render(plane(slope:0.9)), projected=try pixels(projectedImage), legacyImage=try render(plane(slope:0.9),legacy:true),legacy=try pixels(legacyImage)
        func error(_ values:[UInt8]) -> Double {
            var sum=0.0,count=0
            for y in stride(from:14,to:178,by:3) { for x in stride(from:14,to:242,by:3) {
                let a=rgb(values,x:x,y:y),b=rgb(reference,x:x,y:y)
                sum += Double(zip(a,b).reduce(0) {$0+abs($1.0-$1.1)})/(3*255);count += 1
            }}
            return sum/Double(count)
        }
        let actual=error(projected), wrong=error(legacy)
        XCTAssertLessThan(actual,0.025,"Fragment projection must preserve original photo pixels")
        XCTAssertGreaterThan(wrong,actual+0.025,"Oblique fixture must expose vertex-UV warp")
        let metrics=XCTAttachment(string:"actualGPUError="+String(actual)+"; legacyVertexUVError="+String(wrong));metrics.name="actual-metal-oblique-numerical-errors";metrics.lifetime = .keepAlways;add(metrics)
        let referenceAttachment=XCTAttachment(image:referenceImage);referenceAttachment.name="actual-metal-oblique-reference";referenceAttachment.lifetime = .keepAlways;add(referenceAttachment)
        let legacyAttachment=XCTAttachment(image:legacyImage);legacyAttachment.name="actual-metal-legacy-vertex-uv-warp";legacyAttachment.lifetime = .keepAlways;add(legacyAttachment)
        let attachment=XCTAttachment(image:projectedImage);attachment.name="actual-metal-oblique";attachment.lifetime = .keepAlways;add(attachment)
    }
    func testProductionGPUTextureBindingPreservesNativeVAxisAndSlantedProjection() throws {
        let image=try render(plane(slope:0.9),gpuTexture:true),actual=try pixels(image),expected=try pixels(photo())
        for (x,y) in [(40,30),(190,30),(40,150),(190,150)] {
            let a=rgb(actual,x:x,y:y),b=rgb(expected,x:x,y:y)
            XCTAssertLessThan(zip(a,b).map {abs($0-$1)}.max() ?? 255,20,"Explicit shared Metal texture must preserve native image coordinates")
        }
        let attachment=XCTAttachment(image:image);attachment.name="actual-metal-production-texture-binding";attachment.lifetime = .keepAlways;add(attachment)
    }

    func testActualJPEGThroughProductionCacheRendersCorrectlyOnObliqueGPUPlane() async throws {
        let directory=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
        defer {try? FileManager.default.removeItem(at:directory)}
        try XCTUnwrap(photo().jpegData(compressionQuality:0.98)).write(to:directory.appendingPathComponent("render.jpg"))
        let cache=SpatialDecodedTextureCache()
        let scope=cache.beginScope(names:["render.jpg"],directory:directory)
        let outcome:SpatialDecodedTextureCache.Outcome=await withCheckedContinuation { continuation in
            cache.request(name:"render.jpg",directory:directory,scope:scope) { continuation.resume(returning:$0) }
        }
        guard case let .ready(texture,_)=outcome else {return XCTFail("Actual JPEG/ImageIO/Metal upload pipeline must succeed")}
        let image=try render(plane(slope:0.9),existingTexture:texture),actual=try pixels(image),expected=try pixels(photo())
        for (x,y) in [(40,30),(190,30),(40,150),(190,150)] {
            let a=rgb(actual,x:x,y:y),b=rgb(expected,x:x,y:y)
            XCTAssertLessThan(zip(a,b).map {abs($0-$1)}.max() ?? 255,20,"Production JPEG pipeline must preserve V-axis and source coordinates")
        }
        XCTAssertEqual(cache.snapshot()["decodesOnMainThread"],0)
        let attachment=XCTAttachment(image:image);attachment.name="actual-metal-jpeg-cache-pipeline";attachment.lifetime = .keepAlways;add(attachment)
    }

    func testActualGPUProjectionPreservesPhotoUnderRigidSourceAndViewerTransform() throws {
        let c=Float(cos(0.7)),s=Float(sin(0.7))
        let transform=simd_float4x4(columns:(SIMD4(c,0,-s,0),SIMD4(0,1,0,0),SIMD4(s,0,c,0),SIMD4(1.2,-0.4,0.5,1)))
        var surface=plane(slope:0.9)
        surface.vertices=surface.vertices.map {p in let q=transform*SIMD4(p.x,p.y,p.z,1);return SpatialPoint(x:q.x,y:q.y,z:q.z)}
        var source=frame()
        source.cameraToWorld=[transform.columns.0,transform.columns.1,transform.columns.2,transform.columns.3].flatMap {[$0.x,$0.y,$0.z,$0.w]}
        source.pose.position=SpatialPoint(x:1.2,y:-0.4,z:0.5);source.pose.forward=SpatialPoint(x:-s,y:0,z:-c)
        let image=try render(surface,sourceFrame:source),actual=try pixels(image),expected=try pixels(photo())
        for (x,y) in [(40,30),(190,30),(40,150),(190,150)] {
            let a=rgb(actual,x:x,y:y),b=rgb(expected,x:x,y:y)
            XCTAssertLessThan(zip(a,b).map {abs($0-$1)}.max() ?? 255,20,"World/source coordinate or native V-axis mismatch")
        }
        let attachment=XCTAttachment(image:image);attachment.name="actual-metal-rigid-world-transform";attachment.lifetime = .keepAlways;add(attachment)
    }

    func testPhotoExteriorRemainsUnknownRatherThanClampingEdgePixels() throws {
        let image=try render(plane(expand:2),wider:true),bytes=try pixels(image)
        let outside=rgb(bytes,x:16,y:30)
        XCTAssertGreaterThan(outside.min() ?? 0,150,"Outside source image must show faint unknown color over white, not stretched red/green")
        let attachment=XCTAttachment(image:image);attachment.name="actual-metal-unknown-exterior";attachment.lifetime = .keepAlways;add(attachment)
    }
}
