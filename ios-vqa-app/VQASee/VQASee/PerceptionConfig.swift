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

struct PerceptionConfig: Equatable, Sendable {
    var version: Int
    var nearROI: CGRect
    var leftROI: CGRect
    var rightROI: CGRect
    var thresholds: PerceptionThresholds

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
        )
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
            )
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
