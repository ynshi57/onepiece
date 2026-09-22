import CoreGraphics
import Foundation

/// Runtime perception configuration: decision thresholds used by the
/// segmentation cue reader and road-backend swap.
///
/// This mirrors the server single source of truth in
/// `server-vqa/app/perception_config.py`. Defaults MUST equal the compiled-in
/// constants so adopting the config changes nothing until a value is
/// deliberately tuned and the version bumped. The macOS offline harness and the
/// OTA path both flow through this same struct.
///
/// Three-zone ROI rectangles (near/left/right) are not a product signal
/// (2026-09-22). Leftover `roi` keys in old OTA payloads are decoded and ignored.
struct PerceptionThresholds: Equatable, Sendable {
    var segTraversablePixel: Double
}

/// Who the walkable/drivable surface is being derived FOR. The same N=5
/// segmentation output yields a DIFFERENT traversable region per role: a walker's
/// surface is the sidewalk, a driver's is the carriageway + lane. This is the core
/// of "可通行区域必须区分人车" — one prediction, role-conditioned derivation.
enum PerceptionRole: String, Equatable, Sendable {
    case pedestrian  // walker / cyclist: primary surface = sidewalk
    case vehicle     // motor vehicle: primary surface = road + lane markings

    /// Segmentation class indices (see `SegClass`: 0 bg, 1 road, 2 sidewalk,
    /// 3 lane, 4 obstacle) that count as THIS role's primary traversable surface.
    var primaryClassIndices: [Int] {
        switch self {
        case .pedestrian: return [SegClass.sidewalk]
        case .vehicle: return [SegClass.road, SegClass.lane]
        }
    }
}

struct PerceptionConfig: Equatable, Sendable {
    var version: Int
    var thresholds: PerceptionThresholds
    /// Role the multiclass segmentation derives the traversable surface for.
    /// Defaults to pedestrian (the safety-critical case T1 quantified: the old
    /// binary model routed walkers onto the road). Binary (C<=2) models ignore it.
    var role: PerceptionRole = .pedestrian
    /// Staged rollout switch for the N=5 role-conditioned segmentation model.
    /// Defaults to FALSE: the live app does **not** run the old binary Fast-SCNN
    /// (it mixed road+sidewalk into one green blob and was the latency hog).
    /// Flipping this (OTA after ANE sign-off) loads bundled mc5 and derives the
    /// walkable region per `role`. Off = no traversable-region forward pass.
    var useMulticlassSegmentation: Bool = false
    var useLaneSegmentation: Bool = true
    /// Swap-point for the road-surface model. `twinlite` is the product default
    /// (BDD drivable + lanes). `mc5` is the CamVid role-conditioned head.
    /// `off` runs neither.
    var roadBackend: RoadBackendID = .twinlite

    static let `default` = PerceptionConfig(
        version: 1,
        thresholds: PerceptionThresholds(
            segTraversablePixel: 0.55
        ),
        role: .pedestrian,
        useMulticlassSegmentation: false,
        useLaneSegmentation: true,
        roadBackend: .twinlite
    )
}

// MARK: - Wire format (matches server-vqa/app/perception_config.py exactly)

/// Decodable representation of the OTA / harness JSON payload. Keys are
/// snake_case to match the Python schema byte-for-byte.
struct PerceptionConfigWire: Codable, Equatable {
    struct ROIWire: Codable, Equatable {
        var x: Double
        var y: Double
        var w: Double
        var h: Double
    }
    struct ROISet: Codable, Equatable {
        var near: ROIWire
        var left: ROIWire
        var right: ROIWire
    }
    struct ThresholdsWire: Codable, Equatable {
        var seg_traversable_pixel: Double
        /// Leftover three-zone keys; decoded so old payloads still load, then ignored.
        var near_blocked_area: Double?
        var side_blocked_area: Double?
        var seg_near_caution_ratio: Double?
        var seg_side_caution_ratio: Double?
    }
    var version: Int
    var updated_at: String?
    var hash: String?
    /// Leftover three-zone boxes; ignored. Absent on new payloads.
    var roi: ROISet?
    var thresholds: ThresholdsWire
    /// Optional for forward/backward compatibility: an older payload without a
    /// role decodes to the pedestrian default; an explicit unknown value is
    /// rejected (never silently coerced).
    var role: String?
    /// Optional staged-rollout switch for the N=5 segmenter; absent → false.
    var use_multiclass_segmentation: Bool?
    /// Optional switch for the dedicated lane segmenter; absent → true (bundled).
    var use_lane_segmentation: Bool?
    /// Optional road-surface backend id; absent → twinlite (product default).
    var road_backend: String?
}

enum PerceptionConfigError: Error, CustomStringConvertible, Equatable {
    case outOfRange(String)

    var description: String {
        switch self {
        case .outOfRange(let detail):
            return "perception config out of range: \(detail)"
        }
    }
}

extension PerceptionConfig {
    /// Build a validated runtime config from the wire payload. Validation mirrors
    /// the Python side so an invalid OTA payload is rejected (never silently
    /// clamped) — the caller is expected to fall back to `.default` visibly.
    /// Leftover `roi` is accepted and discarded.
    init(wire: PerceptionConfigWire) throws {
        guard wire.version >= 1 else {
            throw PerceptionConfigError.outOfRange("version=\(wire.version) must be >= 1")
        }

        let pixel = wire.thresholds.seg_traversable_pixel
        if !(0.0...1.0).contains(pixel) {
            throw PerceptionConfigError.outOfRange("thresholds.seg_traversable_pixel=\(pixel) not in [0,1]")
        }

        let role: PerceptionRole
        if let raw = wire.role {
            guard let parsed = PerceptionRole(rawValue: raw) else {
                throw PerceptionConfigError.outOfRange("role=\(raw) is not one of pedestrian|vehicle")
            }
            role = parsed
        } else {
            role = .pedestrian
        }

        let parsedBackend: RoadBackendID
        if let raw = wire.road_backend {
            guard let parsed = RoadBackendID(rawValue: raw) else {
                throw PerceptionConfigError.outOfRange("road_backend=\(raw) is not one of off|twinlite|mc5")
            }
            parsedBackend = parsed
        } else {
            parsedBackend = .twinlite
        }

        self.init(
            version: wire.version,
            thresholds: PerceptionThresholds(
                segTraversablePixel: pixel
            ),
            role: role,
            useMulticlassSegmentation: wire.use_multiclass_segmentation ?? false,
            useLaneSegmentation: wire.use_lane_segmentation ?? true,
            roadBackend: parsedBackend
        )
    }

    /// Decode + validate from raw JSON. Returns nil on any failure so callers can
    /// fall back to `.default` and surface the failure in the UI.
    static func from(jsonData: Data) -> PerceptionConfig? {
        guard let wire = try? JSONDecoder().decode(PerceptionConfigWire.self, from: jsonData) else {
            return nil
        }
        return try? PerceptionConfig(wire: wire)
    }
}
