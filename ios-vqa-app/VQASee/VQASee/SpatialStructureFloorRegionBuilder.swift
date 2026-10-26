import Foundation
import simd

/// Follows existing wall edges only. Endpoint snapping is candidate connectivity, never an observed corner.
nonisolated enum SpatialStructureFloorRegionBuilder {
    struct Result: Sendable, Equatable {
        var regions:[SpatialStructureMap.FloorRegion]=[]
        var rejections:[String:Int]=[:]
        var gaps:[SpatialStructureMap.Gap]=[]
    }
    private struct Ring {var points:[SpatialPoint];var edges:[Int]}
    /// Caller supplies the geometric floor-datum surfaces. Missing floor class does not invent a ring.
    static func build(wallSegments:[SpatialStructureMap.WallSegment],floorSurfaces:[SpatialSurface],floorTrackID:String?=nil)->Result {
        var result=Result()
        guard let floor=floorSurfaces.first,!floorSurfaces.isEmpty,
              floorSurfaces.allSatisfy({$0.observed && $0.kind == "horizontal" && $0.epoch == floor.epoch && $0.vertices.count>=3 && $0.vertices.allSatisfy(\.isFinite)}),
              let observation=SpatialPlaneTrackAssociation.observations(from:[floor]).first,abs(observation.normal.y)>=0.99,
              floorSurfaces.flatMap(\.vertices).allSatisfy({abs(simd_dot(SIMD3($0.x,$0.y,$0.z)-observation.centroid,observation.normal))<=SpatialPlaneGrouping.offsetLimit}) else {result.rejections["invalidOrMissingFloorPlane"]=1;return result}
        guard Set(wallSegments.map(\.id)).count == wallSegments.count else {result.rejections["duplicateWallSegmentID"]=1;return result}
        guard !wallSegments.isEmpty else {result.rejections["noWallSegments"]=1;return result}
        let sourceEndpoints=wallSegments.flatMap {[$0.start,$0.end]}
        guard sourceEndpoints.allSatisfy({$0.isFinite && abs(simd_dot(SIMD3($0.x,$0.y,$0.z)-observation.centroid,observation.normal))<=SpatialPlaneGrouping.offsetLimit}) else {result.rejections["invalidOrOffFloorEndpoint"]=1;return result}
        // Verify the original 8 cm gate first, then place candidate connectivity on one floor plane.
        let endpoints=sourceEndpoints.map {point -> SpatialPoint in
            let p=SIMD3(point.x,point.y,point.z),q=p-simd_dot(p-observation.centroid,observation.normal)*observation.normal
            return SpatialPoint(x:q.x,y:q.y,z:q.z)
        }
        var parent=Array(endpoints.indices)
        func root(_ value:Int)->Int {var v=value;while parent[v] != v {v=parent[v]};return v}
        for i in endpoints.indices {for j in endpoints.indices where j>i {
            if SpatialScanGeometry.distance(endpoints[i],endpoints[j])<=SpatialPlaneGrouping.offsetLimit {parent[root(j)]=root(i)}
        }}
        var clusters:[Int:[Int]]=[:]
        for i in endpoints.indices {clusters[root(i),default:[]].append(i)}
        let keys=clusters.keys.sorted();var nodes:[SpatialPoint]=[],membership:[Int:Int]=[:]
        for key in keys {
            let indices=clusters[key]!
            // No transitive chain may silently stretch the original 8 cm connection gate.
            guard indices.allSatisfy({a in indices.allSatisfy {SpatialScanGeometry.distance(endpoints[a],endpoints[$0])<=SpatialPlaneGrouping.offsetLimit}}) else {result.rejections["transitiveEndpointCluster"]=1;return result}
            let n=Float(indices.count)
            nodes.append(indices.reduce(SpatialPoint(x:0,y:0,z:0)) {SpatialPoint(x:$0.x+endpoints[$1].x/n,y:$0.y+endpoints[$1].y/n,z:$0.z+endpoints[$1].z/n)})
            for i in indices {membership[i]=nodes.count-1}
        }
        var edges:[(Int,Int)]=[],adjacency:[Int:[Int]]=[:]
        for i in wallSegments.indices {
            let a=membership[i*2]!,b=membership[i*2+1]!
            guard a != b else {result.rejections["collapsedWallSegment"]=1;return result}
            edges.append((a,b));adjacency[a,default:[]].append(i);adjacency[b,default:[]].append(i)
        }
        var visited=Set<Int>(),rings:[Ring]=[]
        for seed in edges.indices where !visited.contains(seed) {
            var component=Set<Int>(),pending=[seed]
            while let edge=pending.popLast() {
                guard component.insert(edge).inserted else {continue}
                for node in [edges[edge].0,edges[edge].1] {pending+=adjacency[node] ?? []}
            }
            visited.formUnion(component)
            let componentNodes=Set(component.flatMap {[edges[$0].0,edges[$0].1]})
            guard componentNodes.allSatisfy({adjacency[$0]?.count == 2}) else {
                for node in componentNodes where adjacency[node]?.count == 1 {
                    let edge=adjacency[node]![0],source=wallSegments[edge]
                    result.gaps.append(.init(verticalTrackID:source.verticalTrackID,start:nodes[node],end:nodes[node],reason:"open boundary endpoint; other endpoint not inferred; not a doorway",sourceSurfaceIDs:source.sourceSurfaceIDs,frameIDs:source.frameIDs,wallSegmentIDs:[source.id]))
                }
                result.rejections[componentNodes.contains(where:{(adjacency[$0]?.count ?? 0)>2}) ? "branchedWallGraph":"openWallChain",default:0]+=1;continue
            }
            var node=edges[seed].0,current=seed,ordered:[Int]=[],points:[SpatialPoint]=[]
            repeat {
                points.append(nodes[node]);ordered.append(current)
                let edge=edges[current];node=edge.0 == node ? edge.1:edge.0
                guard let next=adjacency[node]?.first(where:{$0 != current}) else {break}
                current=next
            } while current != seed && ordered.count<=component.count
            guard node == edges[seed].0,ordered.count == component.count,points.count>=3 else {result.rejections["invalidEdgeCycle",default:0]+=1;continue}
            guard abs(signedArea(points))>0.000001,!selfIntersects(points) else {result.rejections["selfIntersectingOrDegenerateRing",default:0]+=1;continue}
            rings.append(Ring(points:points,edges:ordered))
        }
        // Inter-ring touching/crossings cannot become holes. Reject both rather than guessing topology.
        var invalid=Set<Int>()
        for i in rings.indices {for j in rings.indices where j>i {
            if boundariesIntersect(rings[i].points,rings[j].points) {invalid.insert(i);invalid.insert(j);result.rejections["intersectingOrTouchingRings",default:0]+=1}
        }}
        let valid=rings.indices.filter {!invalid.contains($0)}
        let depths=Dictionary(uniqueKeysWithValues:valid.map {i in (i,valid.filter {$0 != i && inside(rings[i].points[0],ring:rings[$0].points)}.count)})
        for i in valid where depths[i]!%2 == 0 {
            let holes=valid.filter {j in depths[j] == depths[i]!+1 && inside(rings[j].points[0],ring:rings[i].points)}
            let edgeIDs=([i]+holes).flatMap {rings[$0].edges}
            result.regions.append(.init(id:"floor-region-\(floor.id)-\(result.regions.count)",outerRing:rings[i].points,holes:holes.map {rings[$0].points},floorSurfaceIDs:floorSurfaces.map(\.id).sorted(),floorTrackID:floorTrackID,frameIDs:Array(Set(edgeIDs.flatMap {wallSegments[$0].frameIDs})).sorted(),wallSegmentIDs:edgeIDs.map {wallSegments[$0].id}.sorted(),provenance:.candidate,maximumEndpointJoinMeters:SpatialPlaneGrouping.offsetLimit))
        }
        return result
    }
    /// Shared shape-only validation for stored candidate rings; provenance/source binding belongs to Store.
    static func validate(region:SpatialStructureMap.FloorRegion)->Bool {
        let rings=[region.outerRing]+region.holes
        guard !region.floorSurfaceIDs.isEmpty,!region.wallSegmentIDs.isEmpty,region.maximumEndpointJoinMeters.isFinite,
              region.maximumEndpointJoinMeters>=0,region.maximumEndpointJoinMeters<=SpatialPlaneGrouping.offsetLimit,
              rings.allSatisfy({$0.count>=3 && $0.allSatisfy(\.isFinite) && abs(signedArea($0))>0.000001 && !selfIntersects($0)}) else {return false}
        let p=region.outerRing,origin=SIMD3(p[0].x,p[0].y,p[0].z)
        var normal=SIMD3<Float>.zero
        for i in p.indices {let a=SIMD3(p[i].x,p[i].y,p[i].z)-origin,b=SIMD3(p[(i+1)%p.count].x,p[(i+1)%p.count].y,p[(i+1)%p.count].z)-origin;normal+=simd_cross(a,b)}
        guard simd_length(normal)>0.000001 else {return false};normal=simd_normalize(normal)
        guard abs(normal.y)>=0.99,rings.flatMap({$0}).allSatisfy({abs(simd_dot(SIMD3($0.x,$0.y,$0.z)-origin,normal))<=0.0001}) else {return false}
        for i in region.holes.indices {
            let hole=region.holes[i]
            guard inside(hole[0],ring:region.outerRing),!boundariesIntersect(hole,region.outerRing) else {return false}
            for j in region.holes.indices where j>i {
                if boundariesIntersect(hole,region.holes[j]) || inside(hole[0],ring:region.holes[j]) || inside(region.holes[j][0],ring:hole) {return false}
            }
        }
        return region.areaMeters2.isFinite && region.areaMeters2>0
    }

    /// Source-edge binding: implicit closure is accepted only if the existing edges reproduce it.
    static func validate(region:SpatialStructureMap.FloorRegion,wallSegments:[SpatialStructureMap.WallSegment],floorSurfaces:[SpatialSurface])->Bool {
        guard validate(region:region),Set(region.wallSegmentIDs).count == region.wallSegmentIDs.count,
              Set(region.floorSurfaceIDs).count == region.floorSurfaceIDs.count else {return false}
        let segments=wallSegments.filter {region.wallSegmentIDs.contains($0.id)}
        let floors=floorSurfaces.filter {region.floorSurfaceIDs.contains($0.id)}
        guard segments.count == region.wallSegmentIDs.count,floors.count == region.floorSurfaceIDs.count else {return false}
        let rebuilt=build(wallSegments:segments,floorSurfaces:floors,floorTrackID:region.floorTrackID)
        return rebuilt.regions.contains {candidate in
            Set(candidate.wallSegmentIDs) == Set(region.wallSegmentIDs) && Set(candidate.floorSurfaceIDs) == Set(region.floorSurfaceIDs)
                && equivalent(candidate.outerRing,region.outerRing) && candidate.holes.count == region.holes.count
                && candidate.holes.allSatisfy {hole in region.holes.contains {equivalent(hole,$0)}}
                && Set(candidate.frameIDs) == Set(region.frameIDs) && region.provenance == .candidate
        }
    }
    private static func equivalent(_ a:[SpatialPoint],_ b:[SpatialPoint])->Bool {
        guard a.count == b.count,!a.isEmpty else {return false}
        for start in b.indices {
            if a.indices.allSatisfy({SpatialScanGeometry.distance(a[$0],b[(start+$0)%b.count])<=0.0001}) {return true}
            if a.indices.allSatisfy({SpatialScanGeometry.distance(a[$0],b[(start+b.count-$0)%b.count])<=0.0001}) {return true}
        };return false
    }

    private static func signedArea(_ p:[SpatialPoint])->Float {p.indices.reduce(Float(0)) {sum,i in let a=p[i],b=p[(i+1)%p.count];return sum+a.x*b.z-b.x*a.z}/2}
    private static func cross(_ a:SpatialPoint,_ b:SpatialPoint,_ c:SpatialPoint)->Float {(b.x-a.x)*(c.z-a.z)-(b.z-a.z)*(c.x-a.x)}
    private static func intersects(_ a:SpatialPoint,_ b:SpatialPoint,_ c:SpatialPoint,_ d:SpatialPoint)->Bool {
        let e:Float=0.000001
        guard max(a.x,b.x)+e>=min(c.x,d.x),max(c.x,d.x)+e>=min(a.x,b.x),max(a.z,b.z)+e>=min(c.z,d.z),max(c.z,d.z)+e>=min(a.z,b.z) else {return false}
        let p=cross(a,b,c),q=cross(a,b,d),r=cross(c,d,a),s=cross(c,d,b)
        return ((p>=(-e) && q<=e)||(q>=(-e) && p<=e)) && ((r>=(-e) && s<=e)||(s>=(-e) && r<=e))
    }
    private static func selfIntersects(_ ring:[SpatialPoint])->Bool {
        for i in ring.indices {for j in ring.indices where j>i {
            if j == (i+1)%ring.count || i == (j+1)%ring.count {continue}
            if intersects(ring[i],ring[(i+1)%ring.count],ring[j],ring[(j+1)%ring.count]) {return true}
        }};return false
    }
    private static func boundariesIntersect(_ a:[SpatialPoint],_ b:[SpatialPoint])->Bool {
        a.indices.contains {i in b.indices.contains {j in intersects(a[i],a[(i+1)%a.count],b[j],b[(j+1)%b.count])}}
    }
    private static func inside(_ p:SpatialPoint,ring:[SpatialPoint])->Bool {
        var value=false
        for i in ring.indices {let a=ring[i],b=ring[(i+1)%ring.count]
            if (a.z>p.z) != (b.z>p.z),p.x<(b.x-a.x)*(p.z-a.z)/(b.z-a.z)+a.x {value.toggle()}
        };return value
    }
}
