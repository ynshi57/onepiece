import Foundation
import simd

/// Bounded plane-intersection candidates, not a solved room, doorway or measured wall length.
nonisolated enum SpatialStructureP0Builder {
    struct Result: Sendable, Equatable { var map: SpatialStructureMap; var receipt: SpatialStructureP0Receipt }
    struct Config: Sendable, Equatable {
        var trackAssociation = SpatialPlaneTrackAssociation.Config()
        var revision = 1
        var poseRevision = 0
    }
    static func build(document: SpatialScanDocument, config: Config = Config()) -> Result {
        let epoch=document.keyframes.last?.pose.epoch ?? document.surfaces.map(\.epoch).max() ?? 0
        let scoped=document.surfaces.filter {$0.observed && $0.epoch == epoch}
        let semanticRows=(document.indoorSemanticObservations ?? []).filter {row in
            guard row.status == .processed,row.failure == nil,let taxonomy=row.taxonomy,taxonomy.verified,
                  taxonomy.configSHA256.count == 64,taxonomy.configSHA256.allSatisfy(\.isHexDigit),
                  taxonomy.floorLabelID>=0,taxonomy.floorLabelID<150,taxonomy.wallLabelID>=0,taxonomy.wallLabelID<150,
                  taxonomy.floorLabelID != taxonomy.wallLabelID,row.width == 128,row.height == 128,
                  row.labels.count == 128*128,row.confidence.count == 128*128 else {return false}
            return row.records.allSatisfy {$0.modelID == row.modelID && $0.configSHA256 == taxonomy.configSHA256 && $0.frameID == row.frameID && $0.frameTime == row.frameTime && $0.epoch == row.epoch}
        }
        let records=SpatialVisibleFloorProjection.validatedRecords(frames:document.keyframes,rows:semanticRows)
        func semantic(_ s:SpatialSurface) -> SpatialSurfaceSemanticEvidence.Semantic {SpatialSurfaceSemanticEvidence.resolve(surface:s,records:records)}
        let tracks=SpatialPlaneTrackAssociation.associate(surfaces:scoped,config:config.trackAssociation)
        let vertical=tracks.filter {$0.kind == "vertical"}, horizontal=tracks.filter {$0.kind == "horizontal"}
        var map=SpatialStructureMap(scanID:document.id,epoch:epoch,revision:config.revision,poseRevision:config.poseRevision,state:.incomplete)
        map.quality.verticalTrackCount=vertical.count;map.quality.horizontalTrackCount=horizontal.count
        let semanticFloors=horizontal.filter { track in
            let members=scoped.filter {track.memberSurfaceIDs.contains($0.id)}
            return abs(track.normal.y)>=0.99 && !members.isEmpty && members.allSatisfy {semantic($0) == .floor}
        }
        let cameraHeights=document.keyframes.filter {$0.pose.epoch == epoch && $0.pose.position.isFinite}.map {$0.pose.position.y}.sorted()
        let cameraY=cameraHeights.isEmpty ? nil : cameraHeights[cameraHeights.count/2]
        let datums=horizontal.compactMap { track -> (track:SpatialPlaneTrackAssociation.Track,height:Float)? in
            let members=scoped.filter {track.memberSurfaceIDs.contains($0.id)}
            guard abs(track.normal.y)>=0.99, !members.isEmpty else {return nil}
            let ys=members.flatMap {$0.vertices.map(\.y)}.filter(\.isFinite).sorted()
            guard !ys.isEmpty else {return nil}
            let height=ys[ys.count/2]
            if let cameraY, height>=cameraY {return nil}
            let area=members.reduce(Float(0)) {$0+polygonArea($1.vertices)}
            guard area>0.02 else {return nil}
            return (track,height)
        }.sorted {
            if $0.height != $1.height {return $0.height < $1.height}
            return $0.track.memberSurfaceIDs.sorted().joined(separator:"|") < $1.track.memberSurfaceIDs.sorted().joined(separator:"|")
        }
        let lineViews=SpatialStructureWallLineSupport.views(frames:document.keyframes,rows:semanticRows)
        // Whole-surface .other stays furniture. Unknown mixed planes can still yield local wall intervals.
        let walls=vertical.filter {track in
            let members=scoped.filter {track.memberSurfaceIDs.contains($0.id)}
            guard !members.isEmpty else {return false}
            let labels=members.map(semantic)
            if labels.allSatisfy({$0 == .other}) {
                map.quality.rejections["wallTrack:\(track.id):otherSemantic",default:0]+=1;return false
            }
            guard members.allSatisfy({surface in
                let ys=surface.vertices.map(\.y)
                return (ys.max() ?? 0)-(ys.min() ?? 0)>=SpatialPlaneGrouping.tallHeight
            }) else {map.quality.rejections["wallTrack:\(track.id):insufficientHeight",default:0]+=1;return false}
            return true
        }.sorted {$0.memberSurfaceIDs.sorted().joined(separator:"|") < $1.memberSurfaceIDs.sorted().joined(separator:"|")}
        map.quality.semanticFloorVerified = !semanticFloors.isEmpty
        if !map.quality.semanticFloorVerified {map.quality.rejections["noSemanticFloor",default:0]+=1}
        guard let floor=datums.first?.track else {
            map.quality.geometricFloorDatum=false
            map.quality.rejections["noGeometricFloorDatum",default:0]+=1
            map.quality.message="缺少重力对齐的最低水平面基准；未形成地面闭环"
            return finish(document,map)
        }
        map.quality.geometricFloorDatum=true
        for (index,track) in walls.enumerated() {
            // Solve two planes exactly; projecting the anchor alone would drift off a tilted wall.
            let n=floor.normal,w=track.normal,tangent=w-simd_dot(w,n)*n
            let denominator=simd_dot(tangent,tangent)
            guard denominator>0.000001 else {map.quality.rejections["wallTrack:\(track.id):parallelFloor",default:0]+=1;continue}
            let projected=track.anchor-simd_dot(track.anchor-floor.anchor,n)*n
            let origin=projected-simd_dot(projected-track.anchor,w)/denominator*tangent
            let direction=simd_normalize(simd_cross(n,w))
            var up=simd_cross(direction,w)
            if simd_length(up)<=0.000001 {map.quality.rejections["wallTrack:\(track.id):parallelFloor",default:0]+=1;continue}
            up=simd_normalize(up); if up.y<0 {up = -up}
            let members=scoped.filter {track.memberSurfaceIDs.contains($0.id)}
            let confirmedWall=members.allSatisfy {semantic($0) == .wall}
            let geometric=wallSegments(origin:origin,direction:direction,track:track,surfaces:scoped,index:index,frameIDs:[])
            let search=geometric.segments.reduce(into:(lo:Float.infinity,hi:-Float.infinity)) {span,segment in
                for endpoint in [segment.start,segment.end] {
                    let t=simd_dot(vector(endpoint)-origin,direction)
                    span.lo=min(span.lo,t);span.hi=max(span.hi,t)
                }
            }
            let spans: (segments:[SpatialStructureMap.WallSegment],gaps:[SpatialStructureMap.Gap])
            if confirmedWall {
                let frameIDs=Array(Set(records.filter {record in
                    track.memberSurfaceIDs.contains(record.surfaceID) && SpatialSurfaceSemanticEvidence.vote(record) == .wall
                }.map(\.frameID))).sorted()
                spans=wallSegments(origin:origin,direction:direction,track:track,surfaces:scoped,index:index,frameIDs:frameIDs)
            } else {
                guard search.lo.isFinite, search.hi>search.lo else {
                    map.quality.rejections["wallTrack:\(track.id):insufficientFiniteSpan",default:0]+=1;continue
                }
                let supported=SpatialStructureWallLineSupport.intervals(origin:origin,direction:direction,up:up,lo:search.lo,hi:search.hi,views:lineViews)
                guard !supported.isEmpty else {map.quality.rejections["wallTrack:\(track.id):noLocalWallSupport",default:0]+=1;continue}
                let ids=track.memberSurfaceIDs.sorted()
                let segments=supported.enumerated().map {i,range in
                    SpatialStructureMap.WallSegment(id:"wall-p0-\(index)-\(i)",verticalTrackID:track.id,
                        start:point(origin+direction*range.lo),end:point(origin+direction*range.hi),
                        startProvenance:.candidate,endProvenance:.candidate,lengthMeters:range.hi-range.lo,
                        sourceSurfaceIDs:ids,frameIDs:range.frameIDs)
                }
                var gaps:[SpatialStructureMap.Gap]=[]
                for i in 1..<supported.count {
                    gaps.append(.init(verticalTrackID:track.id,start:point(origin+direction*supported[i-1].hi),
                        end:point(origin+direction*supported[i].lo),sourceSurfaceIDs:ids,
                        frameIDs:Array(Set(supported[i-1].frameIDs+supported[i].frameIDs)).sorted()))
                }
                spans=(segments,gaps)
            }
            guard !spans.segments.isEmpty else {map.quality.rejections["wallTrack:\(track.id):insufficientFiniteSpan",default:0]+=1;continue}
            guard spans.segments.flatMap({[$0.start,$0.end]}).allSatisfy({p in
                p.isFinite && abs(simd_dot(vector(p)-floor.anchor,n))<=SpatialPlaneGrouping.offsetLimit &&
                abs(simd_dot(vector(p)-track.anchor,w))<=SpatialPlaneGrouping.offsetLimit
            }) else {map.quality.rejections["wallTrack:\(track.id):offPlaneEndpoint",default:0]+=1;continue}
            map.wallSegments+=spans.segments;map.gaps+=spans.gaps
        }
        let stereo=SpatialStructureSemanticStereo.publish(
            frames:document.keyframes,views:lineViews,floor:floor,existing:map.wallSegments,startIndex:walls.count
        )
        map.wallSegments+=stereo.segments;map.gaps+=stereo.gaps
        for (reason,count) in stereo.rejections {map.quality.rejections[reason,default:0]+=count}
        let occupancy=SpatialStructureBEVOccupancy.publish(
            frames:document.keyframes,views:lineViews,floor:floor,existing:map.wallSegments,startIndex:walls.count+stereo.tracks.count
        )
        map.wallSegments+=occupancy.segments;map.gaps+=occupancy.gaps
        for (reason,count) in occupancy.rejections {map.quality.rejections[reason,default:0]+=count}
        let wallTracks=walls+stereo.tracks+occupancy.tracks
        // Corners annotate finite published lines; failure here never suppresses wall segments.
        for i in wallTracks.indices {for j in wallTracks.indices where j>i {
            let a=wallTracks[i],b=wallTracks[j]
            let aSegments=map.wallSegments.filter {$0.verticalTrackID == a.id}
            let bSegments=map.wallSegments.filter {$0.verticalTrackID == b.id}
            guard !aSegments.isEmpty,!bSegments.isEmpty else {continue}
            guard let p=try? SpatialPlaneCornerSolver.intersect(plane(a),plane(floor),plane(b)),p.isFinite else {map.quality.rejections["cornerIntersectionFailed",default:0]+=1;continue}
            let qa=vector(p)
            func pointDistance(_ q:SIMD3<Float>,_ segment:SpatialStructureMap.WallSegment)->Float {
                let start=vector(segment.start),d=vector(segment.end)-start
                let t=max(0,min(1,simd_dot(q-start,d)/simd_dot(d,d)))
                return simd_distance(q,start+t*d)
            }
            func distance(_ segment:SpatialStructureMap.WallSegment)->Float {pointDistance(qa,segment)}
            guard let sa=aSegments.min(by:{distance($0)<distance($1)}),let sb=bSegments.min(by:{distance($0)<distance($1)}) else {continue}
            // Coplanar segments either contain their line intersection or are nearest at an endpoint.
            let segmentDistance:Float = distance(sa)<0.000001 && distance(sb)<0.000001 ? 0 :
                min(pointDistance(vector(sa.start),sb),pointDistance(vector(sa.end),sb),pointDistance(vector(sb.start),sa),pointDistance(vector(sb.end),sa))
            guard segmentDistance<=SpatialPlaneGrouping.offsetLimit,
                  distance(sa)<=SpatialPlaneGrouping.offsetLimit,distance(sb)<=SpatialPlaneGrouping.offsetLimit else {
                map.quality.rejections["cornerOutsideFiniteSegments",default:0]+=1
                let distances=[floor,a,b].map {track -> SpatialStructureMap.PlaneCoverage in
                    let patches=scoped.filter {track.memberSurfaceIDs.contains($0.id)}.map {polygonDistance(p,surface:$0)}
                    let nearest=patches.filter {$0.distanceMeters != nil}.min {$0.distanceMeters! < $1.distanceMeters!}
                    return .init(trackID:track.id,sourceSurfaceIDs:track.memberSurfaceIDs.sorted(),minimumDistanceMeters:nearest?.distanceMeters,nearestSourceSurfaceID:nearest?.sourceSurfaceID,patches:patches)
                }
                map.rejectedCornerCandidates.append(.init(intersection:p,reason:"cornerOutsideFiniteSegments",planes:distances))
                continue
            }
            let visible=document.keyframes.filter {$0.pose.epoch == epoch && sees(p,frame:$0)}
            let selected=selectKeyframes(visible,corner:p)
            map.quality.visibleFrameCount=max(map.quality.visibleFrameCount,visible.count)
            if selected.count<12 {map.quality.rejections[visible.isEmpty ? "noVisibleCornerFrames":"insufficientVisibleCornerFrames",default:0]+=1}
            let ids=Array(Set(floor.memberSurfaceIDs+sa.sourceSurfaceIDs+sb.sourceSurfaceIDs)).sorted()
            let residual=[plane(floor),plane(a),plane(b)].map {abs($0.signedDistance(qa))}.max() ?? 0
            map.corners.append(.init(id:"corner-p0-\(i)-\(j)",position:p,provenance:.candidate,planeTrackIDs:[a.id,b.id,floor.id],planeIntersectionResidualMeters:residual,sourceSurfaceIDs:ids,frameIDs:selected))
        }}
        map.sourceSurfaceIDs=Array(Set(map.wallSegments.flatMap(\.sourceSurfaceIDs)+floor.memberSurfaceIDs)).sorted()
        map.frameIDs=Array(Set(map.wallSegments.flatMap(\.frameIDs)+map.corners.flatMap(\.frameIDs))).sorted()
        let regionResult=SpatialStructureFloorRegionBuilder.build(wallSegments:map.wallSegments,floorSurfaces:scoped.filter {floor.memberSurfaceIDs.contains($0.id)},floorTrackID:floor.id)
        map.floorRegions=regionResult.regions;map.gaps+=regionResult.gaps
        for (reason,count) in regionResult.rejections {map.quality.rejections["floorRegion:"+reason,default:0]+=count}
        map.quality.circuitClosed=wallIdentityCircuitClosed(segments:map.wallSegments,corners:map.corners)
        map.state = .incomplete
        map.quality.message=map.wallSegments.isEmpty ? "暂无已确认墙面的有限交线" : "墙地交线候选；尚未照片匹配、优化或独立测量"
        if !map.quality.semanticFloorVerified {map.quality.message += "；语义地面未确认"}
        if map.floorRegions.isEmpty {map.quality.message += "；未形成地面闭环"}
        if !map.wallSegments.isEmpty {map.quality.message += map.quality.circuitClosed ? "；墙身份回路已闭合":"；墙身份回路未闭合"}
        return finish(document,map)
    }
    /// A single local temporal window, with every admitted frame seeing the same candidate corner.
    static func selectKeyframes(_ keyframes:[SpatialKeyframe],corner:SpatialPoint? = nil,minimum:Int=12,maximum:Int=20) -> [String] {
        guard let corner,maximum>0 else {return []}
        let epoch=keyframes.last?.pose.epoch,counts=Dictionary(grouping:keyframes,by:\.id).mapValues(\.count)
        let visible=keyframes.filter {$0.pose.epoch == epoch && counts[$0.id] == 1 && sees(corner,frame:$0)}.sorted {$0.time == $1.time ? $0.id < $1.id:$0.time < $1.time}
        guard visible.count>maximum else {return visible.map(\.id)}
        var start=0,span=Double.infinity
        for i in 0...visible.count-maximum {
            let candidate=visible[i+maximum-1].time-visible[i].time
            if candidate<span {span=candidate;start=i}
        }
        return Array(visible[start..<start+maximum]).map(\.id)
    }
    private static func sees(_ point:SpatialPoint,frame:SpatialKeyframe) -> Bool {
        guard frame.time.isFinite,frame.pose.time == frame.time,frame.pose.position.isFinite,
              frame.cameraToWorld.count == 16,
              abs(frame.cameraToWorld[12]-frame.pose.position.x)<0.001,abs(frame.cameraToWorld[13]-frame.pose.position.y)<0.001,abs(frame.cameraToWorld[14]-frame.pose.position.z)<0.001,
              let projection=SpatialObservationGeometry.project(position:point,frame:frame) else {return false}
        return projection.depth<=SpatialObservationGeometry.maximumDepthMeters && projection.pixel.allSatisfy {$0>=0 && $0<=1}
    }
    private static func finish(_ document:SpatialScanDocument,_ map:SpatialStructureMap) -> Result {
        Result(map:map,receipt:SpatialStructureP0Receipt(scanID:document.id,epoch:map.epoch,structureRevision:map.revision,keyframeIDs:map.frameIDs,corners:map.corners,floorRegions:map.floorRegions,wallSegments:map.wallSegments,tapeSegments:map.wallSegments.prefix(2).map {SpatialStructureP0Receipt.TapeSegment(wallSegmentID:$0.id,measuredMeters:nil,note:"独立实测未提供；候选跨度不是房间尺寸")},builderMessage:map.quality.message,quality:map.quality,sourceSurfaceIDs:map.sourceSurfaceIDs,gaps:map.gaps,rejectedCornerCandidates:map.rejectedCornerCandidates))
    }
    private static func vector(_ p:SpatialPoint)->SIMD3<Float> {SIMD3(p.x,p.y,p.z)}
    private static func point(_ p:SIMD3<Float>)->SpatialPoint {SpatialPoint(x:p.x,y:p.y,z:p.z)}
    private static func plane(_ t:SpatialPlaneTrackAssociation.Track)->SpatialPlaneCornerSolver.Plane {.init(normal:t.normal,offset:-simd_dot(t.normal,t.anchor))}
    /// All published wall identities are degree-2 in the corner graph and walk as one cycle. Gaps on a wall do not invent a second wall.
    static func wallIdentityCircuitClosed(segments:[SpatialStructureMap.WallSegment],corners:[SpatialStructureMap.Corner])->Bool {
        let ids=Array(Set(segments.map(\.verticalTrackID))).sorted()
        guard ids.count>=3 else {return false}
        var neighbors:[String:Set<String>]=Dictionary(uniqueKeysWithValues:ids.map {($0,[])})
        for corner in corners {
            let walls=corner.planeTrackIDs.filter {ids.contains($0)}
            guard walls.count == 2,walls[0] != walls[1] else {continue}
            neighbors[walls[0],default:[]].insert(walls[1])
            neighbors[walls[1],default:[]].insert(walls[0])
        }
        guard ids.allSatisfy({neighbors[$0]?.count == 2}) else {return false}
        let start=ids[0]
        var previous=start
        var current=neighbors[start]!.sorted()[0]
        var seen:Set<String>=[start]
        while !seen.contains(current) {
            seen.insert(current)
            guard let next=neighbors[current]?.first(where:{$0 != previous}) else {return false}
            previous=current
            current=next
        }
        return current == start && seen.count == ids.count
    }
    /// Degenerate slivers are not a support plane. This is not a room-area gate.
    private static func polygonArea(_ vertices:[SpatialPoint])->Float {
        guard vertices.count>=3,vertices.allSatisfy(\.isFinite) else {return 0}
        var x:Float=0,y:Float=0,z:Float=0
        for i in vertices.indices {let a=vertices[i],b=vertices[(i+1)%vertices.count];x+=(a.y-b.y)*(a.z+b.z);y+=(a.z-b.z)*(a.x+b.x);z+=(a.x-b.x)*(a.y+b.y)}
        let area=sqrt(x*x+y*y+z*z)/2
        return area.isFinite ? area : 0
    }
    /// Exact 3D point-to-finite-planar-polygon distance; diagnostics never alter the publication gate.
    static func polygonDistance(_ p:SpatialPoint,surface:SpatialSurface)->SpatialStructureMap.PatchDistance {
        func invalid(_ reason:String)->SpatialStructureMap.PatchDistance {.init(sourceSurfaceID:surface.id,distanceMeters:nil,failure:reason)}
        guard p.isFinite,surface.vertices.count>=3,surface.vertices.allSatisfy(\.isFinite) else {return invalid("invalidVertices")}
        let vertices=surface.vertices.map(vector),origin=vertices[0],q=vector(p)
        var normal=SIMD3<Float>.zero
        for i in vertices.indices {normal+=simd_cross(vertices[i]-origin,vertices[(i+1)%vertices.count]-origin)}
        guard simd_length(normal)>0.000001 else {return invalid("degeneratePolygon")}
        normal=simd_normalize(normal)
        // Numerical planarity only: a warped polygon has no unique plane interior.
        guard vertices.allSatisfy({abs(simd_dot($0-origin,normal))<=0.0001}) else {return invalid("nonPlanarPolygon")}
        let signed=simd_dot(q-origin,normal),projected=q-signed*normal
        let axis=abs(normal.x)>abs(normal.y) ? (abs(normal.x)>abs(normal.z) ? 0:2):(abs(normal.y)>abs(normal.z) ? 1:2)
        func flat(_ v:SIMD3<Float>)->SIMD2<Float> {axis == 0 ? SIMD2(v.y,v.z):axis == 1 ? SIMD2(v.x,v.z):SIMD2(v.x,v.y)}
        let x=flat(projected),polygon=vertices.map(flat)
        var inside=false,edgeDistance=Float.infinity
        for i in vertices.indices {
            let a=vertices[i],b=vertices[(i+1)%vertices.count],d=b-a
            let denominator=simd_dot(d,d)
            let t=denominator>0 ? max(0,min(1,simd_dot(q-a,d)/denominator)):0
            edgeDistance=min(edgeDistance,simd_distance(q,a+t*d))
            let u=polygon[i],v=polygon[(i+1)%polygon.count]
            if (u.y>x.y) != (v.y>x.y),x.x<(v.x-u.x)*(x.y-u.y)/(v.y-u.y)+u.x {inside.toggle()}
        }
        let distance=inside ? abs(signed):edgeDistance
        guard distance.isFinite else {return invalid("nonFiniteDistance")}
        return .init(sourceSurfaceID:surface.id,distanceMeters:distance,failure:nil)
    }

    /// Only overlapping per-patch intervals merge. Disjoint observations retain an explicit unknown gap.
    private static func wallSegments(origin:SIMD3<Float>,direction:SIMD3<Float>,track:SpatialPlaneTrackAssociation.Track,surfaces:[SpatialSurface],index:Int,frameIDs:[String])->(segments:[SpatialStructureMap.WallSegment],gaps:[SpatialStructureMap.Gap]) {
        var intervals:[(lo:Float,hi:Float,ids:[String])]=[]
        for surface in surfaces where track.memberSurfaceIDs.contains(surface.id) {
            let values=surface.vertices.map {simd_dot(vector($0)-origin,direction)}
            if let lo=values.min(),let hi=values.max(),hi-lo>SpatialPlaneGrouping.offsetLimit {intervals.append((lo,hi,[surface.id]))}
        }
        intervals.sort {$0.lo<$1.lo};var merged:[(lo:Float,hi:Float,ids:[String])]=[]
        for value in intervals {
            if let last=merged.last,value.lo<=last.hi {merged[merged.count-1]=(last.lo,max(last.hi,value.hi),Array(Set(last.ids+value.ids)).sorted())}
            else {merged.append(value)}
        }
        let segments=merged.enumerated().map {i,range in SpatialStructureMap.WallSegment(id:"wall-p0-\(index)-\(i)",verticalTrackID:track.id,start:point(origin+direction*range.lo),end:point(origin+direction*range.hi),startProvenance:.candidate,endProvenance:.candidate,lengthMeters:range.hi-range.lo,sourceSurfaceIDs:range.ids,frameIDs:frameIDs)}
        var gaps:[SpatialStructureMap.Gap]=[]
        for i in 1..<max(1,merged.count) {gaps.append(.init(verticalTrackID:track.id,start:point(origin+direction*merged[i-1].hi),end:point(origin+direction*merged[i].lo),sourceSurfaceIDs:Array(Set(merged[i-1].ids+merged[i].ids)).sorted(),frameIDs:frameIDs))}
        return (segments,gaps)
    }
}
