import XCTest
@testable import VQASee

@MainActor
final class SpatialLiveSemanticReprojectorTests: XCTestCase {
    private func frames() -> [SpatialKeyframe] {
        [Float(0),0.04].enumerated().map { index, x in
            SpatialKeyframe(id:"synthetic-\(index)",time:Double(index+1),
                pose:SpatialPose(time:Double(index+1),position:SpatialPoint(x:x,y:0,z:0),forward:SpatialPoint(x:0,y:0,z:-1),epoch:1),
                intrinsics:[100,0,0,0,100,0,49.5,49.5,1],width:100,height:100,imageFile:"unused.jpg",
                cameraToWorld:[1,0,0,0,0,1,0,0,0,0,1,0,x,0,0,1])
        }
    }
    private func plane(_ id: String = "wall", depth: Float = -2, epoch: Int = 1) -> SpatialSurface {
        SpatialSurface(id:id,kind:"vertical",vertices:[SpatialPoint(x:-3,y:-3,z:depth),SpatialPoint(x:3,y:-3,z:depth),SpatialPoint(x:3,y:3,z:depth),SpatialPoint(x:-3,y:3,z:depth)],epoch:epoch)
    }
    private func observations(frames: [SpatialKeyframe], oldGeometry: [SpatialSurface]) -> [SpatialIndoorSemanticObservation] {
        frames.map { frame in
            var row = SpatialIndoorSemanticObservation(frameID:frame.id,frameTime:frame.time,epoch:frame.pose.epoch,modelID:"synthetic-test-only")
            row.status = .processed; row.width = 128; row.height = 128
            row.labels = Data(repeating:17,count:128*128); row.confidence = Data(repeating:230,count:128*128)
            row.taxonomy = SpatialSurfaceSemanticTaxonomy(configSHA256:String(repeating:"a",count:64),wallLabelID:17,floorLabelID:29,verified:true)
            row.records = SpatialSurfaceSemanticEvidence.evaluateAll(surfaces:oldGeometry,frame:frame,mask:row.mask!)
            return row
        }
    }

    func testActualActorReprojectsChangedGeometryWithoutMutatingSources() async {
        let sourceFrames = frames(), oldPlane = plane(depth:-2), newPlane = plane(depth:-2.3)
        let rows = observations(frames:sourceFrames,oldGeometry:[oldPlane]), original = rows
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.resolve(surface:newPlane,records:rows.flatMap(\.records)),.unknown)
        let reconciled = await SpatialLiveSemanticReprojector().reconcile(rows:rows,frames:sourceFrames,surfaces:[newPlane])
        XCTAssertEqual(rows,original)
        XCTAssertEqual(reconciled.map(\.attemptID),rows.map(\.attemptID))
        XCTAssertEqual(reconciled.map(\.labels),rows.map(\.labels))
        XCTAssertTrue(reconciled.flatMap(\.records).allSatisfy { $0.surfaceVertices == newPlane.vertices && $0.wallSupport == 576 })
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.resolve(surface:newPlane,records:reconciled.flatMap(\.records)),.wall)
        XCTAssertEqual(rows.flatMap(\.records).map(\.surfaceVertices),[oldPlane.vertices,oldPlane.vertices])
    }

    func testActualActorRecomputesWholeSceneOcclusionInsteadOfCopyingOldWallVotes() async {
        let sourceFrames = frames(), rear = plane(), front = plane("front",depth:-1)
        let rows = observations(frames:sourceFrames,oldGeometry:[rear]), original = rows
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.resolve(surface:rear,records:rows.flatMap(\.records)),.wall)
        let reconciled = await SpatialLiveSemanticReprojector().reconcile(rows:rows,frames:sourceFrames,surfaces:[rear,front])
        XCTAssertEqual(rows,original)
        let rearRecords = reconciled.flatMap(\.records).filter { $0.surfaceID == rear.id }
        XCTAssertEqual(rearRecords.count,2)
        XCTAssertTrue(rearRecords.allSatisfy { $0.occluded == 576 && $0.wallSupport == 0 && $0.surfaceVertices == rear.vertices })
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.resolve(surface:rear,records:rearRecords),.unknown)
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.resolve(surface:front,records:reconciled.flatMap(\.records)),.wall)
    }

    func testActualActorDoesNotUseAnotherEpochAsOccluder() async {
        let sourceFrames = frames(), rear = plane(), foreignFront = plane("foreign",depth:-1,epoch:2)
        let rows = observations(frames:sourceFrames,oldGeometry:[rear])
        let reconciled = await SpatialLiveSemanticReprojector().reconcile(rows:rows,frames:sourceFrames,surfaces:[rear,foreignFront])
        let records = reconciled.flatMap(\.records)
        XCTAssertEqual(records.count,2)
        XCTAssertTrue(records.allSatisfy { $0.surfaceID == rear.id && $0.epoch == 1 && $0.wallSupport == 576 && $0.occluded == 0 })
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.resolve(surface:rear,records:records),.wall)
        XCTAssertEqual(SpatialSurfaceSemanticEvidence.resolve(surface:foreignFront,records:records),.unknown)
    }
}
