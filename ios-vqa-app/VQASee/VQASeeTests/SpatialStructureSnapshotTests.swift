import XCTest
@testable import VQASee

final class SpatialStructureSnapshotTests: XCTestCase {
    func testInputIdentityChangesWithGeometryPoseEpochAndSemantics() throws {
        let document = fixture()
        let signature = try SpatialStructureSnapshot.inputSignature(for:document)
        var changed = document
        changed.keyframes[0].cameraToWorld[12] += 0.1
        XCTAssertNotEqual(signature,try SpatialStructureSnapshot.inputSignature(for:changed))
        changed = document;changed.keyframes[0].pose.epoch += 1
        XCTAssertNotEqual(signature,try SpatialStructureSnapshot.inputSignature(for:changed))
        changed = document;changed.surfaces[0].vertices[0].x += 0.1
        XCTAssertNotEqual(signature,try SpatialStructureSnapshot.inputSignature(for:changed))
        changed = document;changed.indoorSemanticObservations?[0].failure = "new evidence failure"
        XCTAssertNotEqual(signature,try SpatialStructureSnapshot.inputSignature(for:changed))
    }

    func testUnconsumedDepthAndSaveMetadataDoNotInvalidate() throws {
        let document = fixture()
        let signature = try SpatialStructureSnapshot.inputSignature(for:document)
        var changed = document
        changed.createdAt = Date(timeIntervalSince1970:100)
        changed.events.append("saved")
        changed.depthObservations = [SpatialDepthObservation(sourceFrameID:"f",sourceTime:1,epoch:0)]
        let old = changed.indoorSemanticObservations![0]
        changed.indoorSemanticObservations?[0].previousAttempts = [old]
        XCTAssertEqual(signature,try SpatialStructureSnapshot.inputSignature(for:changed))
    }

    func testSingleFlightAndPersistentRevision() async throws {
        let cache = SpatialStructureSnapshotCache()
        let document = SpatialScanDocument()
        async let a = cache.snapshot(for:document)
        async let b = cache.snapshot(for:document)
        let (first,second) = try await (a,b)
        XCTAssertEqual(first,second)
        let count = await cache.buildCount
        XCTAssertEqual(count,1)
        var saved = document;saved.structureSnapshot = first
        XCTAssertEqual(try SpatialStructureSnapshot.inputSignature(for:saved),first.inputSignature)
        let restarted = SpatialStructureSnapshotCache()
        let restored = try await restarted.snapshot(for:saved)
        XCTAssertEqual(restored,first)
        let restartBuilds = await restarted.buildCount
        XCTAssertEqual(restartBuilds,0)
        saved.surfaces.append(SpatialSurface(id:"new",kind:"horizontal",vertices:[],observed:false))
        XCTAssertFalse(first.matches(document:saved))
        let next = try await restarted.snapshot(for:saved)
        XCTAssertGreaterThan(next.map.revision,first.map.revision)
        XCTAssertTrue(next.matches(document:saved))
        // A lazy listing may clear stale geometry, but the revision high-water survives restart.
        saved.structureSnapshot = nil;saved.structureRevision = next.map.revision
        let restartedAgain = SpatialStructureSnapshotCache()
        let rebuilt = try await restartedAgain.snapshot(for:saved)
        XCTAssertGreaterThan(rebuilt.map.revision,next.map.revision)
    }

    func testSaveReloadExportAndLegacyListing() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer {try? FileManager.default.removeItem(at:directory)}
        let store = SpatialScanStore(directory:directory)
        let document = SpatialScanDocument()
        let cache = SpatialStructureSnapshotCache()
        var saved = document;saved.structureSnapshot = try await cache.snapshot(for:document)
        try await store.save(saved)
        let loaded = try await store.load(id:document.id)
        XCTAssertEqual(loaded.structureSnapshot,saved.structureSnapshot)
        let listed = try await store.loadAll()
        XCTAssertEqual(listed.first?.structureSnapshot,saved.structureSnapshot)
        let export = try await store.export(loaded)
        XCTAssertTrue(FileManager.default.fileExists(atPath:export.path))
        try? FileManager.default.removeItem(at:export)
        var changed = loaded
        changed.surfaces.append(SpatialSurface(id:"new",kind:"horizontal",vertices:[],observed:false))
        let savedURL=directory.appendingPathComponent(document.id.uuidString).appendingPathComponent("document.json")
        try JSONEncoder().encode(changed).write(to:savedURL)
        let staleList=try await store.loadAll()
        let stale=try XCTUnwrap(staleList.first(where:{$0.id == document.id}))
        XCTAssertNil(stale.structureSnapshot)
        XCTAssertEqual(stale.structureRevision,saved.structureSnapshot?.map.revision)
        let refreshed=try await store.load(id:document.id)
        XCTAssertGreaterThan(try XCTUnwrap(refreshed.structureSnapshot).map.revision,try XCTUnwrap(saved.structureSnapshot).map.revision)
        // A pre-snapshot scan is listed without eagerly building every archive.
        let legacy = SpatialScanDocument()
        let legacyDirectory=directory.appendingPathComponent(legacy.id.uuidString)
        try FileManager.default.createDirectory(at:legacyDirectory,withIntermediateDirectories:true)
        var json=try XCTUnwrap(JSONSerialization.jsonObject(with:JSONEncoder().encode(legacy)) as? [String:Any])
        json.removeValue(forKey:"structureSnapshot")
        try JSONSerialization.data(withJSONObject:json).write(to:legacyDirectory.appendingPathComponent("document.json"))
        let legacyList = try await store.loadAll()
        XCTAssertNil(legacyList.first(where:{$0.id == legacy.id})?.structureSnapshot)
        let selected = try await store.load(id:legacy.id)
        XCTAssertNotNil(selected.structureSnapshot)
        let unchanged = try JSONDecoder().decode(SpatialScanDocument.self,from:Data(contentsOf:legacyDirectory.appendingPathComponent("document.json")))
        XCTAssertNil(unchanged.structureSnapshot) // Reading does not rewrite the original archive.
    }

    func testPreviousCornerGatedAlgorithmSnapshotIsRebuilt() async throws {
        var doc=SpatialScanDocument()
        var oldInput=SpatialStructureInput(document:doc)
        oldInput.algorithmVersion="structure-floor-ring-v1"
        let old=SpatialStructureSnapshot(inputSignature:try oldInput.signature(),map:.init(scanID:doc.id,epoch:0,revision:7,poseRevision:7,state:.incomplete))
        doc.structureSnapshot=old;doc.structureRevision=7
        XCTAssertFalse(old.matches(document:doc))
        let cache=SpatialStructureSnapshotCache()
        let current=try await cache.snapshot(for:doc)
        XCTAssertGreaterThan(current.map.revision,7)
        XCTAssertTrue(current.matches(document:doc))
        let count=await cache.buildCount
        XCTAssertEqual(count,1)
    }

    func testMalformedDerivedGeometryCannotBeRestored() throws {
        let document=SpatialScanDocument()
        let signature=try SpatialStructureSnapshot.inputSignature(for:document)
        var map=SpatialStructureMap(scanID:document.id,epoch:0,revision:1,poseRevision:0,state:.incomplete)
        map.wallSegments=[.init(id:"wall",verticalTrackID:"track",start:.init(x:0,y:0,z:0),end:.init(x:1,y:0,z:0),startProvenance:.candidate,endProvenance:.candidate,lengthMeters:5)]
        XCTAssertFalse(SpatialStructureSnapshot(inputSignature:signature,map:map).isWellFormed)
    }

    private func fixture()->SpatialScanDocument {
        var document=SpatialScanDocument()
        document.keyframes=[SpatialKeyframe(id:"f",time:1,pose:SpatialPose(time:1,position:.init(x:0,y:0,z:0),forward:.init(x:0,y:0,z:-1),epoch:0),intrinsics:[100,0,0,0,100,0,50,50,1],width:100,height:100,imageFile:"f.jpg",cameraToWorld:[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])]
        document.surfaces=[SpatialSurface(id:"surface",kind:"horizontal",vertices:[.init(x:0,y:0,z:0),.init(x:1,y:0,z:0),.init(x:1,y:0,z:1)])]
        document.indoorSemanticObservations=[SpatialIndoorSemanticObservation(frameID:"f",frameTime:1,epoch:0,modelID:"test",failure:"not processed")]
        return document
    }
}

extension SpatialScanDocument {
    /// `SpatialScanStore.save` writes a derived structure snapshot. Tests comparing archives must include it.
    func withPersistedStructure() async throws -> SpatialScanDocument {
        var result = self
        result.structureSnapshot = try await SpatialStructureSnapshotCache.shared.snapshot(for: result)
        result.structureRevision = result.structureSnapshot?.map.revision
        return result
    }
}
