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

    // MARK: - UFLDv2 lane polylines → ego guidance path (Phase B primary source)

    /// Build a traversable centerline from UFLD row-anchor lane polylines.
    ///
    /// CULane ego lane is bounded by row indices 1 and 2; the product path is the
    /// midpoint between those lines when both exist. UFLD points use top-left origin;
    /// output uses bottom-left Vision coordinates like `centerline`.
    static func fromUFLDPolylines(
        _ lanes: [LanePolyline],
        egoX: Double = 0.5,
        horizon: Double = 0.55,
        samples: Int = 16,
        source: String = "ufld"
    ) -> GuidancePath? {
        let rowLanes = lanes.filter { $0.source == .rowAnchor && $0.points.count >= 2 }
        guard !rowLanes.isEmpty else { return nil }

        let left = rowLanes.first { $0.laneIndex == 1 }
        let right = rowLanes.first { $0.laneIndex == 2 }
        if let left, let right {
            return midlineBetween(
                left: left,
                right: right,
                horizon: horizon,
                samples: samples,
                source: source
            )
        }
        if let boundary = bestSingleRowLane(rowLanes, egoX: egoX) {
            return fromSingleBoundary(
                boundary,
                egoX: egoX,
                horizon: horizon,
                samples: samples,
                source: source
            )
        }
        return nil
    }

    private static func midlineBetween(
        left: LanePolyline,
        right: LanePolyline,
        horizon: Double,
        samples: Int,
        source: String
    ) -> GuidancePath? {
        let yMin = max(left.points.map(\.y).min() ?? 0, right.points.map(\.y).min() ?? 0)
        let yMax = min(left.points.map(\.y).max() ?? 0, right.points.map(\.y).max() ?? 0)
        guard yMax > yMin else { return nil }

        let clampedHorizon = min(max(horizon, 0.05), 1.0)
        let yTopTL = 1.0 - clampedHorizon
        let count = max(2, samples)
        var points: [GuidancePoint] = []
        points.reserveCapacity(count)

        for i in 0..<count {
            let t = Double(i) / Double(count - 1)
            let yTL = yMin + t * (yMax - yMin)
            if yTL < yTopTL { continue }
            guard let xLeft = interpolateX(atY: Double(yTL), in: left.points),
                  let xRight = interpolateX(atY: Double(yTL), in: right.points) else {
                continue
            }
            let xMid = (xLeft + xRight) / 2.0
            let halfW = abs(xRight - xLeft) / 2.0
            guard halfW.isFinite, halfW > 0.001 else { continue }
            points.append(
                GuidancePoint(
                    x: min(max(xMid, 0.0), 1.0),
                    y: 1.0 - yTL,
                    halfWidth: min(max(halfW, 0.005), 0.5)
                )
            )
        }

        return finalizePolylinePath(points: points, samples: count, source: source)
    }

    private static func fromSingleBoundary(
        _ lane: LanePolyline,
        egoX: Double,
        horizon: Double,
        samples: Int,
        source: String
    ) -> GuidancePath? {
        let sorted = lane.points.sorted { $0.y < $1.y }
        guard let yMin = sorted.first?.y, let yMax = sorted.last?.y, yMax > yMin else {
            return nil
        }

        let defaultHalfLane = 0.035
        let inward: Double
        if lane.laneIndex == 1 {
            inward = defaultHalfLane
        } else if lane.laneIndex == 2 {
            inward = -defaultHalfLane
        } else {
            let bottomX = Double(sorted.last?.x ?? 0.5)
            inward = bottomX <= egoX ? defaultHalfLane : -defaultHalfLane
        }

        let clampedHorizon = min(max(horizon, 0.05), 1.0)
        let yTopTL = 1.0 - clampedHorizon
        let count = max(2, samples)
        var points: [GuidancePoint] = []
        points.reserveCapacity(count)

        for i in 0..<count {
            let t = Double(i) / Double(count - 1)
            let yTL = Double(yMin) + t * Double(yMax - yMin)
            if yTL < yTopTL { continue }
            guard let xBoundary = interpolateX(atY: yTL, in: sorted) else { continue }
            let xCenter = min(max(xBoundary + inward, 0.0), 1.0)
            points.append(
                GuidancePoint(
                    x: xCenter,
                    y: 1.0 - yTL,
                    halfWidth: defaultHalfLane
                )
            )
        }

        return finalizePolylinePath(points: points, samples: count, source: source)
    }

    private static func bestSingleRowLane(_ lanes: [LanePolyline], egoX: Double) -> LanePolyline? {
        lanes
            .filter { $0.points.count >= GuidancePath.minPoints }
            .max { lhs, rhs in
                let lhsScore = singleLaneScore(lhs, egoX: egoX)
                let rhsScore = singleLaneScore(rhs, egoX: egoX)
                if lhsScore != rhsScore { return lhsScore < rhsScore }
                return lhs.points.count < rhs.points.count
            }
    }

    private static func singleLaneScore(_ lane: LanePolyline, egoX: Double) -> Double {
        let bottom = lane.points.max(by: { $0.y < $1.y })
        let bottomX = Double(bottom?.x ?? 0.5)
        let indexBonus: Double
        switch lane.laneIndex {
        case 1, 2: indexBonus = 0.1
        default: indexBonus = 0
        }
        return Double(lane.points.count) + indexBonus - abs(bottomX - egoX)
    }

    private static func interpolateX(atY y: Double, in points: [CGPoint]) -> Double? {
        guard !points.isEmpty else { return nil }
        let sorted = points.sorted { $0.y < $1.y }
        if y <= Double(sorted[0].y) { return Double(sorted[0].x) }
        if y >= Double(sorted[sorted.count - 1].y) { return Double(sorted[sorted.count - 1].x) }
        for index in 0..<(sorted.count - 1) {
            let a = sorted[index]
            let b = sorted[index + 1]
            let ay = Double(a.y)
            let by = Double(b.y)
            if ay <= y, y <= by {
                if by == ay { return Double(a.x) }
                let t = (y - ay) / (by - ay)
                return Double(a.x) + t * Double(b.x - a.x)
            }
        }
        return nil
    }

    private static func finalizePolylinePath(
        points: [GuidancePoint],
        samples: Int,
        source: String
    ) -> GuidancePath? {
        let ordered = points.sorted { $0.y < $1.y }
        let coverage = Double(ordered.count) / Double(max(2, samples))
        guard ordered.count >= GuidancePath.minPoints, coverage >= GuidancePath.minCoverage else {
            return nil
        }
        let confidence = min(1.0, coverage)
        let line = GuidanceLine(points: ordered, confidence: confidence, kind: "primary")
        return GuidancePath(status: .ok, coverage: coverage, lines: [line], source: source)
    }
}
