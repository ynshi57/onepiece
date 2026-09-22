import CoreGraphics
import CoreML
import CoreVideo
import Foundation
import Vision

/// Class-index contract of the N=5 CamVid multiclass segmentation model, matching
/// the training/export script (`deploy/ios/finetune_fast_scnn_camvid_multiclass.py`)
/// and the server `camvid_class_map`: 0 bg · 1 road · 2 sidewalk · 3 lane · 4
/// obstacle. Single source of truth so the device derivation can never drift from
/// how the model was trained.
enum SegClass {
    static let background = 0
    static let road = 1
    static let sidewalk = 2
    static let lane = 3
    static let obstacle = 4
    static let count = 5
}

/// Optional RGB-only traversability/floor segmentation runner.
///
/// Expected custom model contract for `VQASeeTraversabilitySegmentation`:
/// - Output is either a single-channel pixel buffer or MLMultiArray.
/// - Higher values mean more traversable/floor-like.
/// - The runner computes coarse ROI coverage only; it never declares a route safe.
final class LocalTraversabilitySegmentationRunner {
    private let visionModel: VNCoreMLModel?

    init(bundle: Bundle = .main, modelName: String = "VQASeeTraversabilitySegmentation") {
        var loadedModel: VNCoreMLModel?
        if let compiledURL = bundle.url(forResource: modelName, withExtension: "mlmodelc"),
           let visionModel = CoreMLPlatformLoader.visionModel(at: compiledURL) {
            loadedModel = visionModel
        }
        self.visionModel = loadedModel
    }

    /// Load from an explicit compiled `.mlmodelc` URL (offline evaluation path): lets
    /// the harness point at the N=5 multiclass model WITHOUT bundling it into the
    /// shipping source tree (mirrors the lane runner). Returns nil if it won't load
    /// so the caller can fail loud rather than silently score an empty segmenter.
    init?(compiledModelURL: URL) {
        guard let visionModel = CoreMLPlatformLoader.visionModel(at: compiledModelURL) else {
            return nil
        }
        self.visionModel = visionModel
    }

    var isAvailable: Bool {
        visionModel != nil
    }

    /// Coarse ROI cue only (backward compatible).
    func analyze(
        pixelBuffer: CVPixelBuffer,
        orientation: CGImagePropertyOrientation = .right,
        config: PerceptionConfig = .default
    ) -> LocalSegmentationCueSignal? {
        analyzeDetailed(pixelBuffer: pixelBuffer, orientation: orientation, config: config)?.cue
    }

    /// Run the segmentation model ONCE and derive both the coarse ROI cue and a
    /// traversable guidance line from the same per-pixel output (no double
    /// inference, per the frame budget).
    func analyzeDetailed(
        pixelBuffer: CVPixelBuffer,
        orientation: CGImagePropertyOrientation = .right,
        config: PerceptionConfig = .default,
        emitGrid: Bool = false
    ) -> LocalSegmentationResult? {
        guard let visionModel else { return nil }
        let request = VNCoreMLRequest(model: visionModel)
        request.imageCropAndScaleOption = .scaleFill
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: orientation, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return nil
        }
        for result in request.results ?? [] {
            if let pixelBufferObservation = result as? VNPixelBufferObservation,
               let res = Self.resultFromPixelBuffer(pixelBufferObservation.pixelBuffer, config: config, emitGrid: emitGrid) {
                return res
            }
            if let feature = result as? VNCoreMLFeatureValueObservation,
               let array = feature.featureValue.multiArrayValue,
               let sampler = Self.sampler(fromMultiArray: array, config: config) {
                return Self.result(width: sampler.width, height: sampler.height, sample: sampler.sample, config: config, emitGrid: emitGrid)
            }
        }
        return nil
    }

    /// Coarse traversable region raster from the SAME sampler that feeds the cue
    /// and centerline. Binary (1 = traversable, prob >= seg threshold) so it maps
    /// exactly to what the device treats as walkable. Row-major, row 0 = TOP of the
    /// image, so it renders aligned with the frame image and the GT mask.
    static let gridCols = 64
    static let gridRows = 48

    private static func traversableGrid(
        width: Int, height: Int, sample: (Int, Int) -> Double?, config: PerceptionConfig
    ) -> TraversableGrid {
        let threshold = config.thresholds.segTraversablePixel
        var cells = [Int](repeating: 0, count: gridCols * gridRows)
        for r in 0..<gridRows {
            let yRaw = Int((Double(r) + 0.5) / Double(gridRows) * Double(height))
            let y = min(max(yRaw, 0), height - 1)
            for c in 0..<gridCols {
                let xRaw = Int((Double(c) + 0.5) / Double(gridCols) * Double(width))
                let x = min(max(xRaw, 0), width - 1)
                if let value = sample(x, y), value >= threshold {
                    cells[r * gridCols + c] = 1
                }
            }
        }
        return TraversableGrid(cols: gridCols, rows: gridRows, cells: cells)
    }

    /// Build cue + guidance line (and, on the harness path, the region grid) from
    /// one traversability sampler.
    private static func result(
        width: Int, height: Int, sample: (Int, Int) -> Double?, config: PerceptionConfig, emitGrid: Bool
    ) -> LocalSegmentationResult {
        let cue = cueValue(width: width, height: height, sample: sample, config: config)
        let path = GuidancePathBuilder.centerline(
            width: width,
            height: height,
            sample: sample,
            threshold: config.thresholds.segTraversablePixel,
            source: "ios_segmentation"
        )
        let grid = emitGrid ? traversableGrid(width: width, height: height, sample: sample, config: config) : nil
        return LocalSegmentationResult(cue: cue, guidancePath: path, traversableGrid: grid)
    }

    /// Compute cue + guidance line by reading the segmentation buffer DIRECTLY
    /// while it stays locked — no full width×height copy. Both the ROI cue (coarse
    /// grid) and the centerline (≤16 sampled rows) only touch a small fraction of
    /// the pixels, so materializing the whole grid every frame was pure waste on
    /// the device's real-time path.
    private static func resultFromPixelBuffer(
        _ pixelBuffer: CVPixelBuffer, config: PerceptionConfig, emitGrid: Bool
    ) -> LocalSegmentationResult? {
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(pixelBuffer) else { return nil }
        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)
        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let pixelFormat = CVPixelBufferGetPixelFormatType(pixelBuffer)
        let sample: (Int, Int) -> Double? = { x, y in
            guard x >= 0, x < width, y >= 0, y < height else { return nil }
            if pixelFormat == kCVPixelFormatType_OneComponent8 {
                let row = base.advanced(by: y * bytesPerRow).assumingMemoryBound(to: UInt8.self)
                return Double(row[x]) / 255.0
            }
            if pixelFormat == kCVPixelFormatType_OneComponent32Float {
                let row = base.advanced(by: y * bytesPerRow).assumingMemoryBound(to: Float32.self)
                let value = row[x]
                return value.isFinite ? Double(value) : nil
            }
            return nil
        }
        return result(width: width, height: height, sample: sample, config: config, emitGrid: emitGrid)
    }

    private static func sampler(
        fromMultiArray array: MLMultiArray, config: PerceptionConfig
    ) -> (width: Int, height: Int, sample: (Int, Int) -> Double?)? {
        let shape = array.shape.map { $0.intValue }
        guard shape.count >= 2 else { return nil }
        let height = shape[shape.count - 2]
        let width = shape[shape.count - 1]
        guard width > 8, height > 8 else { return nil }
        let strides = array.strides.map { $0.intValue }
        let hStride = strides[strides.count - 2]
        let wStride = strides[strides.count - 1]

        // Segmentation models output per-class logits on a channel dim just before
        // (H, W): shape [..., C, H, W]. A C=2 model is binary (class 1 =
        // traversable); its raw values are logits, NOT probabilities, so they must
        // be turned into a traversable probability in [0,1] before any threshold.
        // Reading channel 0 raw (the old code) fed the WRONG class's logit,
        // thresholded at 0.5 — meaningless — which is why the guidance line wandered.
        let classCount = shape.count >= 3 ? shape[shape.count - 3] : 1
        let cStride = shape.count >= 3 ? strides[strides.count - 3] : 0

        let sample: (Int, Int) -> Double?
        if classCount == 2 {
            sample = { x, y in
                guard x >= 0, x < width, y >= 0, y < height else { return nil }
                let base = y * hStride + x * wStride
                let notTrav = array[base].doubleValue           // channel 0
                let trav = array[base + cStride].doubleValue    // channel 1 (traversable)
                guard notTrav.isFinite, trav.isFinite else { return nil }
                // 2-class softmax prob of the traversable class = sigmoid(l1 - l0).
                return 1.0 / (1.0 + exp(notTrav - trav))
            }
        } else if classCount == 1 {
            // Single-channel model already emits a traversable score/probability.
            sample = { x, y in
                guard x >= 0, x < width, y >= 0, y < height else { return nil }
                let value = array[y * hStride + x * wStride].doubleValue
                return value.isFinite ? value : nil
            }
        } else {
            // N=5 multiclass (0 bg, 1 road, 2 sidewalk, 3 lane, 4 obstacle): the
            // traversable region is ROLE-CONDITIONED. Return the softmax mass of the
            // role's PRIMARY classes (walker → sidewalk; driver → road+lane) so the
            // SAME per-frame logits yield a walker's OR a driver's walkable surface.
            // A pixel whose primary-class softmax >= segTraversablePixel is where
            // argmax lands on a primary class, i.e. this equals argmax-in-primary
            // once thresholded, but stays smooth for the centerline builder.
            guard !config.role.primaryClassIndices.filter({ $0 >= 0 && $0 < classCount }).isEmpty else {
                // The requested role has no valid class in this model — fail loudly
                // rather than fabricate a route from an unmapped class.
                return nil
            }
            let role = config.role
            sample = { x, y in
                guard x >= 0, x < width, y >= 0, y < height else { return nil }
                let base = y * hStride + x * wStride
                var logits = [Double](repeating: 0, count: classCount)
                for c in 0..<classCount { logits[c] = array[base + c * cStride].doubleValue }
                return traversableProbability(fromClassLogits: logits, role: role)
            }
        }
        return (width, height, sample)
    }

    /// Role-conditioned traversable probability from ONE pixel's per-class logits:
    /// the softmax mass on the role's primary classes (walker → sidewalk; driver →
    /// road+lane). Thresholding this at `segTraversablePixel` is equivalent to
    /// "argmax lands on a primary class" when the threshold is >= 0.5, while staying
    /// smooth for the centerline builder. Internal so it is unit-testable without a
    /// Core ML model. Returns nil on non-finite logits or no valid primary class.
    static func traversableProbability(
        fromClassLogits logits: [Double], role: PerceptionRole
    ) -> Double? {
        guard logits.count >= 3, logits.allSatisfy({ $0.isFinite }) else { return nil }
        let primary = role.primaryClassIndices.filter { $0 >= 0 && $0 < logits.count }
        guard !primary.isEmpty else { return nil }
        let maxLogit = logits.max() ?? 0
        var total = 0.0
        var primaryMass = 0.0
        for (c, l) in logits.enumerated() {
            let e = exp(l - maxLogit)
            total += e
            if primary.contains(c) { primaryMass += e }
        }
        guard total > 0 else { return nil }
        return primaryMass / total
    }

    private static func cueValue(width: Int, height: Int, sample: (Int, Int) -> Double?, config: PerceptionConfig) -> LocalSegmentationCueSignal? {
        let traversablePixel = config.thresholds.segTraversablePixel
        func coverage(in roi: CGRect) -> Double? {
            let xStart = max(0, Int(roi.minX * CGFloat(width)))
            let xEnd = min(width - 1, Int(roi.maxX * CGFloat(width)))
            let yStart = max(0, Int(roi.minY * CGFloat(height)))
            let yEnd = min(height - 1, Int(roi.maxY * CGFloat(height)))
            let stepX = max(1, (xEnd - xStart) / 10)
            let stepY = max(1, (yEnd - yStart) / 8)
            var traversable = 0
            var valid = 0
            for y in stride(from: yStart, through: yEnd, by: stepY) {
                for x in stride(from: xStart, through: xEnd, by: stepX) {
                    guard let value = sample(x, y) else { continue }
                    valid += 1
                    if value >= traversablePixel { traversable += 1 }
                }
            }
            guard valid >= 8 else { return nil }
            return Double(traversable) / Double(valid)
        }
        return LocalSegmentationCueSignal(
            traversableRatio: coverage(in: CGRect(x: 0, y: 0, width: 1, height: 1))
        )
    }
}
