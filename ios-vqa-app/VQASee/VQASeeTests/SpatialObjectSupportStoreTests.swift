import XCTest
@testable import VQASee

/// Synthetic calibrated cameras exercise provenance and persistence, not real-world accuracy.
final class SpatialObjectSupportStoreTests: XCTestCase {
    private func frame(id: String, x: Float) -> SpatialKeyframe {
        SpatialKeyframe(id:id,time:x == 0 ? 1 : 2,
            pose:SpatialPose(time:x == 0 ? 1 : 2,position:SpatialPoint(x:x,y:0,z:0),forward:SpatialPoint(x:0,y:0,z:-1),epoch:0),
            intrinsics:[800,0,0,0,600,0,430,310,1],width:1000,height:800,imageFile:"\(id).jpg",
            cameraToWorld:[1,0,0,0,0,1,0,0,0,0,1,0,x,0,0,1])
    }

    private func fixture() throws -> SpatialScanDocument {
        let a = frame(id:"source",x:0), b = frame(id:"target",x:0.5)
        // Native projection of (0, 0, -3), with asymmetric K.
        let match = SpatialImageMatch(sourcePixel:[430/1000,310/800],
            targetPixel:[(430-800*0.5/3)/1000,310/800],cycleErrorPixels:0.37)
        let support = try XCTUnwrap(SpatialObservationGeometry.triangulate(match,source:a,target:b))
        let observation = SpatialObjectObservation(attemptID:UUID(),frameID:a.id,epoch:0,frameTime:a.time,
            status:.processed,elapsedSeconds:0.1,detections:[SpatialObjectDetection(label:"box",score:0.8,
                boundingBox:[0.2,0.2,0.8,0.8],rawBoundingBox:[0.2,0.2,0.8,0.8])],modelID:"synthetic-test")
        let attempt = SpatialObjectSupportAttempt(sourceFrameID:a.id,sourceAttemptID:try XCTUnwrap(observation.attemptID),
            detectionIndex:0,targetFrameID:b.id,epoch:0,status:.measured,reason:"Synthetic visible-surface test only",
            elapsedSeconds:0.2,pairGridCount:150,pairMatchCount:1,sampledMatchCount:1,supports:[support])
        var document = SpatialScanDocument()
        document.keyframes = [a,b]
        document.objectObservations = [observation]
        document.objectSupportAttempts = [attempt]
        return document
    }

    private func prepare(_ document: SpatialScanDocument, store: SpatialScanStore) async throws {
        // Store validates attachment presence and size; no image decoder is used in this unit test.
        for frame in document.keyframes {
            try await store.saveKeyframe(data:Data([0xFF,0xD8,0xFF,0xD9]),scanID:document.id,filename:frame.imageFile)
        }
        try await store.save(document)
    }

    func testSurfaceEvidenceRoundTripsWithNonzeroCycleError() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at:directory) }
        let store = SpatialScanStore(directory:directory), document = try fixture()
        try await prepare(document,store:store)
        let loaded = try await store.loadAll()
        let expected = try await document.withPersistedStructure()
        XCTAssertEqual(loaded,[expected])
        XCTAssertEqual(loaded.first?.objectSupportAttempts?.first?.supports.first?.cycleErrorPixels,0.37)
    }

    func testInvalidEvidenceCannotReplaceExistingArchive() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at:directory) }
        let store = SpatialScanStore(directory:directory), document = try fixture()
        try await prepare(document,store:store)
        let mutations: [(String,(inout SpatialScanDocument) -> Void)] = [
            ("fabricated position", { $0.objectSupportAttempts![0].supports[0].position.x += 0.5 }),
            ("target epoch", { $0.keyframes[1].pose.epoch = 1 }),
            ("unknown source attempt", { $0.objectSupportAttempts![0].sourceAttemptID = UUID() }),
            ("wrong detection index", { $0.objectSupportAttempts![0].detectionIndex = 1 }),
            ("wrong source frame", { $0.objectSupportAttempts![0].sourceFrameID = "target" }),
            ("raw detection provenance", { $0.objectObservations![0].detections[0].rawBoundingBox = [0.1,0.1,0.8,0.8] }),
            ("invalid cycle", { $0.objectSupportAttempts![0].supports[0].cycleErrorPixels = 2 }),
            ("negative cycle", { $0.objectSupportAttempts![0].supports[0].cycleErrorPixels = -1 }),
            ("fabricated reprojection", { $0.objectSupportAttempts![0].supports[0].reprojectionErrorPixels = 1.5 })
        ]
        for (name, mutate) in mutations {
            var invalid = document
            mutate(&invalid)
            do {
                try await store.save(invalid)
                XCTFail("Must reject \(name)")
            } catch SpatialScanStore.StoreError.invalidDocument {
                // Correct: rejection occurs before replacing the valid archive.
            } catch {
                XCTFail("Unexpected error for \(name): \(error)")
            }
            let preserved = try await store.loadAll()
            let expected = try await document.withPersistedStructure()
            XCTAssertEqual(preserved,[expected],"Must preserve archive after \(name)")
        }
    }
}
