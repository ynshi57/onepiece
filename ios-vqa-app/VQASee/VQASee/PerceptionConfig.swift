import CoreGraphics
import Foundation

/// Runtime perception configuration: the tunable ROI rectangles and decision
/// thresholds used by `LocalPathGuidanceEngine` and the segmentation cue reader.
///
/// This mirrors the server single source of truth in
/// `server-vqa/app/perception_config.py`. Defaults MUST equal the compiled-in
/// constants so adopting the config changes nothing until a value is
/// deliberately tuned and the version bumped. The macOS offline harness and the
/// OTA path both flow through this same struct.
struct PerceptionThresholds: Equatable, Sendable {
    var nearBlockedArea: Double
    var sideBlockedArea: Double
    var segNearCautionRatio: Double
    var segSideCautionRatio: Double
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
    var nearROI: CGRect
    var leftROI: CGRect
    var rightROI: CGRect
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
    /// Swap-point for the road-surface model. `twinlite` is the experimental App
    /// overlay (BDD drivable + lanes). `mc5` is the CamVid role-conditioned head.
    /// `off` runs neither.
    var roadBackend: RoadBackendID = .twinlite

    /// Single source of default values. ROIs reuse the engine constants so there
    /// is exactly one place defining the shipping defaults.
    static let `default` = PerceptionConfig(
        version: 1,
        nearROI: LocalPathGuidanceEngine.nearPathROI,
        leftROI: LocalPathGuidanceEngine.leftFrontROI,
        rightROI: LocalPathGuidanceEngine.rightFrontROI,
        thresholds: PerceptionThresholds(
            nearBlockedArea: 0.82,
            sideBlockedArea: 0.86,
            segNearCautionRatio: 0.35,
            segSideCautionRatio: 0.30,
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
        var near_blocked_area: Double
        var side_blocked_area: Double
        var seg_near_caution_ratio: Double
        var seg_side_caution_ratio: Double
        var seg_traversable_pixel: Double
    }
    var version: Int
    var updated_at: String?
    var hash: String?
    var roi: ROISet
    var thresholds: ThresholdsWire
    /// Optional for forward/backward compatibility: an older payload without a
    /// role decodes to the pedestrian default; an explicit unknown value is
    /// rejected (never silently coerced).
    var role: String?
    /// Optional staged-rollout switch for the N=5 segmenter; absent → false.
    var use_multiclass_segmentation: Bool?
    /// Optional switch for the dedicated lane segmenter; absent → true (bundled).
    var use_lane_segmentation: Bool?
    /// Optional road-surface backend id; absent → twinlite (experimental App default).
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
    init(wire: PerceptionConfigWire) throws {
        guard wire.version >= 1 else {
            throw PerceptionConfigError.outOfRange("version=\(wire.version) must be >= 1")
        }

        func rect(_ roi: PerceptionConfigWire.ROIWire, _ name: String) throws -> CGRect {
            for (key, value) in [("x", roi.x), ("y", roi.y), ("w", roi.w), ("h", roi.h)] {
                if !(0.0...1.0).contains(value) {
                    throw PerceptionConfigError.outOfRange("roi.\(name).\(key)=\(value) not in [0,1]")
                }
            }
            if roi.w <= 0 || roi.h <= 0 {
                throw PerceptionConfigError.outOfRange("roi.\(name) width/height must be > 0")
            }
            if roi.x + roi.w > 1.000001 {
                throw PerceptionConfigError.outOfRange("roi.\(name) x+w=\(roi.x + roi.w) exceeds 1")
            }
            if roi.y + roi.h > 1.000001 {
                throw PerceptionConfigError.outOfRange("roi.\(name) y+h=\(roi.y + roi.h) exceeds 1")
            }
            return CGRect(x: roi.x, y: roi.y, width: roi.w, height: roi.h)
        }

        let thresholdPairs: [(String, Double)] = [
            ("near_blocked_area", wire.thresholds.near_blocked_area),
            ("side_blocked_area", wire.thresholds.side_blocked_area),
            ("seg_near_caution_ratio", wire.thresholds.seg_near_caution_ratio),
            ("seg_side_caution_ratio", wire.thresholds.seg_side_caution_ratio),
            ("seg_traversable_pixel", wire.thresholds.seg_traversable_pixel),
        ]
        for (key, value) in thresholdPairs {
            if !(0.0...1.0).contains(value) {
                throw PerceptionConfigError.outOfRange("thresholds.\(key)=\(value) not in [0,1]")
            }
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
            nearROI: try rect(wire.roi.near, "near"),
            leftROI: try rect(wire.roi.left, "left"),
            rightROI: try rect(wire.roi.right, "right"),
            thresholds: PerceptionThresholds(
                nearBlockedArea: wire.thresholds.near_blocked_area,
                sideBlockedArea: wire.thresholds.side_blocked_area,
                segNearCautionRatio: wire.thresholds.seg_near_caution_ratio,
                segSideCautionRatio: wire.thresholds.seg_side_caution_ratio,
                segTraversablePixel: wire.thresholds.seg_traversable_pixel
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
