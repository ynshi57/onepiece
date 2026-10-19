import XCTest
import UIKit
@testable import VQASee

/// Synthetic raster protocol evidence only: no detection accuracy or scene truth claim.
@MainActor
final class SpatialArchiveObjectIntegrationTests: XCTestCase {
    func testSavedJPEGsAreAnalyzedArchivedAndReloadedWithStableSources() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1 // Raster size must match archived K and image metadata on every simulator/device.
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 320, height: 240), format: format)
        let image = renderer.image { context in
            UIColor.white.setFill(); context.fill(CGRect(x: 0,y: 0,width: 320,height: 240))
            UIColor.systemBlue.setFill(); context.fill(CGRect(x: 29,y: 41,width: 87,height: 153))
            UIColor.systemOrange.setFill(); context.cgContext.fillEllipse(in: CGRect(x: 191,y: 27,width: 63,height: 51))
            UIColor.black.setStroke(); context.cgContext.setLineWidth(7)
            context.cgContext.move(to: CGPoint(x: 143,y: 204)); context.cgContext.addLine(to: CGPoint(x: 297,y: 91)); context.cgContext.strokePath()
        }
        let jpeg = try XCTUnwrap(image.jpegData(compressionQuality: 0.9))
        XCTAssertEqual(image.cgImage?.width, 320)
        XCTAssertEqual(image.cgImage?.height, 240)
        var document = SpatialScanDocument()
        for index in 0..<2 {
            let time = Double(index + 1), x = Float(index) * 0.12
            let pose = SpatialPose(time: time, position: SpatialPoint(x: x,y: 0,z: 0),
                                   forward: SpatialPoint(x: 0,y: 0,z: -1), epoch: 3)
            let frame = SpatialKeyframe(id: "stored-\(index)", time: time, pose: pose,
                intrinsics: [250,0,0,0,250,0,160,120,1], width: 320, height: 240,
                imageFile: "stored-\(index).jpg", cameraToWorld: [1,0,0,0,0,1,0,0,0,0,1,0,x,0,0,1])
            try await store.saveKeyframe(data: jpeg, scanID: document.id, filename: frame.imageFile)
            document.keyframes.append(frame)
            document.poses.append(pose)
        }
        // Commit the real JPEG-bearing scan before init cleanup; do not rely on timing sleeps.
        try await store.save(document)
        let controller = SpatialScanController(store: store)
        controller.document = document
        for frame in document.keyframes {
            XCTAssertEqual(try Data(contentsOf: store.scanDirectoryURL(document.id).appendingPathComponent(frame.imageFile)), jpeg)
        }
        for frame in document.keyframes { controller.archiveSavedKeyframe(frame.id) }
        controller.save() // A real capture enrolls frames; old scans are not silently forced to run new models.
        let deadline = Date().addingTimeInterval(30)
        while (controller.isArchivingObjects || controller.document.objectObservations?.count != 2), Date() < deadline {
            try await Task.sleep(for: .milliseconds(50))
        }
        XCTAssertFalse(controller.isArchivingObjects, "Real local inference must finish or retain a visible timeout failure; no fake short deadline")
        for frame in document.keyframes {
            XCTAssertEqual(try Data(contentsOf: store.scanDirectoryURL(document.id).appendingPathComponent(frame.imageFile)), jpeg)
        }
        let observations = try XCTUnwrap(controller.document.objectObservations)
        XCTAssertEqual(observations.count, 2)
        XCTAssertEqual(controller.document.indoorSemanticObservations?.count, 2)
        let semanticRows = try XCTUnwrap(controller.document.indoorSemanticObservations)
        for row in semanticRows {
            guard row.status == .processed else {
                throw NSError(domain: "SpatialArchiveSemanticIntegration",code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "Actual archived JPEG inference failed for \(row.frameID): \(row.failure ?? "unknown failure")"])
            }
            XCTAssertNil(row.failure)
            XCTAssertEqual(row.labels.count,128*128)
            XCTAssertEqual(row.confidence.count,128*128)
            XCTAssertNotNil(row.mask)
        }
        XCTAssertEqual(Set((controller.document.indoorSemanticObservations ?? []).map(\.frameID)), Set(document.keyframes.map(\.id)))
        XCTAssertEqual(Set(observations.compactMap(\.attemptID)).count, 2)
        for frame in document.keyframes {
            let observation = try XCTUnwrap(observations.first { $0.frameID == frame.id })
            XCTAssertNotNil(observation.attemptID)
            XCTAssertEqual(observation.epoch, frame.pose.epoch)
            XCTAssertEqual(observation.frameTime, frame.time)
            XCTAssertTrue(observation.elapsedSeconds.isFinite)
            XCTAssertGreaterThanOrEqual(observation.elapsedSeconds, 0)
            if observation.status == .failed || observation.status == .partial {
                let failure = try XCTUnwrap(observation.failure)
                XCTAssertFalse(failure.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                XCTAssertFalse(failure.contains("invalidImageDimensions"), "Fixture dimensions are real; protocol failures cannot stand in for inference")
            }
            if observation.status == .failed { XCTAssertTrue(observation.detections.isEmpty) }
        }
        controller.save()
        while (controller.isSaving || controller.isAnalyzingIndoorSemantics) && Date() < deadline { try await Task.sleep(for: .milliseconds(50)) }
        XCTAssertFalse(controller.isSaving)
        let reloaded = try await store.loadAll()
        let restored = try XCTUnwrap(reloaded.first { $0.id == document.id })
        XCTAssertEqual(restored.objectObservations, observations)
        XCTAssertEqual(restored.indoorSemanticObservations, controller.document.indoorSemanticObservations)
        for frame in document.keyframes {
            let url = store.scanDirectoryURL(document.id).appendingPathComponent(frame.imageFile)
            XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
            XCTAssertEqual(try Data(contentsOf: url), jpeg)
        }
    }
}

extension SpatialArchiveObjectIntegrationTests {
    func testLegacyScanCanSaveWithoutForcingNewInference() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        let pose = SpatialPose(time: 1, position: SpatialPoint(x: 0,y: 0,z: 0), forward: SpatialPoint(x: 0,y: 0,z: -1), epoch: 0)
        let frame = SpatialKeyframe(id: "legacy", time: 1, pose: pose, intrinsics: [20,0,0,0,20,0,10,10,1],
            width: 20,height: 20,imageFile: "legacy.jpg",cameraToWorld: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])
        var document = SpatialScanDocument()
        document.poses = [pose]; document.keyframes = [frame]
        let format = UIGraphicsImageRendererFormat(); format.scale = 1
        let image = UIGraphicsImageRenderer(size: CGSize(width: 20,height: 20), format: format).image { context in
            UIColor.white.setFill(); context.fill(CGRect(x: 0,y: 0,width: 20,height: 20))
        }
        try await store.saveKeyframe(data: XCTUnwrap(image.jpegData(compressionQuality: 0.9)), scanID: document.id, filename: frame.imageFile)
        try await store.save(document)
        let controller = SpatialScanController(store: store)
        controller.document = document
        controller.save()
        let deadline = Date().addingTimeInterval(10)
        while controller.isSaving && Date() < deadline { try await Task.sleep(for: .milliseconds(50)) }
        XCTAssertFalse(controller.isSaving)
        XCTAssertFalse(controller.isArchivingObjects)
        XCTAssertFalse(controller.isAnalyzingIndoorSemantics)
        XCTAssertNil(controller.document.objectObservations)
        XCTAssertNil(controller.document.indoorSemanticObservations)
        let saved = try await store.loadAll()
        let restored = try XCTUnwrap(saved.first { $0.id == document.id })
        XCTAssertNil(restored.indoorSemanticObservations)
        XCTAssertNil(restored.objectObservations)
    }
}
