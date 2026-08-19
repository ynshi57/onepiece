import CoreGraphics
import Foundation

/// Traversable guidance-line representation for VQASee.
///
/// This is the on-device mirror of `server-vqa/app/guidance_path.py`. The engine
/// produces predicted guidance lines from its segmentation + object perception;
/// the server produces ground-truth lines from a dataset traversability mask.
/// Both speak the same wire schema so the closed loop can score them fairly.
///
/// Coordinate convention (matches ROIs / object boxes): normalized image
/// coordinates, origin BOTTOM-LEFT, y up. A line starts near the user's feet
/// (small y) and extends forward (larger y). Keep wire keys/defaults in sync with
/// the Python schema (guarded by `tests/test_guidance_path_swift_parity.py`).

enum GuidancePathStatus: String, Sendable, Equatable {
    case ok
    case insufficient
}

struct GuidancePoint: Sendable, Equatable {
    var x: Double
    var y: Double
    var halfWidth: Double
}

struct GuidanceRiskSegment: Sendable, Equatable {
    var fromIndex: Int
    var toIndex: Int
    var reason: String
}

struct GuidanceLine: Sendable, Equatable {
    var points: [GuidancePoint]
    var confidence: Double = 0
    var kind: String = "primary"
    var riskSegments: [GuidanceRiskSegment] = []
}

struct GuidancePath: Sendable, Equatable {
    var status: GuidancePathStatus
    var coverage: Double
    var lines: [GuidanceLine] = []
    var source: String = ""

    static let insufficient = GuidancePath(status: .insufficient, coverage: 0)

    var primary: GuidanceLine? {
        lines.first(where: { $0.kind == "primary" }) ?? lines.first
    }
}

// MARK: - Wire format (snake_case, matches guidance_path.py to_dict exactly)

extension GuidancePath {
    /// A line needs at least this vertical coverage AND this many points to be ok.
    static let minCoverage = 0.30
    static let minPoints = 3

    func toWire() -> [String: Any] {
        [
            "status": status.rawValue,
            "coverage": (coverage * 100000).rounded() / 100000,
            "source": source,
            "lines": lines.map { line in
                [
                    "kind": line.kind,
                    "confidence": (line.confidence * 100000).rounded() / 100000,
                    "points": line.points.map { point in
                        [
                            "x": (point.x * 100000).rounded() / 100000,
                            "y": (point.y * 100000).rounded() / 100000,
                            "half_width": (point.halfWidth * 100000).rounded() / 100000,
                        ]
                    },
                    "risk_segments": line.riskSegments.map { seg in
                        ["from_index": seg.fromIndex, "to_index": seg.toIndex, "reason": seg.reason] as [String: Any]
                    },
                ] as [String: Any]
            },
        ]
    }
}

// MARK: - Centerline generation (mirrors centerline_from_mask in guidance_path.py)

enum GuidancePathBuilder {
    /// Contiguous "traversable" runs in a grid row as (start, endExclusive).
    private static func runs(rowTraversable: [Bool]) -> [(Int, Int)] {
        var result: [(Int, Int)] = []
        var start: Int?
        for (i, val) in rowTraversable.enumerated() {
            if val, start == nil {
                start = i
            } else if !val, let s = start {
                result.append((s, i))
                start = nil
            }
        }
        if let s = start { result.append((s, rowTraversable.count)) }
        return result
    }

    /// Trace a free-space centerline through a traversability grid.
    ///
    /// - `sample(x,y)`: traversability at grid cell (top-left origin), nil if invalid.
    /// - `threshold`: values >= threshold are traversable.
    /// Returns a `GuidancePath` in bottom-left-origin normalized coordinates, or
    /// status=insufficient when the free space is too broken (explicit degrade).
    static func centerline(
        width: Int,
        height: Int,
        sample: (Int, Int) -> Double?,
        threshold: Double,
        samples: Int = 16,
        horizon: Double = 0.55,
        source: String = ""
    ) -> GuidancePath {
        guard width >= 2, height >= 2 else {
            return GuidancePath(status: .insufficient, coverage: 0, source: source)
        }
        let clampedHorizon = min(max(horizon, 0.05), 1.0)
        var topRow = Int((Double(height) * (1.0 - clampedHorizon)).rounded())
        topRow = max(0, min(topRow, height - 2))

        let count = max(2, samples)
        var rowIndices: [Int] = []
        for i in 0..<count {
            let t = Double(i) / Double(count - 1)
            let value = Double(height - 1) + t * (Double(topRow) - Double(height - 1))
            rowIndices.append(Int(value.rounded()))
        }

        var points: [GuidancePoint] = []
        var prevCenter: Double? = nil
        for imgRow in rowIndices {
            var rowMask = [Bool](repeating: false, count: width)
            for x in 0..<width {
                if let v = sample(x, imgRow), v >= threshold { rowMask[x] = true }
            }
            let rowRuns = runs(rowTraversable: rowMask)
            if rowRuns.isEmpty { break }
            let target = prevCenter ?? (Double(width) * 0.5)
            let best = rowRuns.min(by: { a, b in
                abs((Double(a.0 + a.1) / 2.0) - target) < abs((Double(b.0 + b.1) / 2.0) - target)
            })!
            let center = Double(best.0 + best.1) / 2.0
            let halfW = Double(best.1 - best.0) / 2.0
            prevCenter = center
            points.append(
                GuidancePoint(
                    x: center / Double(width),
                    y: 1.0 - (Double(imgRow) + 0.5) / Double(height),
                    halfWidth: halfW / Double(width)
                )
            )
        }

        let coverage = Double(points.count) / Double(rowIndices.count)
        if points.count < GuidancePath.minPoints || coverage < GuidancePath.minCoverage {
            return GuidancePath(status: .insufficient, coverage: coverage, source: source)
        }
        let confidence = min(1.0, coverage)
        let line = GuidanceLine(points: points, confidence: confidence, kind: "primary")
        return GuidancePath(status: .ok, coverage: coverage, lines: [line], source: source)
    }
}
