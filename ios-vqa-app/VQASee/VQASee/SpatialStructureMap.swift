import Foundation

/// Metric structure output for one scan epoch. Consumed by top/3D/export at a single revision.
nonisolated struct SpatialStructureMap: Codable, Sendable, Equatable {
    enum State: String, Codable, Sendable { case incomplete, ready }

    enum Provenance: String, Codable, Sendable {
        /// Plane intersection only; not tape-validated.
        case candidate
        case observed
        case inferred
    }

    struct Corner: Codable, Sendable, Equatable, Identifiable {
        var id: String
        var position: SpatialPoint
        var provenance: Provenance
        var planeTrackIDs: [String]
        var planeIntersectionResidualMeters: Float?
        var sourceSurfaceIDs: [String] = []
        var frameIDs: [String] = []
    }

    struct WallSegment: Codable, Sendable, Equatable, Identifiable {
        var id: String
        var verticalTrackID: String
        var start: SpatialPoint
        var end: SpatialPoint
        var startProvenance: Provenance
        var endProvenance: Provenance
        /// Candidate span along a hypothesized wall/floor intersection, never a measured room dimension.
        var lengthMeters: Float
        var sourceSurfaceIDs: [String] = []
        var frameIDs: [String] = []
    }

    struct FloorRegion: Codable, Sendable, Equatable, Identifiable {
        var id:String
        /// Implicit closure; first vertex is not repeated. Source wall edges must justify the closing edge.
        var outerRing:[SpatialPoint]
        var holes:[[SpatialPoint]]
        var floorSurfaceIDs:[String]
        var floorTrackID:String?
        var frameIDs:[String]
        var wallSegmentIDs:[String]
        var provenance:Provenance = .candidate
        var maximumEndpointJoinMeters:Float = SpatialPlaneGrouping.offsetLimit
        var areaSquareMeters:Float {areaMeters2}
        var areaMeters2:Float {
            func area(_ ring:[SpatialPoint])->Float {
                guard ring.count>=3 else {return 0}
                var x:Float=0,y:Float=0,z:Float=0
                for i in ring.indices {let a=ring[i],b=ring[(i+1)%ring.count];x+=(a.y-b.y)*(a.z+b.z);y+=(a.z-b.z)*(a.x+b.x);z+=(a.x-b.x)*(a.y+b.y)}
                return sqrt(x*x+y*y+z*z)/2
            }
            return max(0,area(outerRing)-holes.reduce(0) {$0+area($1)})
        }
    }

    struct Gap: Codable, Sendable, Equatable {
        var verticalTrackID: String
        var start: SpatialPoint
        var end: SpatialPoint
        var reason: String = "unobserved interval; not a doorway"
        var sourceSurfaceIDs:[String] = []
        var frameIDs:[String] = []
        var wallSegmentIDs:[String] = []
    }

    struct PatchDistance: Codable, Sendable, Equatable {
        var sourceSurfaceID: String
        var distanceMeters: Float?
        var failure: String?
    }
    struct PlaneCoverage: Codable, Sendable, Equatable {
        var trackID: String
        var sourceSurfaceIDs: [String]
        var minimumDistanceMeters: Float?
        var nearestSourceSurfaceID: String?
        var patches: [PatchDistance]
    }
    struct RejectedCornerCandidate: Codable, Sendable, Equatable {
        var intersection: SpatialPoint
        var reason: String
        var planes: [PlaneCoverage]
    }

    struct Quality: Codable, Sendable, Equatable {
        var verticalTrackCount: Int = 0
        var horizontalTrackCount: Int = 0
        var message: String = ""
        var semanticFloorVerified = false
        /// Gravity-aligned lowest support plane. Independent of ADE20K floor class.
        var geometricFloorDatum = false
        /// Published wall identities form a simple corner cycle. Does not invent walls or set ready.
        var circuitClosed = false
        /// User-facing coverage, not a floor-plan promise.
        var observedCoverageLine: String { circuitClosed ? "看到的墙接上了" : "还没看全" }
        var finiteCornerCovered = false
        var visibleFrameCount = 0
        var requiredVisibleFrames = 12
        var rejections: [String:Int] = [:]
    }

    var scanID: UUID
    var epoch: Int
    var revision: Int
    var poseRevision: Int
    var state: State
    var corners: [Corner] = []
    var wallSegments: [WallSegment] = []
    var floorRegions:[FloorRegion] = []
    var quality: Quality = Quality()
    var sourceSurfaceIDs: [String] = []
    var frameIDs: [String] = []
    var gaps: [Gap] = []
    var rejectedCornerCandidates: [RejectedCornerCandidate] = []

    var statusLine: String {
        let walls = wallSegments.count
        let corners = corners.count
        let tag = state == .ready ? "结构已就绪" : "结构未完成"
        return "\(tag) · revision \(revision) · 墙段 \(walls) · 角点 \(corners)"
    }
}

/// P0 experiment receipt (export / tape validation); independent segments left empty by design.
nonisolated struct SpatialStructureP0Receipt: Codable, Sendable, Equatable {
    struct TapeSegment: Codable, Sendable, Equatable {
        var wallSegmentID: String
        var measuredMeters: Float?
        var note: String?
    }

    var scanID: UUID
    var epoch: Int
    var structureRevision: Int
    var keyframeIDs: [String]
    var corners: [SpatialStructureMap.Corner]
    var floorRegions:[SpatialStructureMap.FloorRegion] = []
    var wallSegments: [SpatialStructureMap.WallSegment]
    var tapeSegments: [TapeSegment] = []
    var builderMessage: String
    var quality: SpatialStructureMap.Quality? = nil
    var sourceSurfaceIDs: [String] = []
    var gaps: [SpatialStructureMap.Gap] = []
    var rejectedCornerCandidates: [SpatialStructureMap.RejectedCornerCandidate] = []
}

// Explicit compatibility: synthesized Codable does not use property defaults for missing JSON keys.
extension SpatialStructureMap {
    private enum CodingKeys:String,CodingKey {case scanID,epoch,revision,poseRevision,state,corners,wallSegments,floorRegions,quality,sourceSurfaceIDs,frameIDs,gaps,rejectedCornerCandidates}
    init(from decoder:Decoder) throws {
        let c=try decoder.container(keyedBy:CodingKeys.self)
        scanID=try c.decode(UUID.self,forKey:.scanID);epoch=try c.decode(Int.self,forKey:.epoch)
        revision=try c.decode(Int.self,forKey:.revision);poseRevision=try c.decodeIfPresent(Int.self,forKey:.poseRevision) ?? 0
        state=try c.decode(State.self,forKey:.state)
        corners=try c.decodeIfPresent([Corner].self,forKey:.corners) ?? []
        wallSegments=try c.decodeIfPresent([WallSegment].self,forKey:.wallSegments) ?? []
        floorRegions=try c.decodeIfPresent([FloorRegion].self,forKey:.floorRegions) ?? []
        quality=try c.decodeIfPresent(Quality.self,forKey:.quality) ?? Quality()
        sourceSurfaceIDs=try c.decodeIfPresent([String].self,forKey:.sourceSurfaceIDs) ?? []
        frameIDs=try c.decodeIfPresent([String].self,forKey:.frameIDs) ?? []
        gaps=try c.decodeIfPresent([Gap].self,forKey:.gaps) ?? []
        rejectedCornerCandidates=try c.decodeIfPresent([RejectedCornerCandidate].self,forKey:.rejectedCornerCandidates) ?? []
    }
}
extension SpatialStructureP0Receipt {
    private enum CodingKeys:String,CodingKey {case scanID,epoch,structureRevision,keyframeIDs,corners,wallSegments,floorRegions,tapeSegments,builderMessage,quality,sourceSurfaceIDs,gaps,rejectedCornerCandidates}
    init(from decoder:Decoder) throws {
        let c=try decoder.container(keyedBy:CodingKeys.self)
        scanID=try c.decode(UUID.self,forKey:.scanID);epoch=try c.decode(Int.self,forKey:.epoch)
        structureRevision=try c.decode(Int.self,forKey:.structureRevision)
        keyframeIDs=try c.decodeIfPresent([String].self,forKey:.keyframeIDs) ?? []
        corners=try c.decodeIfPresent([SpatialStructureMap.Corner].self,forKey:.corners) ?? []
        wallSegments=try c.decodeIfPresent([SpatialStructureMap.WallSegment].self,forKey:.wallSegments) ?? []
        floorRegions=try c.decodeIfPresent([SpatialStructureMap.FloorRegion].self,forKey:.floorRegions) ?? []
        tapeSegments=try c.decodeIfPresent([TapeSegment].self,forKey:.tapeSegments) ?? []
        builderMessage=try c.decodeIfPresent(String.self,forKey:.builderMessage) ?? ""
        quality=try c.decodeIfPresent(SpatialStructureMap.Quality.self,forKey:.quality)
        sourceSurfaceIDs=try c.decodeIfPresent([String].self,forKey:.sourceSurfaceIDs) ?? []
        gaps=try c.decodeIfPresent([SpatialStructureMap.Gap].self,forKey:.gaps) ?? []
        rejectedCornerCandidates=try c.decodeIfPresent([SpatialStructureMap.RejectedCornerCandidate].self,forKey:.rejectedCornerCandidates) ?? []
    }
}

extension SpatialStructureMap.Quality {
    private enum CodingKeys:String,CodingKey {case verticalTrackCount,horizontalTrackCount,message,semanticFloorVerified,geometricFloorDatum,circuitClosed,finiteCornerCovered,visibleFrameCount,requiredVisibleFrames,rejections}
    init(from decoder:Decoder) throws {
        let c=try decoder.container(keyedBy:CodingKeys.self)
        verticalTrackCount=try c.decodeIfPresent(Int.self,forKey:.verticalTrackCount) ?? 0
        horizontalTrackCount=try c.decodeIfPresent(Int.self,forKey:.horizontalTrackCount) ?? 0
        message=try c.decodeIfPresent(String.self,forKey:.message) ?? ""
        semanticFloorVerified=try c.decodeIfPresent(Bool.self,forKey:.semanticFloorVerified) ?? false
        geometricFloorDatum=try c.decodeIfPresent(Bool.self,forKey:.geometricFloorDatum) ?? false
        circuitClosed=try c.decodeIfPresent(Bool.self,forKey:.circuitClosed) ?? false
        finiteCornerCovered=try c.decodeIfPresent(Bool.self,forKey:.finiteCornerCovered) ?? false
        visibleFrameCount=try c.decodeIfPresent(Int.self,forKey:.visibleFrameCount) ?? 0
        requiredVisibleFrames=try c.decodeIfPresent(Int.self,forKey:.requiredVisibleFrames) ?? 12
        rejections=try c.decodeIfPresent([String:Int].self,forKey:.rejections) ?? [:]
    }
}
extension SpatialStructureMap.Gap {
    private enum CodingKeys:String,CodingKey {case verticalTrackID,start,end,reason,sourceSurfaceIDs,frameIDs,wallSegmentIDs}
    init(from decoder:Decoder) throws {
        let c=try decoder.container(keyedBy:CodingKeys.self)
        verticalTrackID=try c.decode(String.self,forKey:.verticalTrackID);start=try c.decode(SpatialPoint.self,forKey:.start);end=try c.decode(SpatialPoint.self,forKey:.end)
        reason=try c.decodeIfPresent(String.self,forKey:.reason) ?? "unobserved interval; not a doorway"
        sourceSurfaceIDs=try c.decodeIfPresent([String].self,forKey:.sourceSurfaceIDs) ?? []
        frameIDs=try c.decodeIfPresent([String].self,forKey:.frameIDs) ?? []
        wallSegmentIDs=try c.decodeIfPresent([String].self,forKey:.wallSegmentIDs) ?? []
    }
}
