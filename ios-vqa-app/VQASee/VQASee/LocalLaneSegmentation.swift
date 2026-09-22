import CoreGraphics
import CoreImage
import CoreML
import CoreVideo
import Foundation
import Vision

struct LanePolyline: Sendable, Equatable {
    enum Source: String, Sendable, Equatable {
        case rowAnchor
        case colAnchor
        /// TwinLite lane-mask skeleton. Display only — never UFLD guidance.
        case twinliteMask = "twinlite_mask"
    }

    var laneIndex: Int
    var source: Source
    /// Normalized image-space points, x/y in 0...1, origin at the top-left.
    var points: [CGPoint]

    func toWire() -> [String: Any] {
        [
            "lane_index": laneIndex,
            "source": source.rawValue,
            "points": points.map { ["x": Double($0.x), "y": Double($0.y)] },
        ]
    }
}

/// Pure Swift mirror of `deploy/ios/decode_ufldv2_lanes.py`.
///
/// UFLDv2 emits ordinal-classification logits plus a 2-class existence head. This
/// decoder turns those anchors into normalized lane polylines. It is model-agnostic
/// and testable without Core ML, so the future UFLDv2 runner can stay thin.
enum UFLDv2LaneDecoder {
    static let culaneRowAnchors: [Double] = (0..<72).map { index -> Double in
        let t = Double(index) / 71.0
        return 0.42 + (1.0 - 0.42) * t
    }
    static let culaneColAnchors: [Double] = (0..<81).map { index -> Double in
        Double(index) / 80.0
    }

    static func decodeRowAnchors(
        locRow: [Double],
        existRow: [Double],
        numGrid: Int = 200,
        numRows: Int = 72,
        numLanes: Int = 4,
        rowAnchors: [Double] = culaneRowAnchors,
        laneIndices: [Int] = [1, 2],
        gateDivisor: Double = 2.0,
        localWidth: Int = 1
    ) -> [LanePolyline] {
        guard numGrid > 1,
              numRows > 0,
              numLanes > 0,
              rowAnchors.count == numRows,
              locRow.count == numGrid * numRows * numLanes,
              existRow.count == 2 * numRows * numLanes else {
            return []
        }

        var lanes: [LanePolyline] = []
        for laneIndex in laneIndices where laneIndex >= 0 && laneIndex < numLanes {
            let validRows = (0..<numRows).filter { anchor in
                existenceClass(existRow, anchor: anchor, lane: laneIndex, anchorCount: numRows, laneCount: numLanes) == 1
            }
            guard Double(validRows.count) > Double(numRows) / gateDivisor else {
                continue
            }

            let points = validRows.compactMap { anchor -> CGPoint? in
                guard let expected = expectedGridIndex(
                    locRow,
                    gridCount: numGrid,
                    anchor: anchor,
                    lane: laneIndex,
                    anchorCount: numRows,
                    laneCount: numLanes,
                    localWidth: localWidth
                ) else {
                    return nil
                }
                return CGPoint(
                    x: clamp01(expected / Double(numGrid - 1)),
                    y: clamp01(rowAnchors[anchor])
                )
            }
            if !points.isEmpty {
                lanes.append(LanePolyline(laneIndex: laneIndex, source: .rowAnchor, points: points))
            }
        }
        return lanes
    }

    static func decodeColAnchors(
        locCol: [Double],
        existCol: [Double],
        numGrid: Int = 100,
        numCols: Int = 81,
        numLanes: Int = 4,
        colAnchors: [Double] = culaneColAnchors,
        laneIndices: [Int] = [0, 3],
        gateDivisor: Double = 4.0,
        localWidth: Int = 1
    ) -> [LanePolyline] {
        guard numGrid > 1,
              numCols > 0,
              numLanes > 0,
              colAnchors.count == numCols,
              locCol.count == numGrid * numCols * numLanes,
              existCol.count == 2 * numCols * numLanes else {
            return []
        }

        var lanes: [LanePolyline] = []
        for laneIndex in laneIndices where laneIndex >= 0 && laneIndex < numLanes {
            let validCols = (0..<numCols).filter { anchor in
                existenceClass(existCol, anchor: anchor, lane: laneIndex, anchorCount: numCols, laneCount: numLanes) == 1
            }
            guard Double(validCols.count) > Double(numCols) / gateDivisor else {
                continue
            }

            let points = validCols.compactMap { anchor -> CGPoint? in
                guard let expected = expectedGridIndex(
                    locCol,
                    gridCount: numGrid,
                    anchor: anchor,
                    lane: laneIndex,
                    anchorCount: numCols,
                    laneCount: numLanes,
                    localWidth: localWidth
                ) else {
                    return nil
                }
                return CGPoint(
                    x: clamp01(colAnchors[anchor]),
                    y: clamp01(expected / Double(numGrid - 1))
                )
            }
            if !points.isEmpty {
                lanes.append(LanePolyline(laneIndex: laneIndex, source: .colAnchor, points: points))
            }
        }
        return lanes
    }

    private static func existenceClass(
        _ logits: [Double],
        anchor: Int,
        lane: Int,
        anchorCount: Int,
        laneCount: Int
    ) -> Int {
        let absent = logits[(0 * anchorCount * laneCount) + (anchor * laneCount) + lane]
        let present = logits[(1 * anchorCount * laneCount) + (anchor * laneCount) + lane]
        return present > absent ? 1 : 0
    }

    private static func expectedGridIndex(
        _ logits: [Double],
        gridCount: Int,
        anchor: Int,
        lane: Int,
        anchorCount: Int,
        laneCount: Int,
        localWidth: Int
    ) -> Double? {
        var bestIndex = 0
        var bestValue = -Double.infinity
        for grid in 0..<gridCount {
            let value = logits[(grid * anchorCount * laneCount) + (anchor * laneCount) + lane]
            guard value.isFinite else {
                return nil
            }
            if value > bestValue {
                bestValue = value
                bestIndex = grid
            }
        }

        let lo = max(0, bestIndex - localWidth)
        let hi = min(gridCount - 1, bestIndex + localWidth)
        let window = (lo...hi).map { grid in
            logits[(grid * anchorCount * laneCount) + (anchor * laneCount) + lane]
        }
        guard let maxLogit = window.max(), maxLogit.isFinite else {
            return nil
        }

        let weights = window.map { exp($0 - maxLogit) }
        let sum = weights.reduce(0, +)
        guard sum.isFinite, sum > 0 else {
            return nil
        }

        let expected = zip((lo...hi), weights).reduce(0.0) { partial, pair in
            partial + Double(pair.0) * pair.1 / sum
        }
        return expected + 0.5
    }

    private static func clamp01(_ value: Double) -> Double {
        min(max(value, 0.0), 1.0)
    }
}

/// Post-decode geometry for UFLDv2 row-anchor ego lanes (indices 1 and 2).
///
/// Raw `pred2coords` stays a per-row expected-x. This pass drops isolated
/// x-spikes and light-smoothes heading so a 165° Z does not ship as a lane.
/// Col-anchor lanes (0/3) are left untouched: their geometry is in y.
enum UFLDv2LanePolisher {
    static let minPoints = 8
    static let maxSpikeDev = 0.035
    static let maxIdentityJump = 0.06
    static let smoothLambda = 800.0

    static func polishRowEgoLanes(_ lanes: [LanePolyline]) -> [LanePolyline] {
        lanes.map { lane in
            guard lane.source == .rowAnchor, lane.laneIndex == 1 || lane.laneIndex == 2 else {
                return lane
            }
            let polished = polish(lane.points)
            guard polished.count >= 2 else { return lane }
            var out = lane
            out.points = polished
            return out
        }
    }

    static func polish(_ points: [CGPoint]) -> [CGPoint] {
        guard points.count >= minPoints else { return points }
        let sorted = points.sorted { $0.y < $1.y }
        let fragment = longestIdentityFragment(rejectSpikes(sorted))
        if fragment.count < minPoints {
            return fragment.count >= 2 ? fragment : points
        }
        let smoothed = smoothX(fragment)
        return smoothed.count >= 2 ? smoothed : points
    }

    private static func rejectSpikes(_ points: [CGPoint]) -> [CGPoint] {
        guard points.count >= 3 else { return points }
        return points.enumerated().compactMap { index, point in
            if index == 0 || index == points.count - 1 { return point }
            let pred = 0.5 * (points[index - 1].x + points[index + 1].x)
            return abs(point.x - pred) > maxSpikeDev ? nil : point
        }
    }

    private static func longestIdentityFragment(_ points: [CGPoint]) -> [CGPoint] {
        guard points.count >= 2 else { return points }
        var fragments: [[CGPoint]] = [[points[0]]]
        for index in 1..<points.count {
            let prev = points[index - 1]
            let point = points[index]
            if abs(Double(point.x - prev.x)) > maxIdentityJump {
                fragments.append([point])
            } else {
                fragments[fragments.count - 1].append(point)
            }
        }
        return fragments.max(by: { $0.count < $1.count }) ?? points
    }

    private static func smoothX(_ points: [CGPoint]) -> [CGPoint] {
        let n = points.count
        guard n >= minPoints, smoothLambda > 0 else { return points }
        let target = points.map { Double($0.x) }
        let weights = Array(repeating: 1.0, count: n)
        let xs = solveSmoothD2(target: target, weights: weights, lam: smoothLambda)
        return zip(xs, points).map { x, point in
            CGPoint(x: min(max(x, 0.0), 1.0), y: point.y)
        }
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
}

/// CULane UFLDv2 input transform (mirrors `preprocess()` in decode_ufldv2_lanes.py):
/// stretch-resize to (1600, net_h/crop_ratio) then keep the bottom `net_h` rows.
enum UFLDv2ImagePreprocessor {
    static let netWidth = 1600
    static let netHeight = 320
    static let cropRatio = 0.6

    static var resizedHeight: Int {
        Int(Double(netHeight) / cropRatio)
    }

    private static let ciContext = CIContext(options: nil)

    static func preprocess(
        pixelBuffer: CVPixelBuffer,
        orientation: CGImagePropertyOrientation = .up
    ) -> CVPixelBuffer? {
        var image = CIImage(cvPixelBuffer: pixelBuffer)
        image = image.oriented(forExifOrientation: Int32(orientation.rawValue))
        let extent = image.extent.integral
        guard extent.width > 0, extent.height > 0 else { return nil }

        let scaleX = Double(netWidth) / extent.width
        let scaleY = Double(resizedHeight) / extent.height
        let scaled = image.transformed(by: CGAffineTransform(scaleX: scaleX, y: scaleY))
        let cropRect = CGRect(x: 0, y: 0, width: netWidth, height: netHeight)
        let cropped = scaled.cropped(to: cropRect)
        let translated = cropped.transformed(
            by: CGAffineTransform(
                translationX: -cropped.extent.origin.x,
                y: -cropped.extent.origin.y
            )
        )

        var output: CVPixelBuffer?
        let status = CVPixelBufferCreate(
            kCFAllocatorDefault,
            netWidth,
            netHeight,
            kCVPixelFormatType_32BGRA,
            nil,
            &output
        )
        guard status == kCVReturnSuccess, let output else { return nil }
        ciContext.render(translated, to: output)
        return output
    }
}

/// UFLDv2 Core ML runner: outputs true lane polylines, not a pixel mask.
///
/// This is intentionally separate from `LocalLaneSegmentationRunner` (the old
/// CamVid lane-pixel model). Product UI should prefer `lanePolylines`; `laneGrid`
/// remains a debug/regression layer.
final class LocalLanePolylineRunner {
    private let visionModel: VNCoreMLModel?

    /// Wall-clock milliseconds of the LAST inference (model forward + decode).
    private(set) var lastInferenceMs: Double?

    init(bundle: Bundle = .main, modelName: String = "VQASeeLaneUFLDv2") {
        var loaded: VNCoreMLModel?
        if let compiledURL = bundle.url(forResource: modelName, withExtension: "mlmodelc"),
           let visionModel = CoreMLPlatformLoader.visionModel(at: compiledURL) {
            loaded = visionModel
        }
        self.visionModel = loaded
    }

    init?(compiledModelURL: URL) {
        guard let visionModel = CoreMLPlatformLoader.visionModel(at: compiledModelURL) else {
            return nil
        }
        self.visionModel = visionModel
    }

    var isAvailable: Bool { visionModel != nil }

    func analyze(
        pixelBuffer: CVPixelBuffer,
        orientation: CGImagePropertyOrientation = .right
    ) -> [LanePolyline]? {
        guard let visionModel else { return nil }
        guard let preprocessed = UFLDv2ImagePreprocessor.preprocess(
            pixelBuffer: pixelBuffer,
            orientation: orientation
        ) else {
            return nil
        }
        let start = DispatchTime.now()
        let request = VNCoreMLRequest(model: visionModel)
        request.imageCropAndScaleOption = .scaleFill
        let handler = VNImageRequestHandler(cvPixelBuffer: preprocessed, orientation: .up, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return nil
        }

        var outputs: [String: MLMultiArray] = [:]
        for result in request.results ?? [] {
            guard let feature = result as? VNCoreMLFeatureValueObservation,
                  let array = feature.featureValue.multiArrayValue else {
                continue
            }
            outputs[feature.featureName] = array
        }
        guard let locRow = Self.values(from: outputs["loc_row"], lastDims: (200, 72, 4)),
              let existRow = Self.values(from: outputs["exist_row"], lastDims: (2, 72, 4)),
              let locCol = Self.values(from: outputs["loc_col"], lastDims: (100, 81, 4)),
              let existCol = Self.values(from: outputs["exist_col"], lastDims: (2, 81, 4)) else {
            return nil
        }

        let decoded = UFLDv2LaneDecoder.decodeRowAnchors(locRow: locRow, existRow: existRow)
            + UFLDv2LaneDecoder.decodeColAnchors(locCol: locCol, existCol: existCol)
        let lanes = UFLDv2LanePolisher.polishRowEgoLanes(decoded)
        lastInferenceMs = Double(DispatchTime.now().uptimeNanoseconds - start.uptimeNanoseconds) / 1_000_000.0
        return lanes.isEmpty ? nil : lanes
    }

    private static func values(
        from array: MLMultiArray?,
        lastDims: (Int, Int, Int)
    ) -> [Double]? {
        guard let array else { return nil }
        var shape = array.shape.map { $0.intValue }
        if shape.count == 4, shape[0] == 1 {
            shape = Array(shape.dropFirst())
        }
        guard shape.count == 3 else { return nil }
        guard shape[0] == lastDims.0, shape[1] == lastDims.1, shape[2] == lastDims.2 else {
            return nil
        }
        let expectedCount = lastDims.0 * lastDims.1 * lastDims.2
        guard array.count == expectedCount else { return nil }
        var values: [Double] = []
        values.reserveCapacity(expectedCount)
        for index in 0..<expectedCount {
            let value = array[index].doubleValue
            guard value.isFinite else {
                return nil
            }
            values.append(value)
        }
        return values
    }
}

/// Dedicated on-device LANE-MARKING segmenter (T4 device channel).
///
/// Contract for `VQASeeLaneSegmentation` (trained by
/// `deploy/ios/finetune_lane_segmenter_camvid.py`, see
/// `docs/model-lab/2026-08-27-lane-marking-segmentation.md`):
/// - Core ML output is `[1, 2, H, W]` logits; channel 1 = lane, channel 0 = not-lane.
/// - The lane probability is the 2-class softmax of the lane class = `sigmoid(l1 - l0)`.
/// - A cell is a lane marking when that probability >= `laneProbThreshold`.
///
/// It is a SEPARATE model from the traversability segmenter — a second forward
/// pass — so it is opt-in. The live device path leaves it OFF until a latency
/// budget is signed off (罗根); the offline harness injects it to emit + score the
/// lane channel. It never declares any route safe.
final class LocalLaneSegmentationRunner {
    private let visionModel: VNCoreMLModel?

    /// Lane markings are thin: a coarse 64×48 grid would erase them, so the lane
    /// raster is finer than the traversability grid.
    static let gridCols = 128
    static let gridRows = 96

    /// 2-class softmax prob of the lane class above which a cell counts as lane.
    /// Kept here for now; a future OTA (T5) can move it into PerceptionConfig.
    static let laneProbThreshold = 0.5

    /// Wall-clock milliseconds of the LAST inference (model forward + grid build),
    /// so the harness can report a latency budget for the added lane pass. nil
    /// before the first successful run.
    private(set) var lastInferenceMs: Double?

    /// Device path: load the compiled `.mlmodelc` from the app bundle.
    init(bundle: Bundle = .main, modelName: String = "VQASeeLaneSegmentation") {
        var loaded: VNCoreMLModel?
        if let compiledURL = bundle.url(forResource: modelName, withExtension: "mlmodelc"),
           let visionModel = CoreMLPlatformLoader.visionModel(at: compiledURL) {
            loaded = visionModel
        }
        self.visionModel = loaded
    }

    /// Harness path: load a compiled `.mlmodelc` from an explicit URL, so the
    /// evaluation harness can use the freshly compiled model without copying it
    /// into the shipping source tree.
    init?(compiledModelURL: URL) {
        guard let visionModel = CoreMLPlatformLoader.visionModel(at: compiledModelURL) else {
            return nil
        }
        self.visionModel = visionModel
    }

    var isAvailable: Bool { visionModel != nil }

    /// Run the lane model once and return the lane-marking raster. nil when the
    /// model is unavailable or produced no usable output (fail loud, never
    /// fabricate a lane).
    func analyze(
        pixelBuffer: CVPixelBuffer,
        orientation: CGImagePropertyOrientation = .right
    ) -> LaneGrid? {
        guard let visionModel else { return nil }
        let start = DispatchTime.now()
        let request = VNCoreMLRequest(model: visionModel)
        request.imageCropAndScaleOption = .scaleFill
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: orientation, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return nil
        }
        for result in request.results ?? [] {
            guard let feature = result as? VNCoreMLFeatureValueObservation,
                  let array = feature.featureValue.multiArrayValue,
                  let sampler = Self.laneSampler(fromMultiArray: array) else {
                continue
            }
            let grid = Self.laneGrid(width: sampler.width, height: sampler.height, sample: sampler.sample)
            let elapsed = Double(DispatchTime.now().uptimeNanoseconds - start.uptimeNanoseconds) / 1_000_000.0
            lastInferenceMs = elapsed
            return grid
        }
        return nil
    }

    /// Read the lane class probability from a `[..., 2, H, W]` logits array.
    private static func laneSampler(
        fromMultiArray array: MLMultiArray
    ) -> (width: Int, height: Int, sample: (Int, Int) -> Double?)? {
        let shape = array.shape.map { $0.intValue }
        guard shape.count >= 3 else { return nil }
        let classCount = shape[shape.count - 3]
        // A dedicated lane model is binary; anything else is not this contract.
        guard classCount == 2 else { return nil }
        let height = shape[shape.count - 2]
        let width = shape[shape.count - 1]
        guard width > 8, height > 8 else { return nil }
        let strides = array.strides.map { $0.intValue }
        let cStride = strides[strides.count - 3]
        let hStride = strides[strides.count - 2]
        let wStride = strides[strides.count - 1]
        let sample: (Int, Int) -> Double? = { x, y in
            guard x >= 0, x < width, y >= 0, y < height else { return nil }
            let base = y * hStride + x * wStride
            let notLane = array[base].doubleValue           // channel 0
            let lane = array[base + cStride].doubleValue     // channel 1 (lane)
            guard notLane.isFinite, lane.isFinite else { return nil }
            return 1.0 / (1.0 + exp(notLane - lane))         // sigmoid(l1 - l0)
        }
        return (width, height, sample)
    }

    private static func laneGrid(
        width: Int, height: Int, sample: (Int, Int) -> Double?
    ) -> LaneGrid {
        var cells = [Int](repeating: 0, count: gridCols * gridRows)
        for r in 0..<gridRows {
            let yRaw = Int((Double(r) + 0.5) / Double(gridRows) * Double(height))
            let y = min(max(yRaw, 0), height - 1)
            for c in 0..<gridCols {
                let xRaw = Int((Double(c) + 0.5) / Double(gridCols) * Double(width))
                let x = min(max(xRaw, 0), width - 1)
                if let value = sample(x, y), value >= laneProbThreshold {
                    cells[r * gridCols + c] = 1
                }
            }
        }
        return LaneGrid(cols: gridCols, rows: gridRows, cells: cells)
    }
}
