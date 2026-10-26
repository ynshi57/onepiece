import Foundation
import simd

/// Read-only scene overlay: wall candidates and observation shells — not room floor vectorization.
nonisolated struct SpatialSceneHypothesis: Sendable, Equatable {
    struct WallLine: Sendable, Equatable {
        var surfaceIDs: [String]
        var role: String
        var confirmed: Bool
        var start: SpatialPoint
        var end: SpatialPoint
    }

    var walls: [WallLine] = []
    var objects: [WallLine] = []
    var unknownHorizontals: [SpatialSurface] = []
    var unknownVerticals: [SpatialSurface] = []
    var conflictSurfaces: [SpatialSurface] = []
    var epoch: Int?
    var scope = "structure overlay; room floor regions come from StructureMap when available"

    var publishedStatusLine: String {
        let confirmedWalls = walls.filter(\.confirmed).count
        return "墙线候选 \(walls.count) 段（语义已确认 \(confirmedWalls)）· 未知水平 \(unknownHorizontals.count) 面 · 地面矢量待 StructureMap"
    }

    static func rebuild(document: SpatialScanDocument) -> Self {
        var result = Self()
        result.epoch = document.keyframes.last?.pose.epoch ?? document.surfaces.last?.epoch
        let surfaces = document.surfaces.filter { $0.epoch == result.epoch && $0.observed }
        let rows = document.indoorSemanticObservations ?? []
        let records = SpatialVisibleFloorProjection.validatedRecords(frames: document.keyframes, rows: rows)
        func semantic(_ surface: SpatialSurface) -> SpatialSurfaceSemanticEvidence.Semantic {
            SpatialSurfaceSemanticEvidence.resolve(surface: surface, records: records)
        }
        result.unknownHorizontals = surfaces.filter { $0.kind == "horizontal" }
        result.conflictSurfaces = surfaces.filter { semantic($0) == .other }
        result.unknownVerticals = surfaces.filter { $0.kind == "vertical" && semantic($0) == .unknown }
        for line in SpatialPlaneGrouping.wallLines(surfaces: surfaces.filter { semantic($0) != .other }) {
            let wrapped = WallLine(
                surfaceIDs: line.surfaceIDs,
                role: line.role,
                confirmed: line.surfaceIDs.allSatisfy { id in
                    surfaces.first(where: { $0.id == id }).map { semantic($0) == .wall } ?? false
                },
                start: line.start,
                end: line.end
            )
            if line.role == "wall" { result.walls.append(wrapped) }
            else if line.role == "object" { result.objects.append(wrapped) }
        }
        return result
    }
}
