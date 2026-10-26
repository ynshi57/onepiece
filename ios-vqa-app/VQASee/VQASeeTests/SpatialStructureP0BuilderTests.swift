import simd
import XCTest
@testable import VQASee

final class SpatialStructureP0BuilderTests: XCTestCase {
    private func fixture() -> SpatialScanDocument {
        var doc=SpatialScanDocument()
        doc.surfaces=[SpatialSurface(id:"floor",kind:"horizontal",vertices:[SpatialPoint(x:0,y:0,z:0),SpatialPoint(x:2,y:0,z:0),SpatialPoint(x:2,y:0,z:2),SpatialPoint(x:0,y:0,z:2)],epoch:1),SpatialSurface(id:"wall-x",kind:"vertical",vertices:[SpatialPoint(x:0,y:0,z:0),SpatialPoint(x:0,y:2,z:0),SpatialPoint(x:0,y:2,z:2),SpatialPoint(x:0,y:0,z:2)],epoch:1),SpatialSurface(id:"wall-z",kind:"vertical",vertices:[SpatialPoint(x:0,y:0,z:0),SpatialPoint(x:2,y:0,z:0),SpatialPoint(x:2,y:2,z:0),SpatialPoint(x:0,y:2,z:0)],epoch:1)]
        doc.keyframes=(0..<20).map { i in
            let x=Float(i)*0.01
            return SpatialKeyframe(id:"f\(i)",time:Double(i),pose:SpatialPose(time:Double(i),position:SpatialPoint(x:x,y:1,z:3),forward:SpatialPoint(x:0,y:0,z:-1),epoch:1),intrinsics:[80,0,0,0,80,0,50,50,1],width:100,height:100,imageFile:"f\(i).jpg",cameraToWorld:[1,0,0,0,0,1,0,0,0,0,1,0,x,1,3,1])
        }
        // Two source-linked synthetic observations establish a floor label, not dimensional accuracy.
        doc.indoorSemanticObservations=doc.keyframes.prefix(2).map {frame in
            var row=SpatialIndoorSemanticObservation(frameID:frame.id,frameTime:frame.time,epoch:1,modelID:"synthetic")
            row.status = .processed;row.width=128;row.height=128
            row.labels=Data(repeating:3,count:128*128);row.confidence=Data(repeating:230,count:128*128)
            row.taxonomy=SpatialSurfaceSemanticTaxonomy(configSHA256:String(repeating:"a",count:64),wallLabelID:0,floorLabelID:3,verified:true)
            row.records=doc.surfaces.map {surface in
                var record=SpatialSurfaceSemanticRecord(surfaceID:surface.id,surfaceVertices:surface.vertices,epoch:1,frameID:frame.id,frameTime:frame.time,modelID:"synthetic",configSHA256:String(repeating:"a",count:64))
                record.status = .sampled;record.totalSamples=20
                if surface.kind == "horizontal" {record.floorSupport=20} else {record.wallSupport=20}
                return record
            }
            return row
        }
        return doc
    }
    /// Synthetic geometry mutations require fresh source-linked semantic records too.
    private func refreshSyntheticWallEvidence(_ doc:inout SpatialScanDocument) {
        for index in doc.indoorSemanticObservations!.indices {
            let row=doc.indoorSemanticObservations![index]
            doc.indoorSemanticObservations![index].records=doc.surfaces.map {surface in
                var record=SpatialSurfaceSemanticRecord(surfaceID:surface.id,surfaceVertices:surface.vertices,epoch:1,frameID:row.frameID,frameTime:row.frameTime,modelID:row.modelID,configSHA256:row.taxonomy!.configSHA256)
                record.status = .sampled;record.totalSamples=20
                if surface.kind == "horizontal" {record.floorSupport=20} else {record.wallSupport=20}
                return record
            }
        }
    }
    func testSyntheticRoomCornerProducesCandidateAndWallSpans() {
        // Corrected the original invalid K/no-T mock: column-major calibrated frames and explicit floor evidence.
        let doc=fixture(),built=SpatialStructureP0Builder.build(document:doc)
        XCTAssertEqual(built.map.corners.count,1)
        XCTAssertEqual(built.map.corners.first?.provenance,.candidate)
        XCTAssertEqual(built.map.corners[0].position.x,0,accuracy:0.05)
        XCTAssertEqual(built.map.corners[0].position.y,0,accuracy:0.05)
        XCTAssertEqual(built.map.corners[0].position.z,0,accuracy:0.05)
        XCTAssertGreaterThanOrEqual(built.map.wallSegments.count,2)
        XCTAssertEqual(built.receipt.tapeSegments.count,min(2,built.map.wallSegments.count))
        XCTAssertTrue(built.map.floorRegions.isEmpty)
        XCTAssertTrue(built.map.quality.message.contains("未形成地面闭环"))
        XCTAssertEqual(built.map.state,.incomplete)
        XCTAssertTrue(built.map.quality.geometricFloorDatum)
        XCTAssertFalse(built.map.quality.circuitClosed)
        XCTAssertTrue(built.map.quality.semanticFloorVerified)
        XCTAssertTrue(built.map.wallSegments.allSatisfy {$0.startProvenance == .candidate && $0.endProvenance == .candidate})
        XCTAssertEqual(built.map.frameIDs.count,20)
    }
    func testKeyframePickerSpreadsSamples() {
        // Selection is now a bounded local temporal window of the same projected corner, not scan-wide spreading.
        var frames=fixture().keyframes
        for i in frames.indices {frames[i].time=Double(i);frames[i].pose.time=Double(i)}
        let ids=SpatialStructureP0Builder.selectKeyframes(frames,corner:SpatialPoint(x:0,y:0,z:0))
        XCTAssertGreaterThanOrEqual(ids.count,12);XCTAssertLessThanOrEqual(ids.count,20)
        XCTAssertEqual(ids,frames.map(\.id))
        XCTAssertTrue(SpatialStructureP0Builder.selectKeyframes(frames).isEmpty)
    }
    func testUnknownLowestHorizontalStillPublishesWallFeet() {
        var doc=fixture()
        for index in doc.indoorSemanticObservations!.indices {
            for j in doc.indoorSemanticObservations![index].records.indices where doc.indoorSemanticObservations![index].records[j].surfaceID == "floor" {
                doc.indoorSemanticObservations![index].records[j].floorSupport=0
                doc.indoorSemanticObservations![index].records[j].uncertain=20
            }
        }
        let map=SpatialStructureP0Builder.build(document:doc).map
        XCTAssertFalse(map.quality.semanticFloorVerified)
        XCTAssertTrue(map.quality.geometricFloorDatum)
        XCTAssertGreaterThan(map.quality.rejections["noSemanticFloor"] ?? 0,0)
        XCTAssertGreaterThanOrEqual(map.wallSegments.count,2)
        XCTAssertTrue(map.wallSegments.allSatisfy {abs($0.start.y)<0.0001 && abs($0.end.y)<0.0001})
        XCTAssertEqual(map.state,.incomplete)
        XCTAssertTrue(map.quality.message.contains("语义地面未确认"))
    }
    func testHigherTabletopIsNotGeometricDatum() {
        var doc=fixture()
        var table=doc.surfaces[0]
        table.id="table"
        table.vertices=table.vertices.map {SpatialPoint(x:$0.x,y:0.8,z:$0.z)}
        doc.surfaces.append(table)
        for index in doc.indoorSemanticObservations!.indices {
            let row=doc.indoorSemanticObservations![index]
            var records=row.records
            for j in records.indices where records[j].surfaceID == "floor" {
                records[j].floorSupport=0
                records[j].uncertain=20
            }
            var tableRecord=SpatialSurfaceSemanticRecord(surfaceID:"table",surfaceVertices:table.vertices,epoch:1,frameID:row.frameID,frameTime:row.frameTime,modelID:row.modelID,configSHA256:row.taxonomy!.configSHA256)
            tableRecord.status = .sampled;tableRecord.totalSamples=20;tableRecord.otherOpposition=20
            records.append(tableRecord)
            doc.indoorSemanticObservations![index].records=records
        }
        let map=SpatialStructureP0Builder.build(document:doc).map
        XCTAssertFalse(map.quality.semanticFloorVerified)
        XCTAssertTrue(map.quality.geometricFloorDatum)
        XCTAssertGreaterThanOrEqual(map.wallSegments.count,2)
        XCTAssertTrue(map.wallSegments.allSatisfy {abs($0.start.y)<0.0001 && abs($0.end.y)<0.0001})
        XCTAssertTrue(map.wallSegments.allSatisfy {abs($0.start.y-0.8)>0.1 && abs($0.end.y-0.8)>0.1})
        XCTAssertEqual(map.state,.incomplete)
    }
    func testInfinitePlaneIntersectionOutsideFinitePatchIsRejected() {
        var doc=fixture()
        doc.surfaces[1].vertices=doc.surfaces[1].vertices.map {SpatialPoint(x:$0.x,y:$0.y,z:$0.z+5)}
        refreshSyntheticWallEvidence(&doc)
        let result=SpatialStructureP0Builder.build(document:doc)
        XCTAssertEqual(result.map.wallSegments.count,2)
        XCTAssertTrue(result.map.corners.isEmpty)
        XCTAssertGreaterThan(result.map.quality.rejections["cornerOutsideFiniteSegments"] ?? 0,0)
    }
    func testInvisibleBadCalibrationAndWrongEpochFramesAreNotInvented() {
        var doc=fixture()
        for i in doc.keyframes.indices {doc.keyframes[i].cameraToWorld[14] = -3;doc.keyframes[i].pose.position.z = -3}
        let result=SpatialStructureP0Builder.build(document:doc)
        XCTAssertEqual(result.map.corners.count,1)
        XCTAssertTrue(result.map.corners.allSatisfy {$0.frameIDs.isEmpty})
        XCTAssertEqual(result.map.quality.rejections["noVisibleCornerFrames"],1)
        var frames=fixture().keyframes
        frames[0].intrinsics[0]=0;frames[1].pose.epoch=2
        let ids=SpatialStructureP0Builder.selectKeyframes(frames,corner:SpatialPoint(x:0,y:0,z:0))
        XCTAssertFalse(ids.contains(frames[0].id));XCTAssertFalse(ids.contains(frames[1].id))
    }
    func testSeparatedPatchIntervalsKeepGapAndNeverClaimObservedLength() {
        var doc=fixture()
        // Association radius intentionally large for this clustering test; publishing must still preserve the gap.
        let original=doc.surfaces[2]
        doc.surfaces[2].vertices=original.vertices.map {SpatialPoint(x:$0.x*0.25,y:$0.y,z:$0.z)}
        var second=original;second.id="wall-z-separated";second.vertices=original.vertices.map {SpatialPoint(x:2+$0.x*0.25,y:$0.y,z:$0.z)}
        doc.surfaces.append(second)
        refreshSyntheticWallEvidence(&doc)
        let result=SpatialStructureP0Builder.build(document:doc)
        XCTAssertFalse(result.map.gaps.isEmpty)
        let segments=result.map.wallSegments.filter {$0.sourceSurfaceIDs.contains("wall-z") || $0.sourceSurfaceIDs.contains("wall-z-separated")}
        XCTAssertEqual(segments.count,2)
        XCTAssertTrue(segments.allSatisfy {$0.lengthMeters<=0.51 && $0.startProvenance == .candidate})
        XCTAssertTrue(result.map.gaps.allSatisfy {$0.reason.contains("not a doorway")})
    }
    func testUnknownAndOtherSemanticsDoNotPublishWalls() {
        for other in [false,true] {
            var doc=fixture()
            for index in doc.indoorSemanticObservations!.indices {
                if other {
                    for j in doc.indoorSemanticObservations![index].records.indices where doc.indoorSemanticObservations![index].records[j].surfaceID != "floor" {
                        doc.indoorSemanticObservations![index].records[j].wallSupport=0
                        doc.indoorSemanticObservations![index].records[j].otherOpposition=20
                    }
                } else {doc.indoorSemanticObservations![index].records.removeAll {$0.surfaceID != "floor"}}
            }
            let map=SpatialStructureP0Builder.build(document:doc).map
            XCTAssertTrue(map.wallSegments.isEmpty);XCTAssertTrue(map.corners.isEmpty)
            XCTAssertTrue(map.quality.semanticFloorVerified)
            XCTAssertEqual(map.quality.rejections.values.reduce(0,+)>=2,true)
        }
    }
    func testMixedWholeSurfaceVoteStillPublishesLocalWallMaskIntervals() {
        var doc=fixture()
        for index in doc.indoorSemanticObservations!.indices {
            doc.indoorSemanticObservations![index].labels=Data(repeating:0,count:128*128)
            doc.indoorSemanticObservations![index].confidence=Data(repeating:230,count:128*128)
            for j in doc.indoorSemanticObservations![index].records.indices where doc.indoorSemanticObservations![index].records[j].surfaceID != "floor" {
                doc.indoorSemanticObservations![index].records[j].wallSupport=10
                doc.indoorSemanticObservations![index].records[j].otherOpposition=10
                doc.indoorSemanticObservations![index].records[j].totalSamples=20
            }
        }
        let map=SpatialStructureP0Builder.build(document:doc).map
        XCTAssertGreaterThanOrEqual(map.wallSegments.count,1)
        XCTAssertTrue(map.wallSegments.allSatisfy {$0.lengthMeters>SpatialPlaneGrouping.offsetLimit && $0.startProvenance == .candidate})
        XCTAssertEqual(map.state,.incomplete)
        XCTAssertTrue(map.floorRegions.isEmpty)
    }
    func testMixedVoteWithOtherMaskDoesNotInventWalls() {
        var doc=fixture()
        for index in doc.indoorSemanticObservations!.indices {
            doc.indoorSemanticObservations![index].labels=Data(repeating:5,count:128*128)
            doc.indoorSemanticObservations![index].confidence=Data(repeating:230,count:128*128)
            for j in doc.indoorSemanticObservations![index].records.indices where doc.indoorSemanticObservations![index].records[j].surfaceID != "floor" {
                doc.indoorSemanticObservations![index].records[j].wallSupport=10
                doc.indoorSemanticObservations![index].records[j].otherOpposition=10
                doc.indoorSemanticObservations![index].records[j].totalSamples=20
            }
        }
        let map=SpatialStructureP0Builder.build(document:doc).map
        XCTAssertTrue(map.wallSegments.isEmpty)
        XCTAssertTrue(map.quality.rejections.keys.contains(where: {$0.contains("noLocalWallSupport") || $0.contains("otherSemantic")}))
    }
    func testElevatedWallPatchesStillYieldFiniteFloorLines() {
        var doc=fixture()
        for i in [1,2] {doc.surfaces[i].vertices=doc.surfaces[i].vertices.map {SpatialPoint(x:$0.x,y:$0.y+0.4,z:$0.z)}}
        refreshSyntheticWallEvidence(&doc)
        let map=SpatialStructureP0Builder.build(document:doc).map
        XCTAssertEqual(map.wallSegments.count,2);XCTAssertEqual(map.corners.count,1)
        XCTAssertTrue(map.wallSegments.allSatisfy {abs($0.start.y)<0.0001 && abs($0.end.y)<0.0001 && abs($0.lengthMeters-2)<0.0001})
        XCTAssertTrue(map.floorRegions.isEmpty);XCTAssertEqual(map.state,.incomplete)
    }
    func testCornerDistanceUsesEightCentimetersWithoutExtendingWallSpans() {
        for offset:Float in [0.07,0.09] {
            var doc=fixture()
            doc.surfaces[1].vertices=doc.surfaces[1].vertices.map {SpatialPoint(x:$0.x,y:$0.y,z:$0.z+offset)}
            refreshSyntheticWallEvidence(&doc)
            let map=SpatialStructureP0Builder.build(document:doc).map
            XCTAssertEqual(map.wallSegments.count,2)
            XCTAssertEqual(map.corners.count,offset<0.08 ? 1:0)
            XCTAssertTrue(map.floorRegions.isEmpty)
            XCTAssertEqual(map.wallSegments.first {$0.sourceSurfaceIDs.contains("wall-x")}!.lengthMeters,2,accuracy:0.0001)
        }
    }
    func testDiagonalEndpointGapCannotExceedEightCentimeters() {
        var doc=fixture()
        doc.surfaces[1].vertices=doc.surfaces[1].vertices.map {SpatialPoint(x:$0.x,y:$0.y,z:$0.z+0.07)}
        doc.surfaces[2].vertices=doc.surfaces[2].vertices.map {SpatialPoint(x:$0.x+0.07,y:$0.y,z:$0.z)}
        refreshSyntheticWallEvidence(&doc)
        let map=SpatialStructureP0Builder.build(document:doc).map
        XCTAssertEqual(map.wallSegments.count,2)
        XCTAssertTrue(map.corners.isEmpty) // sqrt(0.07²+0.07²) > 8cm, even though each line is within 8cm.
    }
    func testFinitePolygonDistancesAreThreeDimensionalAcrossAxesAndEdges() {
        let horizontal=SpatialSurface(id:"y",kind:"horizontal",vertices:[SpatialPoint(x:0,y:0,z:0),SpatialPoint(x:1,y:0,z:0),SpatialPoint(x:1,y:0,z:1),SpatialPoint(x:0,y:0,z:1)])
        let verticalX=SpatialSurface(id:"x",kind:"vertical",vertices:[SpatialPoint(x:0,y:0,z:0),SpatialPoint(x:0,y:1,z:0),SpatialPoint(x:0,y:1,z:1),SpatialPoint(x:0,y:0,z:1)])
        let verticalZ=SpatialSurface(id:"z",kind:"vertical",vertices:[SpatialPoint(x:0,y:0,z:0),SpatialPoint(x:1,y:0,z:0),SpatialPoint(x:1,y:1,z:0),SpatialPoint(x:0,y:1,z:0)])
        for (surface,p,expected) in [(horizontal,SpatialPoint(x:0.5,y:2,z:0.5),Float(2)),(horizontal,SpatialPoint(x:2,y:2,z:0.5),sqrt(Float(5))),(horizontal,SpatialPoint(x:1,y:2,z:0.5),Float(2)),(verticalX,SpatialPoint(x:2,y:0.5,z:0.5),Float(2)),(verticalZ,SpatialPoint(x:0.5,y:0.5,z:2),Float(2)),(verticalZ,SpatialPoint(x:2,y:2,z:2),sqrt(Float(6)))] {
            let distance=SpatialStructureP0Builder.polygonDistance(p,surface:surface)
            XCTAssertEqual(distance.distanceMeters ?? -1,expected,accuracy:0.0001)
            XCTAssertNil(distance.failure)
        }
    }
    func testNonPlanarDiagnosticIsInvalidAndRejectedCandidateKeepsPerPlaneSources() {
        var doc=fixture()
        var warped=doc.surfaces[0];warped.vertices[2].y=0.1
        let invalid=SpatialStructureP0Builder.polygonDistance(SpatialPoint(x:0,y:0,z:0),surface:warped)
        XCTAssertNil(invalid.distanceMeters);XCTAssertEqual(invalid.failure,"nonPlanarPolygon")
        doc.surfaces[1].vertices=doc.surfaces[1].vertices.map {SpatialPoint(x:$0.x,y:$0.y,z:$0.z+5)}
        refreshSyntheticWallEvidence(&doc)
        let built=SpatialStructureP0Builder.build(document:doc)
        XCTAssertTrue(built.map.corners.isEmpty)
        XCTAssertFalse(built.receipt.rejectedCornerCandidates.isEmpty)
        for candidate in built.receipt.rejectedCornerCandidates {
            XCTAssertEqual(candidate.planes.count,3)
            let wall=candidate.planes.first {$0.sourceSurfaceIDs.contains("wall-x")}
            XCTAssertEqual(wall?.nearestSourceSurfaceID,"wall-x")
            XCTAssertEqual(wall?.minimumDistanceMeters ?? -1,5,accuracy:0.0001)
        }
    }
    private func rectangleRoom(includeEast:Bool=true,splitWest:Bool=false)->SpatialScanDocument {
        var doc=fixture()
        func wall(_ id:String,_ vertices:[SpatialPoint])->SpatialSurface {
            SpatialSurface(id:id,kind:"vertical",vertices:vertices,observed:true,epoch:1)
        }
        var surfaces:[SpatialSurface]=[
            SpatialSurface(id:"floor",kind:"horizontal",vertices:[
                SpatialPoint(x:0,y:0,z:0),SpatialPoint(x:4,y:0,z:0),SpatialPoint(x:4,y:0,z:3),SpatialPoint(x:0,y:0,z:3)
            ],observed:true,epoch:1)
        ]
        if splitWest {
            surfaces.append(wall("west-a",[SpatialPoint(x:0,y:0,z:0),SpatialPoint(x:0,y:2,z:0),SpatialPoint(x:0,y:2,z:0.6),SpatialPoint(x:0,y:0,z:0.6)]))
            surfaces.append(wall("west-b",[SpatialPoint(x:0,y:0,z:2.4),SpatialPoint(x:0,y:2,z:2.4),SpatialPoint(x:0,y:2,z:3),SpatialPoint(x:0,y:0,z:3)]))
        } else {
            surfaces.append(wall("west",[SpatialPoint(x:0,y:0,z:0),SpatialPoint(x:0,y:2,z:0),SpatialPoint(x:0,y:2,z:3),SpatialPoint(x:0,y:0,z:3)]))
        }
        surfaces.append(wall("south",[SpatialPoint(x:0,y:0,z:0),SpatialPoint(x:0,y:2,z:0),SpatialPoint(x:4,y:2,z:0),SpatialPoint(x:4,y:0,z:0)]))
        surfaces.append(wall("north",[SpatialPoint(x:0,y:0,z:3),SpatialPoint(x:4,y:0,z:3),SpatialPoint(x:4,y:2,z:3),SpatialPoint(x:0,y:2,z:3)]))
        if includeEast {
            surfaces.append(wall("east",[SpatialPoint(x:4,y:0,z:3),SpatialPoint(x:4,y:2,z:3),SpatialPoint(x:4,y:2,z:0),SpatialPoint(x:4,y:0,z:0)]))
        }
        doc.surfaces=surfaces
        refreshSyntheticWallEvidence(&doc)
        return doc
    }
    func testRectangleWallIdentitiesCloseCircuitAndStayIncomplete() {
        let map=SpatialStructureP0Builder.build(document:rectangleRoom()).map
        XCTAssertEqual(Set(map.wallSegments.map(\.verticalTrackID)).count,4)
        XCTAssertEqual(map.corners.count,4)
        XCTAssertTrue(map.quality.circuitClosed)
        XCTAssertEqual(map.state,.incomplete)
        XCTAssertEqual(map.quality.observedCoverageLine,"看到的墙接上了")
        XCTAssertTrue(map.quality.message.contains("墙身份回路已闭合"))
        XCTAssertTrue(map.wallSegments.allSatisfy {$0.startProvenance == .candidate})
    }
    func testUShapeThreeWallsDoNotCloseCircuit() {
        let map=SpatialStructureP0Builder.build(document:rectangleRoom(includeEast:false)).map
        XCTAssertEqual(Set(map.wallSegments.map(\.verticalTrackID)).count,3)
        XCTAssertEqual(map.corners.count,2)
        XCTAssertFalse(map.quality.circuitClosed)
        XCTAssertEqual(map.state,.incomplete)
        XCTAssertEqual(map.quality.observedCoverageLine,"还没看全")
        XCTAssertTrue(map.floorRegions.isEmpty)
        XCTAssertTrue(map.quality.message.contains("墙身份回路未闭合"))
    }
    func testWallSpansShorterThanOffsetLimitAreNotPublished() {
        XCTAssertTrue(SpatialStructureWallLineSupport.intervals(origin:SIMD3(0,0,0),direction:SIMD3(1,0,0),up:SIMD3(0,1,0),lo:0,hi:0.05,views:[]).isEmpty)
        var doc=fixture()
        doc.surfaces[1].vertices=[SpatialPoint(x:0,y:0,z:0),SpatialPoint(x:0,y:2,z:0),SpatialPoint(x:0,y:2,z:0.02),SpatialPoint(x:0,y:0,z:0.02)]
        refreshSyntheticWallEvidence(&doc)
        let map=SpatialStructureP0Builder.build(document:doc).map
        XCTAssertFalse(map.wallSegments.contains {$0.sourceSurfaceIDs.contains("wall-x")})
        XCTAssertTrue(map.wallSegments.allSatisfy {$0.lengthMeters>SpatialPlaneGrouping.offsetLimit})
    }
    func testNineCentimeterMissingCornerDoesNotCloseCircuit() {
        var doc=rectangleRoom()
        doc.surfaces=doc.surfaces.map { surface in
            guard surface.id == "east" else {return surface}
            var shifted=surface
            shifted.vertices=surface.vertices.map {SpatialPoint(x:$0.x+0.09,y:$0.y,z:$0.z)}
            return shifted
        }
        refreshSyntheticWallEvidence(&doc)
        let map=SpatialStructureP0Builder.build(document:doc).map
        XCTAssertEqual(Set(map.wallSegments.map(\.verticalTrackID)).count,4)
        XCTAssertEqual(map.corners.count,2)
        XCTAssertFalse(map.quality.circuitClosed)
        XCTAssertEqual(map.state,.incomplete)
    }
    func testSofaGapKeepsOneWestWallAndRedGapWithoutInventingAWall() {
        let map=SpatialStructureP0Builder.build(document:rectangleRoom(splitWest:true)).map
        let westIDs=Set(map.wallSegments.filter {$0.sourceSurfaceIDs.contains("west-a") || $0.sourceSurfaceIDs.contains("west-b")}.map(\.verticalTrackID))
        XCTAssertEqual(westIDs.count,1)
        XCTAssertEqual(map.wallSegments.filter {westIDs.contains($0.verticalTrackID)}.count,2)
        let sofaGaps=map.gaps.filter {westIDs.contains($0.verticalTrackID) && SpatialScanGeometry.distance($0.start,$0.end)>1}
        XCTAssertEqual(sofaGaps.count,1)
        XCTAssertEqual(sofaGaps[0].reason.contains("not a doorway"),true)
        XCTAssertTrue(map.quality.circuitClosed)
        XCTAssertTrue(map.floorRegions.isEmpty)
        XCTAssertEqual(map.state,.incomplete)
        XCTAssertEqual(map.corners.count,4)
        #if DEBUG
        let rendered=expectation(description:"sofa-top")
        Task { @MainActor in
            do {try SpatialStructureP0Renderer.top(map:map,document:rectangleRoom(splitWest:true),output:URL(fileURLWithPath:"/tmp/vqasee-sofa-gap-top.png"))}
            catch {XCTFail("sofa top render failed: \(error)")}
            rendered.fulfill()
        }
        wait(for:[rendered],timeout:30)
        #endif
    }
    func testWhiteRoomMetadataPublishesLocalWallIntervalsWithoutClosingRoom() throws {
        let url=URL(fileURLWithPath:"/tmp/vqasee-white-room-document.json")
        try XCTSkipUnless(FileManager.default.fileExists(atPath:url.path),"white-room metadata is a local replay fixture")
        let document=try JSONDecoder().decode(SpatialScanDocument.self,from:Data(contentsOf:url))
        try XCTSkipUnless(document.id.uuidString=="9B1AF814-B8C4-4C84-8CE3-1C38AEBEF806")
        let map=SpatialStructureP0Builder.build(document:document).map
        XCTAssertEqual(map.state,.incomplete)
        XCTAssertTrue(map.floorRegions.isEmpty)
        XCTAssertTrue(map.quality.semanticFloorVerified)
        let total=map.wallSegments.reduce(Float(0)) {$0+$1.lengthMeters}
        XCTAssertGreaterThanOrEqual(map.wallSegments.count,2)
        XCTAssertGreaterThan(total,1)
        XCTAssertLessThan(total,8)
        let longest=try XCTUnwrap(map.wallSegments.max {$0.lengthMeters<$1.lengthMeters})
        XCTAssertGreaterThan(longest.lengthMeters,1)
        XCTAssertTrue(longest.sourceSurfaceIDs.contains("34AD2185-1D74-4009-A88D-44955B7FBE92"))
        XCTAssertTrue(map.quality.rejections.keys.contains(where: {$0.contains("otherSemantic") || $0.contains("insufficientHeight") || $0.contains("noLocalWallSupport")}))
    }
    func test14B7RGBStereoReplayWritesTopViewWithoutLiDAR() throws {
        let url=URL(fileURLWithPath:"/tmp/vqasee-iphone-14b7/document.json")
        try XCTSkipUnless(FileManager.default.fileExists(atPath:url.path),"14B7 metadata is a local replay fixture")
        let document=try JSONDecoder().decode(SpatialScanDocument.self,from:Data(contentsOf:url))
        try XCTSkipUnless(document.id.uuidString=="14B7CD10-E0E1-456D-8AA8-F07EF7C31AD6")
        XCTAssertTrue((document.depthObservations ?? []).isEmpty)
        XCTAssertTrue((document.relativeDepthObservations ?? []).isEmpty)
        let map=SpatialStructureP0Builder.build(document:document).map
        XCTAssertEqual(map.state,.incomplete)
        XCTAssertFalse(map.quality.circuitClosed)
        let stereo=map.wallSegments.filter {$0.verticalTrackID.hasPrefix("stereo-")}
        let occupancy=map.wallSegments.filter {$0.verticalTrackID.hasPrefix("occupancy-")}
        let payload:[String:Any]=[
            "scanID":document.id.uuidString,
            "wallCount":map.wallSegments.count,
            "stereoCount":stereo.count,
            "occupancyCount":occupancy.count,
            "stereoPointsRejection":map.quality.rejections["stereo:insufficientWallPoints"] ?? 0,
            "trackIDs":Array(Set(map.wallSegments.map(\.verticalTrackID))).sorted(),
            "cornerCount":map.corners.count,
            "circuitClosed":map.quality.circuitClosed,
            "message":map.quality.message,
            "wallSegments":map.wallSegments.map {[
                "id":$0.id,"track":$0.verticalTrackID,"lengthMeters":$0.lengthMeters,
                "start":["x":$0.start.x,"z":$0.start.z],"end":["x":$0.end.x,"z":$0.end.z]
            ]}
        ]
        try JSONSerialization.data(withJSONObject:payload,options:[.prettyPrinted,.sortedKeys]).write(to:URL(fileURLWithPath:"/tmp/vqasee-14b7-rgb-stereo-map.json"),options:.atomic)
        #if DEBUG
        let rendered=expectation(description:"14b7-stereo-top")
        Task { @MainActor in
            do {try SpatialStructureP0Renderer.top(map:map,document:document,output:URL(fileURLWithPath:"/tmp/vqasee-14b7-rgb-stereo-top.png"))}
            catch {XCTFail("14b7 stereo top render failed: \(error)")}
            rendered.fulfill()
        }
        wait(for:[rendered],timeout:30)
        #endif
    }
    func test4D5BC0B7WallIdentityReplayWritesTopView() throws {
        let url=URL(fileURLWithPath:"/tmp/vqasee-iphone-4d5/document.json")
        try XCTSkipUnless(FileManager.default.fileExists(atPath:url.path),"4D5 metadata is a local replay fixture")
        let document=try JSONDecoder().decode(SpatialScanDocument.self,from:Data(contentsOf:url))
        try XCTSkipUnless(document.id.uuidString=="4D5BC0B7-A6BF-4A7F-BB86-AAE945CC46B5")
        let map=SpatialStructureP0Builder.build(document:document).map
        XCTAssertEqual(map.state,.incomplete)
        XCTAssertTrue(map.quality.geometricFloorDatum)
        let payload:[String:Any]=[
            "scanID":document.id.uuidString,
            "wallCount":map.wallSegments.count,
            "trackIDs":Array(Set(map.wallSegments.map(\.verticalTrackID))).sorted(),
            "cornerCount":map.corners.count,
            "gapCount":map.gaps.count,
            "circuitClosed":map.quality.circuitClosed,
            "verticalTrackCount":map.quality.verticalTrackCount,
            "message":map.quality.message,
            "rejections":map.quality.rejections,
            "gaps":map.gaps.map {["track":$0.verticalTrackID,"reason":$0.reason,"length":SpatialScanGeometry.distance($0.start,$0.end),
                "start":["x":$0.start.x,"z":$0.start.z],"end":["x":$0.end.x,"z":$0.end.z]]},
            "wallSegments":map.wallSegments.map {[
                "id":$0.id,"track":$0.verticalTrackID,"lengthMeters":$0.lengthMeters,"sourceSurfaceIDs":$0.sourceSurfaceIDs,
                "start":["x":$0.start.x,"y":$0.start.y,"z":$0.start.z],
                "end":["x":$0.end.x,"y":$0.end.y,"z":$0.end.z]
            ]}
        ]
        try JSONSerialization.data(withJSONObject:payload,options:[.prettyPrinted,.sortedKeys]).write(to:URL(fileURLWithPath:"/tmp/vqasee-4d5-wall-identity-map.json"),options:.atomic)
        #if DEBUG
        let rendered=expectation(description:"4d5-top")
        Task { @MainActor in
            do {try SpatialStructureP0Renderer.top(map:map,document:document,output:URL(fileURLWithPath:"/tmp/vqasee-4d5-wall-identity-top.png"))}
            catch {XCTFail("4d5 top render failed: \(error)")}
            rendered.fulfill()
        }
        wait(for:[rendered],timeout:30)
        #endif
    }
    func testD3A612BDGeometricDatumPublishesWallFeetOnLowestPlane() throws {
        let url=URL(fileURLWithPath:"/tmp/vqasee-latest-scan/document.json")
        try XCTSkipUnless(FileManager.default.fileExists(atPath:url.path),"D3A612BD metadata is a local replay fixture")
        let document=try JSONDecoder().decode(SpatialScanDocument.self,from:Data(contentsOf:url))
        try XCTSkipUnless(document.id.uuidString=="D3A612BD-86CD-4728-9047-86B348FB372E")
        let first=SpatialStructureP0Builder.build(document:document).map
        let second=SpatialStructureP0Builder.build(document:document).map
        XCTAssertEqual(first.wallSegments,second.wallSegments)
        XCTAssertEqual(first.corners,second.corners)
        XCTAssertTrue(first.quality.geometricFloorDatum)
        XCTAssertFalse(first.quality.semanticFloorVerified)
        XCTAssertFalse(first.wallSegments.isEmpty)
        XCTAssertEqual(first.state,.incomplete)
        XCTAssertTrue(first.floorRegions.isEmpty)
        let epoch=document.keyframes.last?.pose.epoch ?? document.surfaces.map(\.epoch).max() ?? 0
        let horizontals=document.surfaces.filter {$0.observed && $0.epoch == epoch && $0.kind == "horizontal" && $0.vertices.count>=3}
        func medianY(_ surface:SpatialSurface)->Float {
            let ys=surface.vertices.map(\.y).filter(\.isFinite).sorted()
            return ys.isEmpty ? Float.infinity : ys[ys.count/2]
        }
        let lowest=try XCTUnwrap(horizontals.map(medianY).min())
        XCTAssertTrue(first.wallSegments.allSatisfy {seg in
            abs(seg.start.y-lowest)<=SpatialPlaneGrouping.offsetLimit && abs(seg.end.y-lowest)<=SpatialPlaneGrouping.offsetLimit
        })
        XCTAssertTrue(first.wallSegments.allSatisfy {seg in
            horizontals.filter {medianY($0) > lowest+0.2}.allSatisfy {table in
                abs(seg.start.y-medianY(table))>SpatialPlaneGrouping.offsetLimit && abs(seg.end.y-medianY(table))>SpatialPlaneGrouping.offsetLimit
            }
        })
        let payload:[String:Any]=[
            "scanID":document.id.uuidString,
            "wallCount":first.wallSegments.count,
            "cornerCount":first.corners.count,
            "floorRegionCount":first.floorRegions.count,
            "semanticFloorVerified":first.quality.semanticFloorVerified,
            "geometricFloorDatum":first.quality.geometricFloorDatum,
            "circuitClosed":first.quality.circuitClosed,
            "verticalTrackCount":first.quality.verticalTrackCount,
            "gapCount":first.gaps.count,
            "lowestHorizontalY":lowest,
            "message":first.quality.message,
            "rejections":first.quality.rejections,
            "wallSegments":first.wallSegments.map {[
                "id":$0.id,"lengthMeters":$0.lengthMeters,
                "start":["x":$0.start.x,"y":$0.start.y,"z":$0.start.z],
                "end":["x":$0.end.x,"y":$0.end.y,"z":$0.end.z],
                "sourceSurfaceIDs":$0.sourceSurfaceIDs
            ]}
        ]
        try JSONSerialization.data(withJSONObject:payload,options:[.prettyPrinted,.sortedKeys]).write(to:URL(fileURLWithPath:"/tmp/vqasee-d3-geometric-datum-map.json"),options:.atomic)
        #if DEBUG
        let rendered=expectation(description:"d3-top")
        Task { @MainActor in
            do {try SpatialStructureP0Renderer.top(map:first,document:document,output:URL(fileURLWithPath:"/tmp/vqasee-d3-geometric-datum-top.png"))}
            catch {XCTFail("top render failed: \(error)")}
            rendered.fulfill()
        }
        wait(for:[rendered],timeout:30)
        #endif
    }
}
