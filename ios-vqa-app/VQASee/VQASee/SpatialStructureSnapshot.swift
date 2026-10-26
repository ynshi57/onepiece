import Foundation
import CryptoKit

/// Only builder inputs belong in this key. No depth, display state, timestamps of saving, or snapshot recursion.
nonisolated struct SpatialStructureInput: Codable, Sendable, Equatable {
    static let algorithmVersion = "structure-geometric-floor-datum-v1"
    var algorithmVersion = Self.algorithmVersion
    var scanID: UUID
    var frames: [SpatialKeyframe]
    var surfaces: [SpatialSurface]
    var semantics: [SpatialIndoorSemanticObservation]

    init(document: SpatialScanDocument) {
        scanID = document.id
        frames = document.keyframes
        surfaces = document.surfaces
        semantics = (document.indoorSemanticObservations ?? []).map { row in
            var row = row
            row.previousAttempts = nil // The builder reads current evidence only.
            return row
        }
    }
    var document: SpatialScanDocument {
        var value = SpatialScanDocument(id:scanID)
        value.keyframes = frames; value.surfaces = surfaces
        value.indoorSemanticObservations = semantics
        return value
    }
    func signature() throws -> String {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        return SHA256.hash(data:try encoder.encode(self)).map {String(format:"%02x",$0)}.joined()
    }
    var epoch: Int {frames.last?.pose.epoch ?? surfaces.map(\.epoch).max() ?? 0}
}

/// A derived map and its input identity are persisted together, never mixed across revisions.
nonisolated struct SpatialStructureSnapshot: Codable, Sendable, Equatable {
    var inputSignature: String
    var map: SpatialStructureMap

    static func inputSignature(for document:SpatialScanDocument) throws -> String {
        try SpatialStructureInput(document:document).signature()
    }
    /// Call outside a rendering body. Views can gate synchronously with their captured typed input instead.
    func matches(document:SpatialScanDocument) -> Bool {
        guard map.scanID == document.id, map.epoch == SpatialStructureInput(document:document).epoch,
              let signature = try? Self.inputSignature(for:document) else {return false}
        return inputSignature == signature
    }
    var isWellFormed: Bool {
        let hash = inputSignature.utf8
        guard hash.count == 64,hash.allSatisfy({(48...57).contains($0) || (97...102).contains($0)}),
              map.revision > 0,map.revision < Int.max,map.poseRevision >= 0,map.epoch >= 0,
              Set(map.corners.map(\.id)).count == map.corners.count,
              Set(map.wallSegments.map(\.id)).count == map.wallSegments.count,
              Set(map.floorRegions.map(\.id)).count == map.floorRegions.count else {return false}
        guard map.corners.allSatisfy({!$0.id.isEmpty && $0.position.isFinite && ($0.planeIntersectionResidualMeters.map {$0.isFinite && $0 >= 0} ?? true)}),
              map.wallSegments.allSatisfy({!$0.id.isEmpty && $0.start.isFinite && $0.end.isFinite && $0.lengthMeters.isFinite && $0.lengthMeters > 0 && abs(SpatialScanGeometry.distance($0.start,$0.end)-$0.lengthMeters) <= 0.001}),
              map.gaps.allSatisfy({$0.start.isFinite && $0.end.isFinite}),
              map.rejectedCornerCandidates.allSatisfy({$0.intersection.isFinite && $0.planes.allSatisfy { p in
                  (p.minimumDistanceMeters.map {$0.isFinite && $0 >= 0} ?? true) && p.patches.allSatisfy { $0.distanceMeters.map {$0.isFinite && $0 >= 0} ?? true }
              }}) else {return false}
        let walls = Set(map.wallSegments.map(\.id))
        return map.floorRegions.allSatisfy {region in
            !region.id.isEmpty && SpatialStructureFloorRegionBuilder.validate(region:region) &&
            region.wallSegmentIDs.allSatisfy {walls.contains($0)}
        }
    }
    func sourcesMatch(document:SpatialScanDocument) -> Bool {
        let surfaces = Set(document.surfaces.filter {$0.epoch == map.epoch && $0.observed}.map(\.id))
        let frames = Set(document.keyframes.filter {$0.pose.epoch == map.epoch}.map(\.id))
        func ids(_ s:[String],_ f:[String])->Bool {s.allSatisfy {surfaces.contains($0)} && f.allSatisfy {frames.contains($0)}}
        return ids(map.sourceSurfaceIDs,map.frameIDs) &&
            map.corners.allSatisfy {ids($0.sourceSurfaceIDs,$0.frameIDs)} &&
            map.wallSegments.allSatisfy {ids($0.sourceSurfaceIDs,$0.frameIDs)} &&
            map.floorRegions.allSatisfy {region in
                ids(region.floorSurfaceIDs,region.frameIDs) && SpatialStructureFloorRegionBuilder.validate(region:region,wallSegments:map.wallSegments,floorSurfaces:document.surfaces.filter {$0.epoch == map.epoch && $0.observed && $0.kind == "horizontal"})
            } &&
            map.gaps.allSatisfy {ids($0.sourceSurfaceIDs,$0.frameIDs)}
    }
}

/// One process-wide single-flight derivation serves persistence and both display consumers.
/// Input-keyed results never stand in for a new input; callers still reject late generation results.
actor SpatialStructureSnapshotCache {
    static let shared = SpatialStructureSnapshotCache()
    private var completed: [UUID:SpatialStructureSnapshot] = [:]
    private var pending: [String:Task<SpatialStructureSnapshot,Error>] = [:]
    private var revisions: [UUID:Int] = [:]
    private(set) var buildCount = 0

    func snapshot(for document:SpatialScanDocument) async throws -> SpatialStructureSnapshot {
        let input = SpatialStructureInput(document:document)
        let signature = try input.signature()
        if let revision = document.structureRevision {
            guard revision >= 0,revision < Int.max else {throw SpatialScanStore.StoreError.invalidDocument}
            revisions[document.id] = max(revisions[document.id] ?? 0,revision)
        }
        let saved = document.structureSnapshot
        if let saved, saved.map.scanID == document.id, saved.isWellFormed {
            revisions[document.id] = max(revisions[document.id] ?? 0,saved.map.revision)
        }
        if let saved,saved.map.scanID == document.id,saved.inputSignature == signature,saved.isWellFormed,saved.sourcesMatch(document:document),saved.map.epoch == input.epoch,saved.map.revision >= (revisions[document.id] ?? 0),
           completed[document.id].map({$0.inputSignature != signature || $0.map.revision <= saved.map.revision}) ?? true {
            completed[document.id] = saved
            return saved
        }
        if let value = completed[document.id],value.inputSignature == signature,value.map.revision >= (revisions[document.id] ?? 0) {return value}
        if let task = pending[signature] {return try await task.value}
        guard (revisions[document.id] ?? 0) < Int.max-1 else {throw SpatialScanStore.StoreError.invalidDocument}
        let revision = (revisions[document.id] ?? 0)+1
        revisions[document.id] = revision
        buildCount += 1
        let task = Task.detached(priority:.utility) { () throws -> SpatialStructureSnapshot in
            var config = SpatialStructureP0Builder.Config()
            config.revision = revision; config.poseRevision = revision
            let map = SpatialStructureP0Builder.build(document:input.document,config:config).map
            let result = SpatialStructureSnapshot(inputSignature:signature,map:map)
            guard result.isWellFormed,result.sourcesMatch(document:input.document) else {throw SpatialScanStore.StoreError.invalidDocument}
            return result
        }
        pending[signature] = task
        do {
            let value = try await task.value
            pending[signature] = nil
            if completed[document.id].map({$0.map.revision <= value.map.revision}) ?? true {completed[document.id] = value}
            // Bound retained geometry; revision counters are small and remain monotonic for this process.
            if completed.count > 8,let key = completed.keys.first(where:{$0 != document.id}) {completed[key] = nil}
            return value
        } catch {pending[signature] = nil;throw error}
    }
}
