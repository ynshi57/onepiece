import XCTest
@testable import VQASee

final class SpatialLiveSemanticPolicyTests: XCTestCase {
    private func row(time: Double, epoch: Int = 0) -> SpatialIndoorSemanticObservation {
        SpatialIndoorSemanticObservation(frameID:"frame-\(time)",frameTime:time,epoch:epoch,modelID:"test",status:.processed,
            failure:nil,width:128,height:128,labels:Data(repeating:0,count:128*128),confidence:Data(repeating:255,count:128*128),
            taxonomy:SpatialSurfaceSemanticTaxonomy(configSHA256:String(repeating:"a",count:64),wallLabelID:0,floorLabelID:3,verified:true))
    }
    private var surface: SpatialSurface {
        SpatialSurface(id:"wall",kind:"vertical",vertices:[SpatialPoint(x:0,y:0,z:-2),SpatialPoint(x:1,y:0,z:-2),SpatialPoint(x:1,y:1,z:-2)])
    }
    func testWindowIsBoundedRecentProcessedSameEpoch() {
        var rows = (0..<8).map { row(time:Double($0)) }
        rows.append(row(time:100,epoch:1))
        var failure = row(time:101); failure.status = .failed; rows.append(failure)
        var truncated = row(time:102); truncated.labels.removeLast(); rows.append(truncated)
        var noTaxonomy = row(time:103); noTaxonomy.taxonomy = nil; rows.append(noTaxonomy)
        XCTAssertEqual(SpatialLiveSemanticPolicy.window(rows:rows,epoch:0).map(\.frameTime),[7,6,5,4])
    }
    func testWholeSceneGateIncludesOccludersButIgnoresObservationTime() {
        let target = surface
        var occluder = surface; occluder.id = "occluder"
        let original = [target,occluder]
        var timestampOnly = occluder; timestampOnly.observationTime = 99
        XCTAssertTrue(SpatialLiveSemanticPolicy.sameScene(original,[timestampOnly,target]))
        var moved = occluder; moved.vertices[0].z -= 0.1
        XCTAssertFalse(SpatialLiveSemanticPolicy.sameScene(original,[target,moved]))
        XCTAssertFalse(SpatialLiveSemanticPolicy.sameScene(original,[target]))
        var added = occluder; added.id = "new"
        XCTAssertFalse(SpatialLiveSemanticPolicy.sameScene(original,[target,occluder,added]))
        var kind = occluder; kind.kind = "horizontal"
        XCTAssertFalse(SpatialLiveSemanticPolicy.sameScene(original,[target,kind]))
        var epoch = occluder; epoch.epoch = 1
        XCTAssertFalse(SpatialLiveSemanticPolicy.sameScene(original,[target,epoch]))
        var hidden = occluder; hidden.observed = false
        XCTAssertFalse(SpatialLiveSemanticPolicy.sameScene(original,[target,hidden]))
    }

    func testRetainAttemptsUsesCurrentProvenance() {
        let original = row(time:1)
        XCTAssertEqual(SpatialLiveSemanticPolicy.retainAttempts(computed:[original],current:[original]),[original])
        let mutations: [(inout SpatialIndoorSemanticObservation)->Void] = [
            { $0.attemptID = UUID() }, { $0.frameID = "new-frame" }, { $0.epoch = 1 },
            { $0.modelID = "new-model" }, { $0.taxonomy!.configSHA256 = String(repeating:"b",count:64) }
        ]
        for mutate in mutations {
            var replacement = original; mutate(&replacement)
            XCTAssertTrue(SpatialLiveSemanticPolicy.retainAttempts(computed:[original],current:[replacement]).isEmpty)
        }
        XCTAssertTrue(SpatialLiveSemanticPolicy.retainAttempts(computed:[original],current:[]).isEmpty)
    }
}
