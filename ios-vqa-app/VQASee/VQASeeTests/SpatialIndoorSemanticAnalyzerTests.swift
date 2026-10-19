import XCTest
import UIKit
@testable import VQASee

final class SpatialIndoorSemanticAnalyzerTests: XCTestCase {
    func testAsymmetricRasterReturnsToNativeAxesForEveryQuarterTurn() {
        // Explicit raster control, not segmentation-accuracy evidence.
        let upright = Array(0..<16)
        let expected = [upright,
            [12,8,4,0,13,9,5,1,14,10,6,2,15,11,7,3],
            [15,14,13,12,11,10,9,8,7,6,5,4,3,2,1,0],
            [3,7,11,15,2,6,10,14,1,5,9,13,0,4,8,12]]
        for turns in 0..<4 {
            var native: [Int] = []
            for y in 0..<4 { for x in 0..<4 {
                let index = SpatialIndoorSemanticAnalyzer.uprightIndex(x: x,y: y,size: 4,turns: turns)
                native.append(upright[index.y*4+index.x])
            }}
            XCTAssertEqual(native, expected[turns])
        }
    }

    func testActualBundledManifestMatchesCompiledResourceMetadata() throws {
        let url = try XCTUnwrap(Bundle.main.url(forResource: "VQASeeIndoorSemantics.labels", withExtension: "json"))
        let manifest = try JSONDecoder().decode(SpatialIndoorSemanticManifest.self, from: Data(contentsOf: url))
        XCTAssertTrue(manifest.valid)
        XCTAssertEqual(manifest.label2id["wall"], 0)
        XCTAssertEqual(manifest.label2id["floor"], 3)
        XCTAssertEqual(manifest.id2label["8"], "windowpane")
        XCTAssertEqual(manifest.outputs, ["labels":"labels","scores":"scores"])
        XCTAssertEqual(Set(manifest.label2id.values), Set(0..<150))
        let modelURL = try XCTUnwrap(Bundle.main.url(forResource: "VQASeeIndoorSemantics", withExtension: "mlmodelc"))
        let metadata = try XCTUnwrap((try JSONSerialization.jsonObject(with: Data(contentsOf: modelURL.appendingPathComponent("metadata.json")))) as? [[String:Any]])
        let user = try XCTUnwrap(metadata.first?["userDefinedMetadata"] as? [String:String])
        XCTAssertEqual(user["modelID"], manifest.modelID)
        XCTAssertEqual(user["configSHA256"], manifest.configSHA256)
    }

    func testManifestRejectsInconsistentOrNoncontiguousTaxonomy() throws {
        let url = try XCTUnwrap(Bundle.main.url(forResource: "VQASeeIndoorSemantics.labels", withExtension: "json"))
        let original = try JSONDecoder().decode(SpatialIndoorSemanticManifest.self, from: Data(contentsOf: url))
        var invalid = original
        invalid.label2id["wall"] = 3
        XCTAssertFalse(invalid.valid)
        invalid = original
        let displaced = try XCTUnwrap(invalid.id2label.removeValue(forKey: "149"))
        invalid.id2label["150"] = displaced
        invalid.label2id[displaced] = 150
        XCTAssertFalse(invalid.valid, "A consistent 150-entry dictionary still cannot index an out-of-range model class")
        invalid = original
        invalid.id2label["00"] = invalid.id2label.removeValue(forKey: "0")
        XCTAssertFalse(invalid.valid, "Model class keys must be canonical decimal IDs")
    }

    func testMissingModelRetainsSourceAndExplicitFailureWithoutFabricatedMask() async {
        let frame = SpatialKeyframe(id: "source-photo", time: 12.75,
            pose: SpatialPose(time: 12.75, position: SpatialPoint(x: 0,y: 0,z: 0), forward: SpatialPoint(x: 0,y: 0,z: -1), epoch: 4),
            intrinsics: [600,0,0,0,600,0,384,256,1],width: 768,height: 512,imageFile: "source.jpg",
            cameraToWorld: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])
        let analyzer = SpatialIndoorSemanticAnalyzer(modelURL: nil, manifestURL: nil)
        let row = await analyzer.analyze(frame: frame, surfaces: [], directory: FileManager.default.temporaryDirectory)
        XCTAssertEqual(row.frameID, frame.id)
        XCTAssertEqual(row.frameTime, frame.time)
        XCTAssertEqual(row.epoch, frame.pose.epoch)
        XCTAssertEqual(row.status, .failed)
        XCTAssertFalse(row.failure?.isEmpty ?? true)
        XCTAssertTrue(row.elapsedSeconds.isFinite)
        XCTAssertGreaterThanOrEqual(row.elapsedSeconds, 0)
        XCTAssertNil(row.mask)
        XCTAssertTrue(row.labels.isEmpty)
        XCTAssertTrue(row.confidence.isEmpty)
        XCTAssertTrue(row.records.isEmpty)
    }
    @MainActor
    func testBundledModelActuallyInfersSyntheticJPEG() async throws {
        // A real local model smoke test, not evidence of wall/floor accuracy.
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let format = UIGraphicsImageRendererFormat(); format.scale = 1
        let image = UIGraphicsImageRenderer(size: CGSize(width: 320,height: 240), format: format).image { context in
            UIColor.white.setFill(); context.fill(CGRect(x: 0,y: 0,width: 320,height: 240))
            UIColor.blue.setFill(); context.fill(CGRect(x: 21,y: 37,width: 83,height: 169))
            UIColor.orange.setFill(); context.cgContext.fillEllipse(in: CGRect(x: 194,y: 29,width: 77,height: 49))
            UIColor.black.setFill(); context.fill(CGRect(x: 149,y: 172,width: 133,height: 17))
        }
        let name = "synthetic.jpg"
        try XCTUnwrap(image.jpegData(compressionQuality: 0.9)).write(to: directory.appendingPathComponent(name))
        let pose = SpatialPose(time: 7.5, position: SpatialPoint(x: 0,y: 0,z: 0),forward: SpatialPoint(x: 0,y: 0,z: -1),epoch: 2)
        let frame = SpatialKeyframe(id: "synthetic-smoke",time: pose.time,pose: pose,
            intrinsics: [250,0,0,0,250,0,160,120,1],width: 320,height: 240,imageFile: name,
            cameraToWorld: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])
        let modelURL = try XCTUnwrap(Bundle.main.url(forResource: "VQASeeIndoorSemantics",withExtension: "mlmodelc"))
        let manifestURL = try XCTUnwrap(Bundle.main.url(forResource: "VQASeeIndoorSemantics.labels",withExtension: "json"))
        let manifest = try JSONDecoder().decode(SpatialIndoorSemanticManifest.self,from: Data(contentsOf: manifestURL))
        let analyzer = SpatialIndoorSemanticAnalyzer(modelURL: modelURL,manifestURL: manifestURL)
        let row = await analyzer.analyze(frame: frame,surfaces: [],directory: directory)
        guard row.status == .processed else {
            throw NSError(domain: "SpatialIndoorSemanticSmoke",code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Real bundled model inference failed: \(row.failure ?? "unknown failure")"])
        }
        XCTAssertNil(row.failure)
        XCTAssertEqual(row.frameID,frame.id)
        XCTAssertEqual(row.modelID,manifest.modelID)
        XCTAssertEqual(row.width,128); XCTAssertEqual(row.height,128)
        XCTAssertEqual(row.labels.count,128*128)
        XCTAssertEqual(row.confidence.count,128*128)
        XCTAssertTrue(row.labels.allSatisfy { $0 < 150 })
        XCTAssertEqual(row.taxonomy?.configSHA256,manifest.configSHA256)
        XCTAssertEqual(row.taxonomy?.wallLabelID,manifest.label2id["wall"])
        XCTAssertEqual(row.taxonomy?.floorLabelID,manifest.label2id["floor"])
        XCTAssertTrue(row.elapsedSeconds.isFinite)
        XCTAssertGreaterThanOrEqual(row.elapsedSeconds,0)
        XCTAssertNotNil(row.mask)
    }

}
