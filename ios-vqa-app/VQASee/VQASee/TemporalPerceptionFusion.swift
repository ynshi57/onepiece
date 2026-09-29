import CoreGraphics
import Foundation

// MARK: - Temporal perception fusion (design 2026-09-28 A+B+C)
//
// Object tracks + risk latch + C geometry smoothing. Traversable/OOD state
// machines remain D work.
// Owned on the main actor via StreamingViewModel; no locks required.

struct TemporalFusionConfig: Equatable, Sendable {
    /// Feature flag — OTA / Settings can turn geometry fusion off and keep raw signal.
    var enabled: Bool = true
    /// Normal geometry older than this must not overwrite the live overlay.
    var maxUsableResultAgeMs: Double = 500
    var matchIoU: Double = 0.30
    var maximumPredictionAge: CFTimeInterval = 0.25
    var confidenceBlend: Double = 0.55
    var riskDowngradeHoldMs: Double = 500
    var riskDowngradeMissFrames: Int = 2
    /// C: blend only two agreeing, fresh lane/path observations.  This removes
    /// frame-to-frame shimmer without ever retaining a missing road geometry.
    var geometryBlend: Double = 0.60
    var laneMaximumPointJump: Double = 0.13
    var guidanceMaximumPointJump: Double = 0.12

    static let `default` = TemporalFusionConfig()
}

enum RiskLevel: String, Sendable, Equatable, Comparable {
    case low
    case medium
    case high

    private var rank: Int {
        switch self {
        case .low: return 0
        case .medium: return 1
        case .high: return 2
        }
    }

    static func < (lhs: RiskLevel, rhs: RiskLevel) -> Bool {
        lhs.rank < rhs.rank
    }
}

enum TemporalFusionDisposition: Sendable, Equatable {
    case publishFullGeometry
    case publishRiskOnly
    case discard
}

struct TemporalFusionResult: Sendable, Equatable {
    let signal: LocalVisionSignal
    let disposition: TemporalFusionDisposition
    let riskLevel: RiskLevel
}

/// Pure policy: stale road geometry is never published. A late priority risk may
/// still surface its object box, but with all road/path geometry stripped.
enum StaleResultPolicy {
    static func disposition(
        resultAgeMs: Double,
        maxUsableResultAgeMs: Double,
        hasPriorityRisk: Bool
    ) -> TemporalFusionDisposition {
        if resultAgeMs <= maxUsableResultAgeMs {
            return .publishFullGeometry
        }
        return hasPriorityRisk ? .publishRiskOnly : .discard
    }
}

/// Risk latch: upgrades publish immediately; downgrades need hold time / miss frames.
struct RiskLatch: Equatable, Sendable {
    private(set) var level: RiskLevel = .low
    private(set) var lastUpgradeAt: CFTimeInterval = 0
    private var pendingLowerSince: CFTimeInterval?
    private var consecutiveLowerFrames: Int = 0

    mutating func reset() {
        level = .low
        lastUpgradeAt = 0
        pendingLowerSince = nil
        consecutiveLowerFrames = 0
    }

    /// Returns true when the latched level changed this observation.
    @discardableResult
    mutating func observe(
        measured: RiskLevel,
        at time: CFTimeInterval,
        config: TemporalFusionConfig = .default
    ) -> Bool {
        if measured > level {
            level = measured
            lastUpgradeAt = time
            pendingLowerSince = nil
            consecutiveLowerFrames = 0
            return true
        }
        if measured == level {
            pendingLowerSince = nil
            consecutiveLowerFrames = 0
            return false
        }
        // measured < level
        consecutiveLowerFrames += 1
        if pendingLowerSince == nil {
            pendingLowerSince = time
        }
        let heldLongEnough =
            (time - (pendingLowerSince ?? time)) * 1000.0 >= config.riskDowngradeHoldMs
        let enoughMisses = consecutiveLowerFrames >= config.riskDowngradeMissFrames
        if heldLongEnough || enoughMisses {
            level = measured
            pendingLowerSince = nil
            consecutiveLowerFrames = 0
            return true
        }
        return false
    }
}

/// Short-horizon object tracker for the camera overlay. It reduces one-frame
/// detector jitter but deliberately never withholds a priority-risk candidate.
final class LocalObjectTemporalFusion {
    private struct Track {
        var id: Int
        var object: LocalPerceptionObject
        var hits: Int
        var misses: Int
        var lastSeenAt: CFTimeInterval
    }

    private var tracks: [Track] = []
    private var nextID = 1
    private var config: TemporalFusionConfig
    private(set) var riskLatch = RiskLatch()
    private(set) var lastDisposition: TemporalFusionDisposition = .publishFullGeometry
    private var previousLanePolylines: [LanePolyline] = []
    private var previousGuidancePath: GuidancePath?

    init(config: TemporalFusionConfig = .default) {
        self.config = config
    }

    func apply(config: TemporalFusionConfig) {
        self.config = config
    }

    func reset() {
        tracks.removeAll()
        nextID = 1
        riskLatch.reset()
        lastDisposition = .publishFullGeometry
        previousLanePolylines = []
        previousGuidancePath = nil
    }

    func fuse(
        _ raw: LocalVisionSignal,
        capturedAt: CFTimeInterval,
        resultAgeMs: Double? = nil
    ) -> TemporalFusionResult {
        guard config.enabled else {
            lastDisposition = .publishFullGeometry
            return TemporalFusionResult(
                signal: raw,
                disposition: .publishFullGeometry,
                riskLevel: Self.riskLevel(from: raw.perception.objects, raw: raw)
            )
        }
        guard !raw.analyzerFailed else {
            reset()
            return TemporalFusionResult(signal: raw, disposition: .publishFullGeometry, riskLevel: .low)
        }

        let age = resultAgeMs ?? 0
        let hasPriority = raw.perception.objects.contains { $0.kind.isPriorityRisk }
        let disposition = StaleResultPolicy.disposition(
            resultAgeMs: age,
            maxUsableResultAgeMs: config.maxUsableResultAgeMs,
            hasPriorityRisk: hasPriority
        )
        guard disposition != .discard else {
            lastDisposition = .discard
            // Keep latch from seeing a "clear" on a discarded frame.
            return TemporalFusionResult(signal: raw, disposition: .discard, riskLevel: riskLatch.level)
        }
        lastDisposition = disposition

        var unmatchedTrackIndices = Set(tracks.indices)
        var fusedObjects: [LocalPerceptionObject] = []
        for candidate in raw.perception.objects {
            guard let box = candidate.normalizedBoundingBox else {
                fusedObjects.append(candidate)
                continue
            }
            let matchingIndex = tracks.indices
                .filter { unmatchedTrackIndices.contains($0) && tracks[$0].object.kind == candidate.kind }
                .max { lhs, rhs in
                    iou(tracks[lhs].object.normalizedBoundingBox, box)
                        < iou(tracks[rhs].object.normalizedBoundingBox, box)
                }
            if let matchingIndex,
               iou(tracks[matchingIndex].object.normalizedBoundingBox, box) >= config.matchIoU
            {
                unmatchedTrackIndices.remove(matchingIndex)
                var track = tracks[matchingIndex]
                track.hits += 1
                track.misses = 0
                track.lastSeenAt = capturedAt
                let blend = config.confidenceBlend
                track.object = LocalPerceptionObject(
                    kind: candidate.kind,
                    direction: candidate.direction,
                    confidence: blend * candidate.confidence + (1 - blend) * track.object.confidence,
                    normalizedBoundingBox: blendRects(
                        track.object.normalizedBoundingBox!,
                        box,
                        weight: blend
                    )
                )
                tracks[matchingIndex] = track
                // Priority risks bypass confirmation; ordinary objects need 2 hits.
                if track.hits >= 2 || candidate.kind.isPriorityRisk {
                    fusedObjects.append(track.object)
                }
            } else {
                let track = Track(
                    id: nextID,
                    object: candidate,
                    hits: 1,
                    misses: 0,
                    lastSeenAt: capturedAt
                )
                nextID += 1
                tracks.append(track)
                if candidate.kind.isPriorityRisk {
                    fusedObjects.append(candidate)
                }
            }
        }

        for index in unmatchedTrackIndices.sorted(by: >) {
            tracks[index].misses += 1
            let track = tracks[index]
            if capturedAt - track.lastSeenAt <= config.maximumPredictionAge, track.misses == 1 {
                fusedObjects.append(track.object)
            }
        }
        tracks.removeAll {
            capturedAt - $0.lastSeenAt > config.maximumPredictionAge || $0.misses >= 2
        }

        let measuredRisk = Self.riskLevel(from: fusedObjects, raw: raw)
        _ = riskLatch.observe(measured: measuredRisk, at: capturedAt, config: config)

        if disposition == .publishRiskOnly {
            // The detector found a potential risk too late for its road geometry
            // to be trustworthy. Keep the risk visible, never the old path/lane.
            var riskOnly = LocalPerceptionSignal.empty
            riskOnly.objects = fusedObjects
            riskOnly.modelStatus = raw.perception.modelStatus
            riskOnly.pathGuidance = LocalPathGuidanceEngine.evaluate(
                perception: riskOnly,
                isTooDark: raw.isTooDark,
                isLikelyCovered: raw.isLikelyCovered,
                depthCapability: raw.perception.pathGuidance.depthCapability
            )
            return TemporalFusionResult(
                signal: replacingPerception(of: raw, with: riskOnly),
                disposition: .publishRiskOnly,
                riskLevel: riskLatch.level
            )
        }

        var perception = raw.perception
        perception.objects = fusedObjects
        // Preserve the capability actually resolved by AR capture. Falling back
        // to the device default here would turn an active depth session inactive.
        perception.pathGuidance = LocalPathGuidanceEngine.evaluate(
            perception: perception,
            isTooDark: raw.isTooDark,
            isLikelyCovered: raw.isLikelyCovered,
            depthCapability: raw.perception.pathGuidance.depthCapability,
            segmentationCues: perception.segmentationCues
        )
        perception.lanePolylines = fuseLanePolylines(raw.perception.lanePolylines)
        perception.guidancePath = fuseGuidancePath(raw.perception.guidancePath)
        return TemporalFusionResult(
            signal: replacingPerception(of: raw, with: perception),
            disposition: .publishFullGeometry,
            riskLevel: riskLatch.level
        )
    }

    private func replacingPerception(of raw: LocalVisionSignal, with perception: LocalPerceptionSignal) -> LocalVisionSignal {
        LocalVisionSignal(
            hasHuman: raw.hasHuman,
            humanDirection: raw.humanDirection,
            brightness: raw.brightness,
            sceneChangeScore: raw.sceneChangeScore,
            isTooDark: raw.isTooDark,
            isLikelyCovered: raw.isLikelyCovered,
            analyzerFailed: raw.analyzerFailed,
            perception: perception
        )
    }

    private func fuseLanePolylines(_ current: [LanePolyline]) -> [LanePolyline] {
        defer { previousLanePolylines = current }
        guard !current.isEmpty, !previousLanePolylines.isEmpty else { return current }
        return current.map { lane in
            guard let previous = previousLanePolylines.first(where: {
                $0.laneIndex == lane.laneIndex && $0.source == lane.source
            }) else { return lane }
            let prior = resample(previous.points, to: lane.points.count)
            guard prior.count == lane.points.count,
                  maximumDistance(prior, lane.points) <= config.laneMaximumPointJump else { return lane }
            return LanePolyline(
                laneIndex: lane.laneIndex,
                source: lane.source,
                points: zip(prior, lane.points).map { blend($0, $1, weight: config.geometryBlend) }
            )
        }
    }

    private func fuseGuidancePath(_ current: GuidancePath?) -> GuidancePath? {
        guard var current else {
            previousGuidancePath = nil
            return nil
        }
        defer { previousGuidancePath = current }
        guard current.status == .ok,
              let currentLine = current.primary,
              let previousLine = previousGuidancePath?.primary,
              previousGuidancePath?.status == .ok else { return current }
        let prior = resample(previousLine.points, to: currentLine.points.count)
        guard prior.count == currentLine.points.count,
              maximumDistance(prior.map { CGPoint(x: $0.x, y: $0.y) }, currentLine.points.map { CGPoint(x: $0.x, y: $0.y) }) <= config.guidanceMaximumPointJump else {
            return current
        }
        let blendedPoints = zip(prior, currentLine.points).map { previous, latest in
            GuidancePoint(
                x: previous.x * (1 - config.geometryBlend) + latest.x * config.geometryBlend,
                y: previous.y * (1 - config.geometryBlend) + latest.y * config.geometryBlend,
                halfWidth: previous.halfWidth * (1 - config.geometryBlend) + latest.halfWidth * config.geometryBlend
            )
        }
        if let index = current.lines.firstIndex(where: { $0.kind == currentLine.kind }) {
            current.lines[index].points = blendedPoints
        } else if !current.lines.isEmpty {
            current.lines[0].points = blendedPoints
        }
        return current
    }

    private func resample(_ points: [CGPoint], to count: Int) -> [CGPoint] {
        guard count > 0, !points.isEmpty else { return [] }
        if points.count == count { return points }
        if points.count == 1 { return Array(repeating: points[0], count: count) }
        return (0..<count).map { index in
            let position = Double(index) * Double(points.count - 1) / Double(max(1, count - 1))
            let lower = Int(position.rounded(.down))
            let upper = min(points.count - 1, lower + 1)
            return blend(points[lower], points[upper], weight: position - Double(lower))
        }
    }

    private func resample(_ points: [GuidancePoint], to count: Int) -> [GuidancePoint] {
        guard count > 0, !points.isEmpty else { return [] }
        if points.count == count { return points }
        if points.count == 1 { return Array(repeating: points[0], count: count) }
        return (0..<count).map { index in
            let position = Double(index) * Double(points.count - 1) / Double(max(1, count - 1))
            let lower = Int(position.rounded(.down))
            let upper = min(points.count - 1, lower + 1)
            let weight = position - Double(lower)
            return GuidancePoint(
                x: points[lower].x * (1 - weight) + points[upper].x * weight,
                y: points[lower].y * (1 - weight) + points[upper].y * weight,
                halfWidth: points[lower].halfWidth * (1 - weight) + points[upper].halfWidth * weight
            )
        }
    }

    private func maximumDistance(_ lhs: [CGPoint], _ rhs: [CGPoint]) -> Double {
        guard lhs.count == rhs.count else { return .infinity }
        return zip(lhs, rhs).map { hypot(Double($0.x - $1.x), Double($0.y - $1.y)) }.max() ?? 0
    }

    private func blend(_ previous: CGPoint, _ current: CGPoint, weight: Double) -> CGPoint {
        CGPoint(
            x: previous.x * (1 - weight) + current.x * weight,
            y: previous.y * (1 - weight) + current.y * weight
        )
    }

    private static func riskLevel(from objects: [LocalPerceptionObject], raw: LocalVisionSignal) -> RiskLevel {
        if objects.contains(where: { $0.kind.isPriorityRisk && ($0.normalizedBoundingBox?.height ?? 0) > 0.35 }) {
            return .high
        }
        if objects.contains(where: \.kind.isPriorityRisk) || raw.hasHuman {
            return .medium
        }
        return .low
    }

    private func blendRects(_ previous: CGRect, _ current: CGRect, weight: Double) -> CGRect {
        CGRect(
            x: previous.minX * (1 - weight) + current.minX * weight,
            y: previous.minY * (1 - weight) + current.minY * weight,
            width: previous.width * (1 - weight) + current.width * weight,
            height: previous.height * (1 - weight) + current.height * weight
        )
    }

    private func iou(_ lhs: CGRect?, _ rhs: CGRect) -> Double {
        guard let lhs else { return 0 }
        let intersection = lhs.intersection(rhs)
        guard !intersection.isNull else { return 0 }
        let overlap = intersection.width * intersection.height
        let union = lhs.width * lhs.height + rhs.width * rhs.height - overlap
        return union > 0 ? Double(overlap / union) : 0
    }
}
