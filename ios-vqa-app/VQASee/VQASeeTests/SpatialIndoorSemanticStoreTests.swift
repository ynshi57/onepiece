import XCTest
@testable import VQASee

/// Synthetic masks test storage integrity and provenance, not segmentation accuracy.
final class SpatialIndoorSemanticStoreTests: XCTestCase {
    private func fixture() -> SpatialScanDocument {
        let frame = SpatialKeyframe(id:"frame",time:1,
            pose:SpatialPose(time:1,position:SpatialPoint(x:0,y:0,z:0),forward:SpatialPoint(x:0,y:0,z:-1),epoch:0),
            intrinsics:[800,0,0,0,600,0,430,310,1],width:1000,height:800,imageFile:"frame.jpg",
            cameraToWorld:[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])
        let vertices = [SpatialPoint(x:-1,y:-1,z:-3),SpatialPoint(x:1,y:-1,z:-3),SpatialPoint(x:1,y:1,z:-3),SpatialPoint(x:-1,y:1,z:-3)]
        let taxonomy = SpatialSurfaceSemanticTaxonomy(configSHA256:String(repeating:"a",count:64),wallLabelID:0,floorLabelID:3,verified:true)
        let mask = SpatialSurfaceSemanticMask(frameID:frame.id,epoch:0,modelID:"synthetic",taxonomy:taxonomy,
            width:128,height:128,labels:Array(repeating:0,count:128*128),scores:Array(repeating:1,count:128*128))
        let surface = SpatialSurface(id:"wall",kind:"vertical",vertices:vertices)
        let record = SpatialSurfaceSemanticEvidence.evaluate(surface:surface,frame:frame,mask:mask)
        var row = SpatialIndoorSemanticObservation(frameID:frame.id,frameTime:1,epoch:0,modelID:"synthetic",
            status:.processed,failure:nil,elapsedSeconds:0.1,width:128,height:128,
            labels:Data(repeating:0,count:128*128),confidence:Data(repeating:255,count:128*128),taxonomy:taxonomy,records:[record])
        var historical = row
        historical.attemptID = UUID()
        historical.records[0].method = "plane-ray-native-mask-24x24-v1"
        historical.records[0].surfaceVertices[0].x -= 0.2
        row.previousAttempts = [historical]
        var document = SpatialScanDocument()
        document.keyframes = [frame]; document.surfaces = [surface]
        document.indoorSemanticObservations = [row]
        return document
    }

    private func prepare(_ document: SpatialScanDocument, store: SpatialScanStore) async throws {
        // These tests exercise Store attachment checks, not JPEG decoding.
        try await store.saveKeyframe(data:Data([0xFF,0xD8,0xFF,0xD9]),scanID:document.id,filename:"frame.jpg")
        try await store.save(document)
    }

    func testMaskAndHistoricalGeometryRoundTrip() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at:directory) }
        let document = fixture(), store = SpatialScanStore(directory:directory)
        try await prepare(document,store:store)
        let loaded = try await store.loadAll()
        XCTAssertEqual(loaded,[document])
        XCTAssertNotEqual(loaded.first?.indoorSemanticObservations?.first?.previousAttempts?.first?.records.first?.surfaceVertices,document.surfaces.first?.vertices)
    }

    func testRejectsCorruptionAndSourceMismatchWithoutReplacingArchive() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at:directory) }
        let document = fixture(), store = SpatialScanStore(directory:directory)
        try await prepare(document,store:store)
        let mutations: [(String,(inout SpatialScanDocument)->Void)] = [
            ("cross epoch", { $0.indoorSemanticObservations![0].epoch = 1 }),
            ("frame time", { $0.indoorSemanticObservations![0].frameTime = 2 }),
            ("truncated labels", { $0.indoorSemanticObservations![0].labels.removeLast() }),
            ("invalid class", { $0.indoorSemanticObservations![0].labels[0] = 150 }),
            ("truncated confidence", { $0.indoorSemanticObservations![0].confidence.removeLast() }),
            ("unverified taxonomy", { $0.indoorSemanticObservations![0].taxonomy!.verified = false }),
            ("bad hash", { $0.indoorSemanticObservations![0].taxonomy!.configSHA256 = String(repeating:"z",count:64) }),
            ("invalid label id", { $0.indoorSemanticObservations![0].taxonomy!.floorLabelID = 150 }),
            ("same label ids", { $0.indoorSemanticObservations![0].taxonomy!.floorLabelID = 0 }),
            ("unknown method", { $0.indoorSemanticObservations![0].records[0].method = "unknown" }),
            ("record model mismatch", { $0.indoorSemanticObservations![0].records[0].modelID = "other" }),
            ("record config mismatch", { $0.indoorSemanticObservations![0].records[0].configSHA256 = String(repeating:"b",count:64) }),
            ("record frame mismatch", { $0.indoorSemanticObservations![0].records[0].frameID = "missing" }),
            ("negative count", { $0.indoorSemanticObservations![0].records[0].wallSupport = -1 }),
            ("count sum", { $0.indoorSemanticObservations![0].records[0].totalSamples -= 1 }),
            ("history corruption", { $0.indoorSemanticObservations![0].previousAttempts![0].confidence.removeLast() }),
            ("history source mismatch", { $0.indoorSemanticObservations![0].previousAttempts![0].frameTime = 2 }),
            ("duplicate attempt", { $0.indoorSemanticObservations![0].previousAttempts![0].attemptID = $0.indoorSemanticObservations![0].attemptID }),
            ("failure carrying mask", { $0.indoorSemanticObservations![0].status = .failed; $0.indoorSemanticObservations![0].failure = "model failed" })
        ]
        for (name,mutate) in mutations {
            var invalid = document; mutate(&invalid)
            do { try await store.save(invalid); XCTFail("Must reject \(name)") }
            catch SpatialScanStore.StoreError.invalidDocument { }
            catch { XCTFail("Unexpected error for \(name): \(error)") }
            let preserved = try await store.loadAll()
            XCTAssertEqual(preserved,[document],name)
        }
    }

    func testFailureWithoutMaskRoundTripsButNeedsReason() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at:directory) }
        var document = fixture()
        document.indoorSemanticObservations = [SpatialIndoorSemanticObservation(frameID:"frame",frameTime:1,epoch:0,
            modelID:"unavailable",failure:"missing model")]
        let store = SpatialScanStore(directory:directory)
        try await prepare(document,store:store)
        let loaded = try await store.loadAll()
        XCTAssertEqual(loaded,[document])
        document.indoorSemanticObservations![0].failure = nil
        do { try await store.save(document); XCTFail("Failure must have a reason") }
        catch SpatialScanStore.StoreError.invalidDocument { }
    }
}
