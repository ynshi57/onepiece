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
    private static func largestAnchorComponent(
        width: Int,
        height: Int,
        sample: (Int, Int) -> Double?,
        threshold: Double,
        anchorFraction: Double = 0.15
    ) -> [Bool]? {
        func traversable(_ x: Int, _ y: Int) -> Bool {
            (sample(x, y).map { $0 >= threshold }) ?? false
        }

        var firstRow: Int?
        for y in stride(from: height - 1, through: 0, by: -1) {
            var rowHasFreeSpace = false
            for x in 0..<width where traversable(x, y) {
                rowHasFreeSpace = true
                break
            }
            if rowHasFreeSpace {
                firstRow = y
                break
            }
        }
        guard let anchorBottom = firstRow else { return nil }

        let band = max(1, Int((Double(height) * anchorFraction).rounded()))
        let anchorTop = max(0, anchorBottom - band + 1)
        var visited = Array(repeating: false, count: width * height)
        var bestPixels: [Int] = []
        var bestArea = 0

        for y in stride(from: height - 1, through: 0, by: -1) {
            for x in 0..<width {
                let idx = y * width + x
                if visited[idx] || !traversable(x, y) { continue }

                var stack = [idx]
                var pixels: [Int] = []
                pixels.reserveCapacity(64)
                visited[idx] = true
                var touchesAnchor = false

                while let current = stack.popLast() {
                    pixels.append(current)
                    let cy = current / width
                    let cx = current % width
                    if cy >= anchorTop && cy <= anchorBottom {
                        touchesAnchor = true
                    }

                    let neighbors = [
                        (cx, cy - 1),
                        (cx, cy + 1),
                        (cx - 1, cy),
                        (cx + 1, cy),
                    ]
                    for (nx, ny) in neighbors where nx >= 0 && nx < width && ny >= 0 && ny < height {
                        let nidx = ny * width + nx
                        if !visited[nidx] && traversable(nx, ny) {
                            visited[nidx] = true
                            stack.append(nidx)
                        }
                    }
                }

                if touchesAnchor && pixels.count > bestArea {
                    bestArea = pixels.count
                    bestPixels = pixels
                }
            }
        }

        guard !bestPixels.isEmpty else { return nil }
        var component = Array(repeating: false, count: width * height)
        for idx in bestPixels {
            component[idx] = true
        }
        return component
    }

    /// Trace a free-space centerline through a traversability grid.
    ///
    /// - `sample(x,y)`: traversability at grid cell (top-left origin), nil if invalid.
    /// - `threshold`: values >= threshold are traversable.
    /// Returns a `GuidancePath` in bottom-left-origin normalized coordinates, or
    /// status=insufficient when the free space is too broken (explicit degrade).
    ///
    /// Tracing rule (mirrors `centerline_from_mask` in guidance_path.py): leading
    /// blocked rows at the BOTTOM are skipped to find the start anchor (a driving
    /// frame's hood / immediate foreground must not kill an otherwise clear path),
    /// but an interior gap once tracing has started breaks the line — we never
    /// bridge across an obstacle ahead.
    ///
    /// Performance: this runs on-device per frame. Each sampled row is scanned in a
    /// single O(width) pass that finds the run nearest to the running center
    /// inline — no per-row array allocation, O(1) extra space.
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
        guard let component = largestAnchorComponent(
            width: width,
            height: height,
            sample: sample,
            threshold: threshold
        ) else {
            return GuidancePath(status: .insufficient, coverage: 0, source: source)
        }

        var points: [GuidancePoint] = []
        points.reserveCapacity(count)
        var prevCenter: Double? = nil

        for i in 0..<count {
            let t = Double(i) / Double(count - 1)
            let imgRow = Int((Double(height - 1) + t * (Double(topRow) - Double(height - 1))).rounded())

            // Single pass over runs inside the anchor-reachable component. Cross-frame
            // stability comes from the component selection (largest reachable free
            // space). WITHIN a frame we keep continuity: the anchor row takes the
            // widest run, then each row follows the run nearest the running center.
            // Taking the widest run every row makes the line fold left-right when the
            // component splits into comparable blocks (the GT zigzag).
            var bestStart = -1
            var bestEnd = -1
            var bestScore = Double.greatestFiniteMagnitude
            var runStart = -1
            var x = 0
            while x <= width {
                let isInComponent = x < width && component[imgRow * width + x]
                if isInComponent {
                    if runStart < 0 { runStart = x }
                } else if runStart >= 0 {
                    let center = Double(runStart + x) / 2.0
                    let score: Double
                    if let prev = prevCenter {
                        score = abs(center - prev)                 // nearest to running center
                    } else {
                        score = -Double(x - runStart)              // widest run anchors first row
                    }
                    if score < bestScore {
                        bestScore = score
                        bestStart = runStart
                        bestEnd = x
                    }
                    runStart = -1
                }
                x += 1
            }

            if bestStart < 0 {
                if prevCenter == nil { continue }  // skip leading blocked bottom rows
                break                              // interior gap: stop, never bridge
            }
            let center = Double(bestStart + bestEnd) / 2.0
            let halfW = Double(bestEnd - bestStart) / 2.0
            prevCenter = center
            points.append(
                GuidancePoint(
                    x: center / Double(width),
                    y: 1.0 - (Double(imgRow) + 0.5) / Double(height),
                    halfWidth: halfW / Double(width)
                )
            )
        }

        let coverage = Double(points.count) / Double(count)
        if points.count < GuidancePath.minPoints || coverage < GuidancePath.minCoverage {
            return GuidancePath(status: .insufficient, coverage: coverage, source: source)
        }
        let confidence = min(1.0, coverage)
        let line = GuidanceLine(points: points, confidence: confidence, kind: "primary")
        return GuidancePath(status: .ok, coverage: coverage, lines: [line], source: source)
    }
}
