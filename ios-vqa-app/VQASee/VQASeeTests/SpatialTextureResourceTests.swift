import XCTest
import UIKit
@testable import VQASee

@MainActor final class SpatialTextureResourceTests:XCTestCase {
    private func writePhoto(_ name:String,directory:URL,color:UIColor) throws {
        let format=UIGraphicsImageRendererFormat();format.scale=1;format.opaque=true
        let image=UIGraphicsImageRenderer(size:CGSize(width:512,height:512),format:format).image {ctx in color.setFill();ctx.fill(CGRect(x:0,y:0,width:512,height:512))}
        try XCTUnwrap(image.jpegData(compressionQuality:0.95)).write(to:directory.appendingPathComponent(name))
    }
    private func request(_ cache:SpatialDecodedTextureCache,name:String,directory:URL,scope:UUID) async -> SpatialDecodedTextureCache.Outcome {
        await withCheckedContinuation {continuation in cache.request(name:name,directory:directory,scope:scope) {continuation.resume(returning:$0)}}
    }
    func testActiveDecodedTexturesHaveHardCostLimitAndReadOffMainWithoutRemovingPlaneEvidence() async throws {
        let directory=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
        defer {try? FileManager.default.removeItem(at:directory)}
        try writePhoto("a.jpg",directory:directory,color:.red);try writePhoto("b.jpg",directory:directory,color:.green);try writePhoto("c.jpg",directory:directory,color:.blue)
        let original=try Data(contentsOf:directory.appendingPathComponent("a.jpg"))
        let cache=SpatialDecodedTextureCache(maximumDecodedBytes:2*1_024*1_024,maximumPixelSize:512)
        let scope=cache.beginScope(names:["a.jpg","b.jpg","c.jpg"],directory:directory)
        guard case let .ready(a,aCost)=await request(cache,name:"a.jpg",directory:directory,scope:scope) else {return XCTFail("First actual JPEG must decode")}
        guard case let .ready(_,bCost)=await request(cache,name:"b.jpg",directory:directory,scope:scope) else {return XCTFail("Second actual JPEG must decode")}
        XCTAssertEqual(a.width,512);XCTAssertLessThanOrEqual(aCost+bCost,cache.maximumDecodedBytes)
        guard case let .unknown(reason)=await request(cache,name:"c.jpg",directory:directory,scope:scope) else {return XCTFail("Capacity must stay unknown instead of retaining unbounded material image")}
        XCTAssertEqual(reason,"decoded_texture_budget")
        let stats=cache.snapshot();XCTAssertEqual(stats["budgetUnknown"],1);XCTAssertEqual(stats["decodesOnMainThread"],0)
        XCTAssertLessThanOrEqual(stats["peakDecodedBytes"] ?? Int.max,cache.maximumDecodedBytes)
        XCTAssertEqual(try Data(contentsOf:directory.appendingPathComponent("a.jpg")),original)
        // Changing active material support releases unused cached decoded cost.
        let next=cache.beginScope(names:["c.jpg"],directory:directory)
        guard case .ready=await request(cache,name:"c.jpg",directory:directory,scope:next) else {return XCTFail("Unused active images must release budget")}
        XCTAssertEqual(cache.snapshot()["entries"],1)
    }
    func testMissingImageAndSupersededScopeAreExplicitFailures() async throws {
        let directory=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let cache=SpatialDecodedTextureCache();let old=cache.beginScope(names:["missing.jpg"],directory:directory)
        guard case let .unknown(reason)=await request(cache,name:"missing.jpg",directory:directory,scope:old) else {return XCTFail("Missing file must fail visibly")}
        XCTAssertEqual(reason,"image_unreadable")
        _=cache.beginScope(names:[],directory:directory)
        guard case let .unknown(stale)=await request(cache,name:"missing.jpg",directory:directory,scope:old) else {return XCTFail("Old scope must not populate a new scene")}
        XCTAssertEqual(stale,"request_superseded")
    }
}
