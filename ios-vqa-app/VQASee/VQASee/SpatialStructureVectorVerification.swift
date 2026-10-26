#if DEBUG
import Foundation

nonisolated struct SpatialStructureVectorVerificationReport: Codable, Sendable {
    struct RegionSummary: Codable, Sendable {var id:String;var outerVertexCount:Int;var holeCount:Int;var areaMeters2:Float;var closedByExistingEdges:Bool}
    var floorRegionSummaries:[RegionSummary]=[]
    var runID:UUID
    var map:SpatialStructureMap
    var receipt:SpatialStructureP0Receipt
    var inputSignature:String
    var sourceFrameCount:Int
    var snapshotSeconds:Double
    var persistenceRoundTripSeconds:Double
    var metadataCodingRoundTripSeconds:Double?
    var renderSeconds:Double
    /// Preparation until the preview image is available, not first-screen-present or p95 latency.
    var previewPreparationSeconds:Double
    var persistedRoundTripEqual:Bool
    var state = "incomplete"
    var meaning = "Real saved scan metadata; no texture-depth or floor-grid computation. Preparation timing is not first rendered UI frame or p95. Closed candidates do not establish independent dimensional accuracy."
}
#endif
