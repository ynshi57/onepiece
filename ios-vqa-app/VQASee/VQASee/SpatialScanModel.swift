import Foundation

nonisolated struct SpatialPoint: Codable, Sendable, Equatable {
    var x: Float
    var y: Float
    var z: Float
    var isFinite: Bool { x.isFinite && y.isFinite && z.isFinite }
}

nonisolated struct SpatialPose: Codable, Sendable, Equatable {
    var time: Double
    var position: SpatialPoint
    var forward: SpatialPoint
    var epoch: Int
}

/// Vertices describe only the observed boundary. No closing edge or complete wall is implied.
nonisolated struct SpatialSurface: Codable, Sendable, Equatable {
    var id: String
    var kind: String
    var vertices: [SpatialPoint]
    var observed: Bool = true
    var observationTime: Double = 0
    var epoch: Int = 0
}

nonisolated struct SpatialKeyframe: Codable, Sendable, Equatable {
    var id: String
    var time: Double
    var pose: SpatialPose
    var intrinsics: [Float]
    var width: Int
    var height: Int
    var imageFile: String
    var cameraToWorld: [Float] = []
    /// Presentation metadata only. JPEG pixels, intrinsics and cameraToWorld stay in native image coordinates.
    /// Missing on earlier scans or when the active interface orientation cannot be established.
    var displayOrientation: String? = nil
    /// ARFrame displayTransform: [a,b,c,d,tx,ty], normalized native image -> normalized viewport.
    var displayTransform: [Float]? = nil
    /// Window viewport width and height in points at the instant of capture.
    var displayViewport: [Float]? = nil
    /// ARCamera exposure at capture. Missing on earlier scans.
    var exposureDuration: Double? = nil
    var exposureOffset: Float? = nil
    /// True only when exposure and white balance were both locked for this frame; nil means unknown.
    var photometricLocked: Bool? = nil

    var hasKnownDisplayOrientation: Bool { displayOrientation != nil && displayTransform != nil && displayViewport != nil }

    var hasValidDisplayMetadata: Bool {
        if displayOrientation == nil && displayTransform == nil && displayViewport == nil { return true }
        guard let orientation = displayOrientation,
              ["portrait", "portraitUpsideDown", "landscapeLeft", "landscapeRight"].contains(orientation),
              let transform = displayTransform, transform.count == 6, transform.allSatisfy(\.isFinite),
              (transform[0] * transform[3] - transform[1] * transform[2]).isFinite,
              abs(transform[0] * transform[3] - transform[1] * transform[2]) > 0.00000001,
              let viewport = displayViewport, viewport.count == 2,
              viewport.allSatisfy({ $0.isFinite && $0 > 0 }) else { return false }
        return true
    }
}

/// Frame times are capture timestamps; lifecycle times are sampled system uptime.
/// Legacy string events are never assigned invented timestamps.
nonisolated struct SpatialTrackingEvent: Codable, Sendable, Equatable {
    enum Kind: String, Codable, Sendable {
        case started, trackingChanged, mappingChanged, trackingGap, trackingRecovered
        case anchorsRemoved, stopped, interrupted, failed
    }
    enum TimeSource: String, Codable, Sendable { case frame, systemUptime }
    enum TrackingState: String, Codable, Sendable { case normal, limited, notAvailable, unknown }
    enum TrackingReason: String, Codable, Sendable {
        case initializing, excessiveMotion, insufficientFeatures, relocalizing, interrupted, failed, unknown
    }
    enum MappingStatus: String, Codable, Sendable { case notAvailable, limited, extending, mapped, unknown }
    var kind: Kind
    var time: Double
    var timeSource: TimeSource
    var epoch: Int
    var trackingState: TrackingState? = nil
    var trackingReason: TrackingReason? = nil
    var mappingStatus: MappingStatus? = nil
    var removedAnchorIDs: [String]? = nil
    var detail: String? = nil
}

/// A per-image model candidate, not an object identity or a 3D position.
nonisolated struct SpatialObjectDetection: Codable, Sendable, Equatable {
    var label: String
    /// Uncalibrated model score; not measured accuracy.
    var score: Float
    /// [left, top, right, bottom], normalized in the archived native JPEG axes.
    var boundingBox: [Float]
    /// Original model output, retained when intersected with the visible image.
    var rawBoundingBox: [Float]? = nil
    var isClipped: Bool { rawBoundingBox.map { $0 != boundingBox } ?? false }
}

nonisolated struct SpatialObjectObservation: Codable, Sendable, Equatable {
    enum Status: String, Codable, Sendable { case processed, partial, failed }
    /// New analyses have stable provenance; nil retains legacy uncertainty.
    var attemptID: UUID? = nil
    var frameID: String
    var epoch: Int
    var frameTime: Double
    var status: Status
    var failure: String? = nil
    var elapsedSeconds: Double
    var detections: [SpatialObjectDetection]
    var modelID: String
    /// Prior outcomes stay attached when a frame is explicitly retried.
    var previousAttempts: [SpatialObjectObservation]? = nil
    /// Per-candidate failures retain raw invalid values in their diagnostic text.
    var detectionFailures: [String]? = nil
}

/// Two-view measurements inside a detection ROI. These may describe background,
/// and are never an object centre, identity or confirmed location.
nonisolated struct SpatialObjectSupportAttempt: Codable, Sendable, Equatable, Identifiable {
    enum Status: String, Codable, Sendable { case measured, unavailable, failed }
    var id: UUID = UUID()
    var sourceFrameID: String
    var sourceAttemptID: UUID
    var detectionIndex: Int
    var targetFrameID: String?
    var epoch: Int
    var status: Status
    var reason: String
    var elapsedSeconds: Double
    /// Full-pair grid denominator; 0 means no optical-flow evaluation completed.
    var pairGridCount: Int
    var pairMatchCount: Int
    var sampledMatchCount: Int
    var supports: [SpatialSurfaceSupport]
    var method: String = "VisionOpticalFlow/revision2/high/two-view-v1"
}

nonisolated struct SpatialScanDocument: Codable, Sendable, Equatable {
    var id: UUID = UUID()
    var createdAt: Date = Date()
    var poses: [SpatialPose] = []
    var surfaces: [SpatialSurface] = []
    var events: [String] = []
    /// nil means this legacy scan has no timestamped tracking evidence.
    var trackingEvents: [SpatialTrackingEvent]? = nil
    var keyframes: [SpatialKeyframe] = []
    var worldMap: SpatialWorldMapReference? = nil
    var relocalizationEvents: [SpatialRelocalizationEvent]? = nil
    /// nil means no local object analysis was recorded; not evidence of an empty room.
    var objectObservations: [SpatialObjectObservation]? = nil
    var objectSupportAttempts: [SpatialObjectSupportAttempt]? = nil
    var indoorSemanticObservations: [SpatialIndoorSemanticObservation]? = nil
    var schemaVersion: Int = 1
}

nonisolated enum SpatialScanGeometry {
    static func distance(_ a: SpatialPoint, _ b: SpatialPoint) -> Float {
        let dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z
        return sqrt(dx * dx + dy * dy + dz * dz)
    }

    static func shouldSample(previous: SpatialPose?, candidate: SpatialPose,
                             minimumDistance: Float = 0.15, minimumAngle: Float = .pi / 18,
                             maximumInterval: Double = 1) -> Bool {
        guard candidate.position.isFinite, candidate.forward.isFinite, candidate.time.isFinite else { return false }
        guard let previous else { return true }
        if previous.epoch != candidate.epoch { return true }
        guard candidate.time > previous.time else { return false }
        if distance(previous.position, candidate.position) >= minimumDistance { return true }
        let a = previous.forward, b = candidate.forward
        let normA = sqrt(a.x*a.x + a.y*a.y + a.z*a.z)
        let normB = sqrt(b.x*b.x + b.y*b.y + b.z*b.z)
        if normA > 0, normB > 0 {
            let cosine = max(-1, min(1, (a.x*b.x + a.y*b.y + a.z*b.z) / (normA*normB)))
            if acos(cosine) >= minimumAngle { return true }
        }
        return candidate.time - previous.time >= maximumInterval
    }

    /// Tracking resets, time gaps and impossible jumps never create connecting lines.
    static func trajectorySegments(_ poses: [SpatialPose], maximumGap: Double = 2,
                                   maximumSpeed: Float = 5) -> [[SpatialPose]] {
        var segments: [[SpatialPose]] = []
        var current: [SpatialPose] = []
        for pose in poses {
            guard pose.position.isFinite, pose.forward.isFinite, pose.time.isFinite else {
                if !current.isEmpty { segments.append(current); current = [] }
                continue
            }
            if let previous = current.last {
                let dt = pose.time - previous.time
                if pose.epoch != previous.epoch || dt <= 0 || dt > maximumGap
                    || distance(previous.position, pose.position) / Float(dt) > maximumSpeed {
                    segments.append(current)
                    current = []
                }
            }
            current.append(pose)
        }
        if !current.isEmpty { segments.append(current) }
        return segments
    }

    /// Largest distance between observed vertices; this is not a room or full wall dimension.
    static func observedBoundarySpan(_ surface: SpatialSurface) -> Float? {
        guard surface.observed, surface.vertices.count >= 2,
              surface.vertices.allSatisfy(\.isFinite) else { return nil }
        var span: Float = 0
        for i in surface.vertices.indices {
            for j in surface.vertices.indices where j > i {
                span = max(span, distance(surface.vertices[i], surface.vertices[j]))
            }
        }
        return span
    }
}

/// Secure ARKit archive is local evidence, not an independently accurate room model.
nonisolated struct SpatialWorldMapReference: Codable, Sendable, Equatable {
    var filename: String = "world-map.archive"
    var sha256: String
    var referenceEpoch: Int
    var capturedAt: Double
    var mappingStatus: String
    var referenceSurfaces: [SpatialSurface]
}
nonisolated struct SpatialRelocalizationEvent: Codable, Sendable, Equatable {
    var time: Double
    var state: String
    var sourceEpoch: Int
    var newEpoch: Int? = nil
    var matchedPlaneCount: Int? = nil
    var maxPlaneDistance: Float? = nil
    var detail: String? = nil
}

nonisolated enum SpatialMapObservationPolicy {
    /// UUID identifies the same map landmark; it does not date an observation.
    /// A loaded didAdd callback is not evidence, a fresh didUpdate may be.
    static func accepts(savedAnchor: Bool, updated: Bool, trackingNormal: Bool,
                        elapsed: Double, intersectsCameraView: Bool) -> Bool {
        trackingNormal && elapsed.isFinite && elapsed >= 1 && intersectsCameraView && (!savedAnchor || updated)
    }
}

nonisolated enum SpatialMapGeometry {
    struct MatchResult { var count: Int; var sufficient: Bool; var maxDistance: Float }
    private static func description(_ s: SpatialSurface) -> (center: SIMD3<Float>, normal: SIMD3<Float>, radius: Float)? {
        guard s.vertices.count >= 3, s.vertices.allSatisfy(\.isFinite) else { return nil }
        let p = s.vertices.map { SIMD3<Float>($0.x,$0.y,$0.z) }
        let center = p.reduce(SIMD3<Float>.zero,+) / Float(p.count)
        var n = SIMD3<Float>.zero
        for i in p.indices { n += cross(p[i]-center,p[(i+1)%p.count]-center) }
        let length = sqrt(dot(n,n)); guard length > 0.02 else { return nil }
        return (center,n/length,p.map { sqrt(dot($0-center,$0-center)) }.max() ?? 0)
    }
    private static func dot(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> Float { a.x*b.x+a.y*b.y+a.z*b.z }
    private static func cross(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> SIMD3<Float> { SIMD3(a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x) }
    static func matches(_ references: [SpatialSurface], _ observed: [SpatialSurface]) -> MatchResult {
        var normals: [SIMD3<Float>] = []; var used = Set<String>(); var maximum: Float = 0
        for candidate in observed {
            guard let c = description(candidate) else { continue }
            let possible = references.compactMap { reference -> (String,SIMD3<Float>,Float)? in
                guard !used.contains(reference.id), reference.kind == candidate.kind, let r = description(reference), abs(dot(c.normal,r.normal)) > 0.97 else { return nil }
                let delta = c.center-r.center
                let distance = abs(dot(delta,r.normal))
                let lateral = sqrt(max(0,dot(delta,delta)-distance*distance))
                guard distance < 0.08, lateral < max(0.1, min(c.radius+r.radius,1.5)) else { return nil }
                return (reference.id,r.normal,distance)
            }.sorted { $0.2 < $1.2 }
            if let match = possible.first { used.insert(match.0); normals.append(match.1); maximum = max(maximum,match.2) }
        }
        let nonparallel = normals.indices.contains { i in normals.indices.contains { j in i != j && abs(dot(normals[i],normals[j])) < 0.85 } }
        return MatchResult(count:normals.count,sufficient:normals.count >= 2 && nonparallel,maxDistance:maximum)
    }
}

/// Incremental display geometry only. It does not invent samples, join epochs,
/// or replace the independently persisted pose history.
nonisolated struct SpatialTrajectoryAppendCache {
    struct Change { var reset: Bool; var edges: [(SpatialPose, SpatialPose)] }
    private var scanID: UUID?
    private var count = 0
    private var first: SpatialPose?
    private var last: SpatialPose?

    mutating func consume(_ document: SpatialScanDocument) -> Change {
        let values = document.poses
        let reset = scanID != document.id || values.count < count
            || (count > 0 && (values.first != first || values[count - 1] != last))
        let start = reset ? 0 : count
        var edges: [(SpatialPose, SpatialPose)] = []
        if values.count > max(1, start) {
            for index in max(1, start)..<values.count {
                let a = values[index - 1], b = values[index]
                if Self.canConnect(a, b) { edges.append((a, b)) }
            }
        }
        scanID = document.id; count = values.count; first = values.first; last = values.last
        return Change(reset: reset, edges: edges)
    }

    static func canConnect(_ a: SpatialPose, _ b: SpatialPose) -> Bool {
        let dt = b.time - a.time
        return a.position.isFinite && b.position.isFinite && a.forward.isFinite && b.forward.isFinite
            && a.epoch == b.epoch && dt > 0 && dt <= 2
            && SpatialScanGeometry.distance(a.position, b.position) / Float(dt) <= 5
    }
}
