#if DEBUG
import XCTest
import UIKit
@testable import VQASee

/// Synthetic manual mask: validates rendering coordinates/colors, not segmentation accuracy.
@MainActor
final class SpatialIndoorSemanticVerificationRendererTests: XCTestCase {
    func testNativeAsymmetricOverlayKeepsBackgroundAndSourceBytes() throws {
        let fixture = try makeFixture(width: 320,height: 240)
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let output = fixture.directory.appendingPathComponent("overlay.jpg")
        try SpatialIndoorSemanticVerificationRenderer.render(frame: fixture.frame,row: fixture.row,directory: fixture.directory,outputURL: output)
        let image = try XCTUnwrap(UIImage(data: Data(contentsOf: output))?.cgImage)
        XCTAssertEqual(image.width,320); XCTAssertEqual(image.height,240)
        let wall = try pixel(image,x: 40,y: 30), floor = try pixel(image,x: 280,y: 210), unchanged = try pixel(image,x: 270,y: 30)
        XCTAssertGreaterThan(wall[0],wall[1]+15); XCTAssertGreaterThan(wall[2],wall[1]+20)
        XCTAssertGreaterThan(floor[1],floor[0]+30); XCTAssertGreaterThan(floor[2],floor[0]+30)
        XCTAssertEqual(unchanged[0],40,accuracy: 10)
        XCTAssertEqual(unchanged[1],40,accuracy: 10)
        XCTAssertEqual(try Data(contentsOf: fixture.directory.appendingPathComponent(fixture.frame.imageFile)),fixture.jpeg)
    }

    func testLongEdgeIsBoundedAndSourceDimensionMismatchIsRejected() throws {
        let fixture = try makeFixture(width: 2000,height: 1000)
        defer { try? FileManager.default.removeItem(at: fixture.directory) }
        let output = fixture.directory.appendingPathComponent("overlay.jpg")
        try SpatialIndoorSemanticVerificationRenderer.render(frame: fixture.frame,row: fixture.row,directory: fixture.directory,outputURL: output)
        let image = try XCTUnwrap(UIImage(data: Data(contentsOf: output))?.cgImage)
        XCTAssertEqual(image.width,1280); XCTAssertEqual(image.height,640)
        var wrong = fixture.frame; wrong.width += 1
        XCTAssertThrowsError(try SpatialIndoorSemanticVerificationRenderer.render(frame: wrong,row: fixture.row,directory: fixture.directory,outputURL: output))
        XCTAssertThrowsError(try SpatialIndoorSemanticVerificationRenderer.render(frame: fixture.frame,row: fixture.row,directory: fixture.directory,
            outputURL: fixture.directory.appendingPathComponent(fixture.frame.imageFile)))
    }

    private func makeFixture(width: Int,height: Int) throws -> (directory: URL,frame: SpatialKeyframe,row: SpatialIndoorSemanticObservation,jpeg: Data) {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory,withIntermediateDirectories: true)
        let format = UIGraphicsImageRendererFormat(); format.scale = 1
        let image = UIGraphicsImageRenderer(size: CGSize(width: width,height: height),format: format).image { context in
            UIColor(white: 40/255,alpha: 1).setFill();context.fill(CGRect(x: 0,y: 0,width: width,height: height))
            UIColor.white.setFill();context.fill(CGRect(x: width/2,y: height/2,width: width/20,height: height/15))
        }
        let jpeg = try XCTUnwrap(image.jpegData(compressionQuality: 0.95))
        try jpeg.write(to: directory.appendingPathComponent("source.jpg"))
        let pose = SpatialPose(time: 1,position: SpatialPoint(x: 0,y: 0,z: 0),forward: SpatialPoint(x: 0,y: 0,z: -1),epoch: 2)
        let frame = SpatialKeyframe(id: "synthetic",time: 1,pose: pose,intrinsics: [100,0,0,0,100,0,10,10,1],
            width: width,height: height,imageFile: "source.jpg",cameraToWorld: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])
        var labels = [UInt8](repeating: 5,count: 128*128)
        for y in 0..<128 { for x in 0..<128 {
            if x < 48 && y < 48 { labels[y*128+x] = 0 }
            if x >= 80 && y >= 80 { labels[y*128+x] = 3 }
        }}
        var row = SpatialIndoorSemanticObservation(frameID: frame.id,frameTime: 1,epoch: 2,modelID: "synthetic-render-test")
        row.status = .processed;row.width = 128;row.height = 128
        row.labels = Data(labels);row.confidence = Data(repeating: 200,count: 128*128)
        row.taxonomy = SpatialSurfaceSemanticTaxonomy(configSHA256: String(repeating: "a",count: 64),wallLabelID: 0,floorLabelID: 3,verified: true)
        return (directory,frame,row,jpeg)
    }

    private func pixel(_ image: CGImage,x: Int,y: Int) throws -> [Float] {
        let crop = try XCTUnwrap(image.cropping(to: CGRect(x: x,y: y,width: 1,height: 1)))
        var bytes = [UInt8](repeating: 0,count: 4)
        try bytes.withUnsafeMutableBytes { memory in
            let context = try XCTUnwrap(CGContext(data: memory.baseAddress,width: 1,height: 1,bitsPerComponent: 8,
                bytesPerRow: 4,space: CGColorSpaceCreateDeviceRGB(),bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
            context.draw(crop,in: CGRect(x: 0,y: 0,width: 1,height: 1))
        }
        return bytes[0..<3].map(Float.init)
    }
}
#endif
