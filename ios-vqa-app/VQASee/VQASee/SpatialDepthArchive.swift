import Foundation

/// Archive-only depth types. Old scans still decode; no new whole-image depth is computed.

nonisolated enum SpatialDepthPixelState: String, Codable, Sendable, Equatable {
    case valid, unavailable, lowTexture, outsideView, invalidFlow, cycleInconsistent, photometricInconsistent
    case invalidGeometry, inconsistentDepth, reflectiveSurfaceUnknown
}

nonisolated enum SpatialDepthMaterialPolicy: String, Codable, Sendable, Equatable {
    case notAssessed, windowMirrorCandidatesExcluded
}

nonisolated struct SpatialDepthObservation: Codable, Sendable, Equatable {
    enum Status: String, Codable, Sendable { case processed, failed }
    var attemptID = UUID()
    var sourceFrameID: String
    var sourceTime: Double
    var epoch: Int
    var targetFrameIDs: [String] = []
    var materialPolicy: SpatialDepthMaterialPolicy = .notAssessed
    var method = "VisionFlow-r2-rotation-NCC-two-target-64x48-v2"
    var status: Status = .failed
    var failure: String? = nil
    var elapsedSeconds: Double = 0
    var stageSeconds: [String: Double] = [:]
    var width = 64
    var height = 48
    var depthMeters: [Float] = Array(repeating: 0, count: 64 * 48)
    var pixelStates: [SpatialDepthPixelState] = Array(repeating: .unavailable, count: 64 * 48)
    var backgroundDepthMeters: [Float]? = nil
    var previousAttempts: [SpatialDepthObservation]? = nil
}

nonisolated struct SpatialDepthPairDiagnostics: Codable, Sendable, Equatable {
    var sourceFrameID: String
    var targetFrameID: String
    var candidateCount: Int
    var reasonCounts: [String: Int]
    var warpValidPixelCount: Int
    var warpPixelCount: Int
    var stageSeconds: [String: Double]
    var geometryReasonCounts: [String: Int] = [:]
}

nonisolated struct SpatialDepthGridResult: Sendable {
    var grid: [SpatialDepthGridMatch]
    var diagnostics: SpatialDepthPairDiagnostics
}

nonisolated struct SpatialDepthGridMatch: Sendable {
    var match: SpatialImageMatch?
    var state: SpatialDepthPixelState
}

nonisolated struct SpatialRelativeDepthObservation: Codable, Sendable, Equatable {
    enum Status: String, Codable, Sendable { case processed, failed }
    static let modelID = "apple/DepthAnythingV2SmallF16@cfef6f6f2a70783dedc0bfae40cecbc2052285d3"
    var attemptID = UUID()
    var frameID: String
    var frameTime: Double
    var epoch: Int
    var modelID: String = Self.modelID
    var status: Status = .failed
    var failure: String? = nil
    var width = 64
    var height = 48
    var relativeInverseDepth: [Float] = []
    var stageSeconds: [String: Double] = [:]
    var totalSeconds: Double = 0
    var previousAttempts: [SpatialRelativeDepthObservation]? = nil
}
