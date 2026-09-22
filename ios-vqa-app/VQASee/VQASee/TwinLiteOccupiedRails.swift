import Foundation
import CoreGraphics

/// TwinLite-only occupied-lane rails → product guidance path.
///
/// Mirrors the ``mask_source="twinlite"`` branch of ``occupied_lane_rails`` in
/// ``server-vqa/app/lane_rails.py`` (row-anchor paint rails + curb rails → pair
/// selection → G2 midpoint). UFLD is handled separately on device.
enum TwinLiteOccupiedRails {
    private static let laneMaxTurnDeg = 40.0
    private static let maxProductHeadingDeg = 12.0
    private static let guidanceLambda = 80_000.0

    static func buildGuidancePath(
        lane: [Bool],
        pavement: [Bool],
        width: Int,
        height: Int,
        egoCol: Int
    ) -> GuidancePath {
        guard width > 8, height > 8, lane.count == width * height, pavement.count == width * height else {
            return GuidancePath(status: .insufficient, coverage: 0, source: "twinlite_rails")
        }
        let hoodRows = max(8, Int((0.02 * Double(height)).rounded()))
        let minPaintRows = max(8, height / 16)

        var paint = railsFromLaneMask(
            lane: lane,
            width: width,
            height: height,
            hoodRows: hoodRows
        )
        paint = paint.map { trimBorderGlued($0, width: width) }
        paint = paint.filter { rail in
            let rows = rail.filter(\.isFinite).count
            guard rows >= minPaintRows else { return false }
            let med = median(rail)
            guard med.isFinite else { return true }
            return med > 8.0 && med < Double(width - 9)
        }

        let (leftCurb, rightCurb) = curbRails(
            pavement: pavement,
            width: width,
            height: height,
            hoodRows: hoodRows
        )
        var candidates: [(rail: [Double], kind: String)] = paint.map { ($0, "paint") }
        if !borderRail(leftCurb, width: width) {
            candidates.append((leftCurb, "curb"))
        }
        if !borderRail(rightCurb, width: width) {
            candidates.append((rightCurb, "curb"))
        }

        guard let pair = selectOccupiedPair(
            candidates: candidates,
            egoCol: egoCol,
            width: width,
            height: height,
            hoodRows: hoodRows
        ) else {
            return GuidancePath(status: .insufficient, coverage: 0, source: "twinlite_rails")
        }
        let (left, right, _, _) = trackOccupiedPair(
            left: pair.left,
            right: pair.right,
            leftKind: pair.leftKind,
            rightKind: pair.rightKind,
            lane: lane,
            pavement: pavement,
            width: width,
            height: height,
            egoCol: egoCol,
            hoodRows: hoodRows
        )

        let (mids, imagePoints) = guidanceBetweenRails(
            left: left,
            right: right,
            pavement: pavement,
            width: width,
            height: height,
            lam: guidanceLambda
        )
        let stats = headingCurvatureStats(mids)
        if stats.maxDPsiDeg > maxProductHeadingDeg {
            return GuidancePath(status: .insufficient, coverage: 0, source: "twinlite_rails")
        }

        return guidancePathFromImagePoints(
            imagePoints: imagePoints,
            left: left,
            right: right,
            width: width,
            height: height
        )
    }

    /// Paint-rail strokes for overlay. `twinliteMask` is ignored by UFLD guidance.
    static func displayPolylines(
        lane: [Bool],
        width: Int,
        height: Int
    ) -> [LanePolyline] {
        guard width > 8, height > 8, lane.count == width * height else { return [] }
        let hoodRows = max(8, Int((0.02 * Double(height)).rounded()))
        var paint = railsFromLaneMask(
            lane: lane,
            width: width,
            height: height,
            hoodRows: hoodRows
        )
        paint = paint.map { trimBorderGlued($0, width: width) }
        var out: [LanePolyline] = []
        for (idx, rail) in paint.enumerated() {
            var points: [CGPoint] = []
            points.reserveCapacity(height)
            for y in 0..<height where rail[y].isFinite {
                let x = min(max((rail[y] + 0.5) / Double(width), 0.0), 1.0)
                let yn = min(max((Double(y) + 0.5) / Double(height), 0.0), 1.0)
                points.append(CGPoint(x: x, y: yn))
            }
            guard points.count >= 2 else { continue }
            out.append(LanePolyline(laneIndex: idx, source: .twinliteMask, points: points))
        }
        return out
    }

    /// Paint-rail strokes for overlay. `twinliteMask` is ignored by UFLD guidance.
    static func displayPolylines(
        lane: [Bool],
        width: Int,
        height: Int
    ) -> [LanePolyline] {
        guard width > 8, height > 8, lane.count == width * height else { return [] }
        let hoodRows = max(8, Int((0.02 * Double(height)).rounded()))
        var paint = railsFromLaneMask(
            lane: lane,
            width: width,
            height: height,
            hoodRows: hoodRows
        )
        paint = paint.map { trimBorderGlued($0, width: width) }
        var out: [LanePolyline] = []
        for (idx, rail) in paint.enumerated() {
            var points: [CGPoint] = []
            points.reserveCapacity(height)
            for y in 0..<height where rail[y].isFinite {
                let x = min(max((rail[y] + 0.5) / Double(width), 0.0), 1.0)
                let yn = min(max((Double(y) + 0.5) / Double(height), 0.0), 1.0)
                points.append(CGPoint(x: x, y: yn))
            }
            guard points.count >= 2 else { continue }
            out.append(LanePolyline(laneIndex: idx, source: .twinliteMask, points: points))
        }
        return out
    }

    // MARK: - GuidancePath export

    private static func guidancePathFromImagePoints(
        imagePoints: [(x: Double, y: Double)],
        left: [Double],
        right: [Double],
        width: Int,
        height: Int
    ) -> GuidancePath {
        guard imagePoints.count >= GuidancePath.minPoints else {
            return GuidancePath(status: .insufficient, coverage: 0, source: "twinlite_rails")
        }
        var guidancePoints: [GuidancePoint] = []
        guidancePoints.reserveCapacity(imagePoints.count)
        for (x, y) in imagePoints {
            let row = Int(y.rounded())
            guard row >= 0, row < height else { continue }
            var halfW = 8.0
            if left[row].isFinite, right[row].isFinite {
                halfW = max(4.0, abs(right[row] - left[row]) * 0.5)
            }
            guidancePoints.append(
                GuidancePoint(
                    x: min(max(x / Double(width), 0.0), 1.0),
                    y: 1.0 - (y + 0.5) / Double(height),
                    halfWidth: min(max(halfW / Double(width), 0.005), 0.5)
                )
            )
        }
        guidancePoints.sort { $0.y < $1.y }
        let yMin = guidancePoints.map(\.y).min() ?? 0
        let yMax = guidancePoints.map(\.y).max() ?? 0
        let coverage = yMax - yMin
        guard guidancePoints.count >= GuidancePath.minPoints,
              coverage >= GuidancePath.minCoverage else {
            return GuidancePath(status: .insufficient, coverage: coverage, source: "twinlite_rails")
        }
        let line = GuidanceLine(
            points: guidancePoints,
            confidence: min(1.0, coverage),
            kind: "primary"
        )
        return GuidancePath(status: .ok, coverage: coverage, lines: [line], source: "twinlite_rails")
    }

    // MARK: - Row-anchor rails

    private static func railsFromLaneMask(
        lane: [Bool],
        width: Int,
        height: Int,
        hoodRows: Int
    ) -> [[Double]] {
        var rails: [[Int: Double]] = []
        var last: [(y: Int, x: Double)] = []
        let gap = 6
        let associateDx = 48.0
        let associateDy = 120
        let minRows = 8
        let maxRails = 4

        for y in stride(from: height - 1, through: 0, by: -1) {
            var xs: [Int] = []
            xs.reserveCapacity(width)
            for x in 0..<width where lane[y * width + x] {
                xs.append(x)
            }
            let clusters = clusterColumns(xs, gap: gap)
            var used = Array(repeating: false, count: clusters.count)
            for ri in 0..<last.count {
                let (ly, lx) = last[ri]
                guard ly - y > 0, ly - y <= associateDy else { continue }
                var bestI = -1
                var bestD = associateDx
                for (i, cluster) in clusters.enumerated() where !used[i] {
                    let mid = 0.5 * Double(cluster.0 + cluster.1)
                    let dist = abs(mid - lx)
                    if dist < bestD {
                        bestD = dist
                        bestI = i
                    }
                }
                if bestI >= 0 {
                    used[bestI] = true
                    let mid = 0.5 * Double(clusters[bestI].0 + clusters[bestI].1)
                    rails[ri][y] = mid
                    last[ri] = (y, mid)
                }
            }
            for (i, cluster) in clusters.enumerated() where !used[i] {
                let mid = 0.5 * Double(cluster.0 + cluster.1)
                rails.append([y: mid])
                last.append((y, mid))
            }
        }

        var scored: [(count: Int, rail: [Double])] = []
        for sparse in rails {
            guard sparse.count >= minRows else { continue }
            var xs = Array(repeating: Double.nan, count: height)
            for (y, x) in sparse {
                xs[y] = x
            }
            scored.append((sparse.count, densifyRail(xs, width: width, hoodRows: hoodRows, extraRows: 0)))
        }
        scored.sort { $0.count > $1.count }
        var smooth: [[Double]] = []
        for (_, rail) in scored.prefix(max(maxRails, 6)) {
            smooth.append(contentsOf: splitSharpTurns(rail, width: width))
        }
        smooth.sort { $0.filter(\.isFinite).count > $1.filter(\.isFinite).count }
        return Array(smooth.prefix(maxRails))
    }

    private static func curbRails(
        pavement: [Bool],
        width: Int,
        height: Int,
        hoodRows: Int
    ) -> ([Double], [Double]) {
        var left = Array(repeating: Double.nan, count: height)
        var right = Array(repeating: Double.nan, count: height)
        let border = max(6, Int((0.04 * Double(width)).rounded()))
        for y in 0..<height {
            var xs: [Int] = []
            for x in 0..<width where pavement[y * width + x] {
                xs.append(x)
            }
            let clusters = clusterColumns(xs, gap: 8).filter { $0.1 - $0.0 >= 12 }
            guard let best = clusters.max(by: { $0.1 - $0.0 < $1.1 - $1.0 }) else { continue }
            let lo = best.0
            let hi = best.1
            if lo > border { left[y] = Double(lo) }
            if hi < width - 1 - border { right[y] = Double(hi) }
        }
        return (
            densifyRail(left, width: width, hoodRows: hoodRows, extraRows: 0),
            densifyRail(right, width: width, hoodRows: hoodRows, extraRows: 0)
        )
    }

    private static func selectOccupiedPair(
        candidates: [(rail: [Double], kind: String)],
        egoCol: Int,
        width: Int,
        height: Int,
        hoodRows: Int
    ) -> (left: [Double], right: [Double], leftKind: String, rightKind: String)? {
        guard candidates.count >= 2 else { return nil }
        let ego = min(max(egoCol, 0), width - 1)
        let minSpan = max(8.0, 0.06 * Double(width))
        let maxSpan = max(minSpan + 4.0, 0.62 * Double(width))
        let minInteriorRatio = 0.12

        func valid(_ xL: Double, _ xR: Double) -> Bool {
            let span = xR - xL
            guard span >= minSpan, span <= maxSpan else { return false }
            let interior = min(Double(ego) - xL, xR - Double(ego))
            return interior >= max(4.0, minInteriorRatio * span)
        }

        func hits(at y: Int) -> [(x: Double, idx: Int, rail: [Double], kind: String)] {
            var pts: [(Double, Int, [Double], String)] = []
            for (idx, item) in candidates.enumerated() {
                let x = item.rail[y]
                if x.isFinite {
                    pts.append((x, idx, item.rail, item.kind))
                }
            }
            return pts.sorted { $0.0 < $1.0 }
        }

        var votes: [String: Int] = [:]
        var examples: [String: (left: [Double], right: [Double], leftKind: String, rightKind: String)] = [:]
        let bandLo = max(0, Int((0.48 * Double(height)).rounded()))
        let bandHi = height - 1 - max(0, hoodRows)
        if bandLo <= bandHi {
            for y in stride(from: bandHi, through: bandLo, by: -1) {
                let pts = hits(at: y)
                guard pts.count >= 2 else { continue }
                for i in 0..<(pts.count - 1) {
                    let (xL, iL, rL, kL) = pts[i]
                    let (xR, iR, rR, kR) = pts[i + 1]
                    guard xL <= Double(ego), Double(ego) <= xR, valid(xL, xR) else { continue }
                    let key = "\(iL)-\(iR)"
                    votes[key, default: 0] += 1
                    examples[key] = (rL, rR, kL, kR)
                }
            }
        }

        func pick(keys: [String], minShare: Double = 0) -> (left: [Double], right: [Double], leftKind: String, rightKind: String)? {
            guard !keys.isEmpty, !votes.isEmpty else { return nil }
            let key = keys.max { votes[$0, default: 0] < votes[$1, default: 0] }!
            let floor = height >= 40 ? 6 : 1
            guard votes[key, default: 0] >= floor else { return nil }
            if minShare > 0 {
                let maxVote = votes.values.max() ?? 0
                if Double(votes[key, default: 0]) < minShare * Double(maxVote) { return nil }
            }
            return examples[key]
        }

        let paintPaint = votes.keys.filter {
            examples[$0]?.leftKind == "paint" && examples[$0]?.rightKind == "paint"
        }
        let paintKeys = votes.keys.filter {
            examples[$0]?.leftKind == "paint" || examples[$0]?.rightKind == "paint"
        }
        if let picked = pick(keys: paintPaint) ?? pick(keys: paintKeys, minShare: 0.35) ?? pick(keys: Array(votes.keys)) {
            return picked
        }

        let startY = max(0, min(height - 1, height - 1 - max(0, hoodRows)))
        if startY >= bandLo {
            for y in stride(from: startY, through: bandLo, by: -1) {
                let pts = hits(at: y)
                guard pts.count >= 2 else { continue }
                for i in 0..<(pts.count - 1) {
                    let (xL, _, rL, kL) = pts[i]
                    let (xR, _, rR, kR) = pts[i + 1]
                    if xL <= Double(ego), Double(ego) <= xR, valid(xL, xR) {
                        return (rL, rR, kL, kR)
                    }
                }
            }
        }
        return nil
    }

    // MARK: - Track + guidance

    private static func trackOccupiedPair(
        left: [Double],
        right: [Double],
        leftKind: String,
        rightKind: String,
        lane: [Bool],
        pavement: [Bool],
        width: Int,
        height: Int,
        egoCol: Int,
        hoodRows: Int
    ) -> (left: [Double], right: [Double], leftKind: String, rightKind: String) {
        var outL = Array(repeating: Double.nan, count: height)
        var outR = Array(repeating: Double.nan, count: height)
        let maxDx = max(80.0, 0.18 * Double(width))
        let ego = min(max(egoCol, 0), width - 1)
        let bandLo = max(0, Int((0.50 * Double(height)).rounded()))
        let bandHi = height - 1 - max(0, hoodRows)
        var valid: [Int] = []
        if bandLo <= bandHi {
            for y in bandLo...bandHi {
                guard left[y].isFinite, right[y].isFinite else { continue }
                if left[y] + 8.0 < Double(ego), Double(ego) < right[y] - 8.0 {
                    valid.append(y)
                }
            }
        }
        guard !valid.isEmpty else {
            return (dropIdentityJumps(left), dropIdentityJumps(right), leftKind, rightKind)
        }
        let preferred = valid.filter { 0.55 * Double(height) <= Double($0) && Double($0) <= 0.80 * Double(height) }
        let pool = preferred.isEmpty ? valid : preferred
        let target = Int((0.68 * Double(height)).rounded())
        let lockY = pool.min { abs($0 - target) < abs($1 - target) }!
        outL[lockY] = left[lockY]
        outR[lockY] = right[lockY]

        func seed(_ xs: [Double], y0: Int) -> ([Int], [Double]) {
            var ys: [Int] = []
            var vs: [Double] = []
            for y in max(0, y0 - 24)...y0 {
                if xs[y].isFinite {
                    ys.append(y)
                    vs.append(xs[y])
                }
            }
            if ys.isEmpty {
                return ([y0], [xs[y0]])
            }
            return (ys, vs)
        }

        func paintMids(at y: Int) -> [Double] {
            var xs: [Int] = []
            for x in 0..<width where lane[y * width + x] {
                xs.append(x)
            }
            return clusterColumns(xs, gap: 6).map { 0.5 * Double($0.0 + $0.1) }
        }

        func edgeCandidates(at y: Int, side: String) -> [Double] {
            var xs: [Int] = []
            for x in 0..<width where pavement[y * width + x] {
                xs.append(x)
            }
            let clusters = clusterColumns(xs, gap: 8).filter { $0.1 - $0.0 >= 8 }
            return clusters.map { side == "right" ? Double($0.1) : Double($0.0) }
        }

        func hit(kind: String, side: String, y: Int, pred: Double, last: Double?) -> Double? {
            if kind == "paint" {
                let paintTol = min(maxDx, max(36.0, 0.05 * Double(width)))
                let mids = paintMids(at: y)
                if let picked = pickNear(mids, pred: pred, maxDx: paintTol) {
                    if let last, abs(picked - last) > paintTol { return nil }
                    return picked
                }
                guard pavement[y * width + min(max(Int(pred.rounded()), 0), width - 1)] else { return nil }
                return min(max(pred, 0.0), Double(width - 1))
            }
            let cands = edgeCandidates(at: y, side: side)
            if let picked = pickNear(cands, pred: pred, maxDx: maxDx) { return picked }
            let xi = min(max(Int(pred.rounded()), 0), width - 1)
            return pavement[y * width + xi] ? min(max(pred, 0.0), Double(width - 1)) : nil
        }

        func walk(from yFrom: Int, to yTo: Int, step: Int) {
            var histLY = seed(left, y0: yFrom).0
            var histLX = seed(left, y0: yFrom).1
            var histRY = seed(right, y0: yFrom).0
            var histRX = seed(right, y0: yFrom).1
            var y = yFrom + step
            while step > 0 ? y <= yTo : y >= yTo {
                let predL = histLX.last! + localSlope(histLY, histLX) * Double(step)
                let predR = histRX.last! + localSlope(histRY, histRX) * Double(step)
                guard let hitL = hit(kind: leftKind, side: "left", y: y, pred: predL, last: histLX.last),
                      let hitR = hit(kind: rightKind, side: "right", y: y, pred: predR, last: histRX.last),
                      hitR - hitL >= 8.0 else { break }
                outL[y] = hitL
                outR[y] = hitR
                histLY.append(y)
                histLX.append(hitL)
                histRY.append(y)
                histRX.append(hitR)
                y += step
            }
        }

        walk(from: lockY, to: height - 1 - max(0, hoodRows), step: 1)
        walk(from: lockY, to: 0, step: -1)

        let inN = min(left.filter(\.isFinite).count, right.filter(\.isFinite).count)
        let outN = min(outL.filter(\.isFinite).count, outR.filter(\.isFinite).count)
        if outN < 8 || (inN >= 16 && Double(outN) < 0.35 * Double(inN)) {
            return (dropIdentityJumps(left), dropIdentityJumps(right), leftKind, rightKind)
        }
        return (dropIdentityJumps(outL), dropIdentityJumps(outR), leftKind, rightKind)
    }

    private static func guidanceBetweenRails(
        left: [Double],
        right: [Double],
        pavement: [Bool],
        width: Int,
        height: Int,
        lam: Double
    ) -> (mids: [Double], points: [(x: Double, y: Double)]) {
        let lo = zip(left, right).map { min($0.0, $0.1) }
        let hi = zip(left, right).map { max($0.0, $0.1) }
        var mids = Array(repeating: Double.nan, count: height)
        for y in 0..<height where lo[y].isFinite && hi[y].isFinite && hi[y] > lo[y] {
            mids[y] = 0.5 * (lo[y] + hi[y])
        }
        var fitted = fitGuidanceG2(mids: mids, lefts: lo, rights: hi, lam: lam, margin: 1.0)
        for y in 0..<height where fitted[y].isFinite && lo[y].isFinite && hi[y].isFinite {
            let innerLo = lo[y] + 1.0
            let innerHi = hi[y] - 1.0
            if innerHi > innerLo {
                fitted[y] = min(max(fitted[y], innerLo), innerHi)
            } else {
                fitted[y] = 0.5 * (lo[y] + hi[y])
            }
        }

        var points: [(Double, Double)] = []
        var started = false
        for y in stride(from: height - 1, through: 0, by: -1) {
            guard fitted[y].isFinite else {
                if started { break }
                continue
            }
            let xi = min(max(Int(fitted[y].rounded()), 0), width - 1)
            guard pavement[y * width + xi] else {
                if started { break }
                continue
            }
            started = true
            points.append((fitted[y], Double(y)))
        }
        return (fitted, points)
    }

    // MARK: - Geometry helpers

    private static func densifyRail(
        _ xs: [Double],
        width: Int,
        hoodRows: Int,
        extraRows: Int?
    ) -> [Double] {
        var out = xs
        let height = xs.count
        let finite = out.enumerated().compactMap { idx, v -> Int? in v.isFinite ? idx : nil }
        guard !finite.isEmpty else { return out }
        if finite.count == 1 {
            out[finite[0]] = xs[finite[0]]
            return out
        }
        let y0 = finite.min()!
        let y1 = finite.max()!
        if y0 <= y1 {
            for y in y0...y1 {
                out[y] = interpolate(finite.map(Double.init), finite.map { xs[$0] }, at: Double(y))
            }
        }
        let extra = extraRows ?? Int((0.28 * Double(height)).rounded())
        let yHi = min(height - 1 - max(0, hoodRows), y1 + max(0, extra))
        if yHi > y1 {
            let tail = Array(finite.suffix(min(12, finite.count)))
            let slope = clamp(
                linearSlope(x: tail.map(Double.init), y: tail.map { xs[$0] }),
                -2.5,
                2.5
            )
            var lastX = xs[y1]
            for y in (y1 + 1)...yHi {
                lastX += slope
                out[y] = clamp(lastX, 0.0, Double(width - 1))
            }
        }
        return out
    }

    private static func splitSharpTurns(_ xs: [Double], width: Int) -> [[Double]] {
        let height = xs.count
        let finite = xs.enumerated().compactMap { idx, v -> Int? in v.isFinite ? idx : nil }
        guard finite.count >= 8 else { return finite.isEmpty ? [] : [xs] }
        let win = max(4, height / 90)
        var cuts = [0]
        let turnUpper = finite.count - win
        if turnUpper > win {
            for i in win..<turnUpper {
                let y0 = finite[i - win]
                let y1 = finite[i]
                let y2 = finite[i + win]
                let v1 = (xs[y1] - xs[y0], Double(y1 - y0))
                let v2 = (xs[y2] - xs[y1], Double(y2 - y1))
                if turnDeg(v1, v2) > laneMaxTurnDeg, cuts.last != i {
                    cuts.append(i)
                }
            }
        }
        cuts.append(finite.count)
        let minLen = max(8, height / 16)
        var fragments: [[Double]] = []
        for a in 0..<(cuts.count - 1) {
            let slice = cuts[a]..<cuts[a + 1]
            if slice.count < minLen { continue }
            var frag = Array(repeating: Double.nan, count: height)
            for k in slice {
                let yi = finite[k]
                frag[yi] = xs[yi]
            }
            fragments.append(densifyRail(frag, width: width, hoodRows: 12, extraRows: 0))
        }
        return fragments.isEmpty ? [xs] : fragments
    }

    private static func dropIdentityJumps(_ xs: [Double]) -> [Double] {
        var out = xs
        let finite = out.enumerated().compactMap { idx, v -> Int? in v.isFinite ? idx : nil }
        guard finite.count >= 3 else { return out }
        for i in 1..<(finite.count - 1) {
            let y0 = finite[i - 1]
            let y1 = finite[i]
            let y2 = finite[i + 1]
            let v1 = (out[y1] - out[y0], Double(y1 - y0))
            let v2 = (out[y2] - out[y1], Double(y2 - y1))
            if turnDeg(v1, v2) > laneMaxTurnDeg {
                for z in 0..<y2 { out[z] = .nan }
                break
            }
        }
        return out
    }

    private static func trimBorderGlued(_ xs: [Double], width: Int) -> [Double] {
        var out = xs
        let border = 3
        for y in 0..<out.count where out[y].isFinite {
            if out[y] <= Double(border) || out[y] >= Double(width - 1 - border) {
                out[y] = .nan
            }
        }
        return out
    }

    private static func fitGuidanceG2(
        mids: [Double],
        lefts: [Double],
        rights: [Double],
        lam: Double,
        margin: Double
    ) -> [Double] {
        var out = mids
        for run in finiteRuns(out) {
            let n = run.count
            guard n >= 3 else { continue }
            var left = (0..<n).map { lefts[run.lowerBound + $0] }
            var right = (0..<n).map { rights[run.lowerBound + $0] }
            let mid = (0..<n).map { out[run.lowerBound + $0] }
            let half = zip(left, right).map { l, r -> Double in
                guard l.isFinite, r.isFinite else { return 8.0 }
                let h = 0.5 * (r - l)
                return h > 1.0 ? h : 8.0
            }
            for i in 0..<n {
                if !left[i].isFinite { left[i] = mid[i] - half[i] }
                if !right[i].isFinite { right[i] = mid[i] + half[i] }
            }
            let weights = Array(repeating: 1.0, count: n)
            var leftS = solveSmoothD2(target: left, weights: weights, lam: lam)
            var rightS = solveSmoothD2(target: right, weights: weights, lam: lam)
            for i in 0..<n where leftS[i] > rightS[i] {
                swap(&leftS[i], &rightS[i])
            }
            let fitted = zip(leftS, rightS).map { 0.5 * ($0.0 + $0.1) }
            for (offset, value) in fitted.enumerated() {
                out[run.lowerBound + offset] = value
            }
        }
        return out
    }

    private static func solveSmoothD2(target: [Double], weights: [Double], lam: Double) -> [Double] {
        let n = target.count
        guard n >= 3, lam > 0 else { return target }
        var a = Array(repeating: Array(repeating: 0.0, count: n), count: n)
        for i in 0..<n { a[i][i] += weights[i] }
        for i in 0..<(n - 2) {
            let coeffs = [(i, 1.0), (i + 1, -2.0), (i + 2, 1.0)]
            for (j, vj) in coeffs {
                for (k, vk) in coeffs {
                    a[j][k] += lam * vj * vk
                }
            }
        }
        let b = zip(weights, target).map(*)
        return solveLinear(a, b) ?? target
    }

    private static func solveLinear(_ matrix: [[Double]], _ rhs: [Double]) -> [Double]? {
        let n = rhs.count
        var a = matrix
        var b = rhs
        for col in 0..<n {
            var pivot = col
            for row in (col + 1)..<n {
                if abs(a[row][col]) > abs(a[pivot][col]) { pivot = row }
            }
            if abs(a[pivot][col]) < 1e-12 { return nil }
            if pivot != col {
                a.swapAt(pivot, col)
                b.swapAt(pivot, col)
            }
            let div = a[col][col]
            for j in col..<n { a[col][j] /= div }
            b[col] /= div
            for row in 0..<n where row != col {
                let factor = a[row][col]
                if abs(factor) < 1e-15 { continue }
                for j in col..<n { a[row][j] -= factor * a[col][j] }
                b[row] -= factor * b[col]
            }
        }
        return b
    }

    private static func headingCurvatureStats(_ mids: [Double]) -> (maxDPsiDeg: Double, n: Int) {
        let runs = finiteRuns(mids)
        guard let run = runs.max(by: { $0.count < $1.count }) else {
            return (0, 0)
        }
        let u = (run.lowerBound..<run.upperBound).map { mids[$0] }
        guard u.count >= 3 else { return (0, u.count) }
        let du = zip(u, u.dropFirst()).map { $1 - $0 }
        let angles = du.map { atan($0) }
        let dpsi = zip(angles, angles.dropFirst()).map { $1 - $0 }
        let maxDPsi = dpsi.map { abs($0 * 180.0 / .pi) }.max() ?? 0
        return (maxDPsi, u.count)
    }

    private static func clusterColumns(_ xs: [Int], gap: Int) -> [(Int, Int)] {
        guard !xs.isEmpty else { return [] }
        var clusters: [(Int, Int)] = []
        var start = xs[0]
        var prev = xs[0]
        for col in xs.dropFirst() {
            if col - prev > gap {
                clusters.append((start, prev))
                start = col
            }
            prev = col
        }
        clusters.append((start, prev))
        return clusters
    }

    private static func finiteRuns(_ values: [Double]) -> [Range<Int>] {
        var runs: [Range<Int>] = []
        var start: Int?
        for (idx, value) in values.enumerated() {
            if value.isFinite {
                if start == nil { start = idx }
            } else if let s = start {
                runs.append(s..<idx)
                start = nil
            }
        }
        if let s = start { runs.append(s..<values.count) }
        return runs
    }

    private static func borderRail(_ xs: [Double], width: Int) -> Bool {
        let finite = xs.filter(\.isFinite)
        guard !finite.isEmpty else { return true }
        let med = median(xs)
        return med <= 8.0 || med >= Double(width - 9)
    }

    private static func median(_ xs: [Double]) -> Double {
        let finite = xs.filter(\.isFinite).sorted()
        guard !finite.isEmpty else { return .nan }
        return finite[finite.count / 2]
    }

    private static func pickNear(_ cands: [Double], pred: Double, maxDx: Double) -> Double? {
        guard !cands.isEmpty, pred.isFinite else { return nil }
        let x = cands.min { abs($0 - pred) < abs($1 - pred) }!
        return abs(x - pred) <= maxDx ? x : nil
    }

    private static func localSlope(_ ys: [Int], _ xs: [Double]) -> Double {
        guard ys.count >= 2 else { return 0 }
        let tail = min(8, ys.count)
        let yTail = ys.suffix(tail).map(Double.init)
        let xTail = xs.suffix(tail)
        return clamp(linearSlope(x: yTail, y: Array(xTail)), -2.5, 2.5)
    }

    private static func linearSlope(x: [Double], y: [Double]) -> Double {
        guard x.count >= 2 else { return 0 }
        let n = Double(x.count)
        let sx = x.reduce(0, +)
        let sy = y.reduce(0, +)
        let sxx = x.map { $0 * $0 }.reduce(0, +)
        let sxy = zip(x, y).map(*).reduce(0, +)
        let denom = n * sxx - sx * sx
        guard abs(denom) > 1e-9 else { return 0 }
        return (n * sxy - sx * sy) / denom
    }

    private static func interpolate(_ xs: [Double], _ ys: [Double], at x: Double) -> Double {
        guard !xs.isEmpty else { return .nan }
        if x <= xs[0] { return ys[0] }
        if x >= xs[xs.count - 1] { return ys[ys.count - 1] }
        for i in 0..<(xs.count - 1) {
            let x0 = xs[i]
            let x1 = xs[i + 1]
            if x0 <= x, x <= x1 {
                if x1 == x0 { return ys[i] }
                let t = (x - x0) / (x1 - x0)
                return ys[i] + t * (ys[i + 1] - ys[i])
            }
        }
        return .nan
    }

    private static func turnDeg(_ v1: (Double, Double), _ v2: (Double, Double)) -> Double {
        let na = hypot(v1.0, v1.1)
        let nb = hypot(v2.0, v2.1)
        guard na > 1e-6, nb > 1e-6 else { return 0 }
        let cos = clamp((v1.0 * v2.0 + v1.1 * v2.1) / (na * nb), -1.0, 1.0)
        return acos(cos) * 180.0 / .pi
    }

    private static func clamp(_ value: Double, _ lo: Double, _ hi: Double) -> Double {
        min(max(value, lo), hi)
    }
}
