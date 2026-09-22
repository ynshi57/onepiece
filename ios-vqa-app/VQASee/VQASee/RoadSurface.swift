import CoreGraphics
import CoreML
import CoreVideo
import Foundation
import Vision

/// Swap-point for on-device road surface models.
///
/// Analyzer / overlay consume `RoadSurfaceMaps` only. A new model is a new
/// `RoadSurfaceBackend` + a `RoadBackendID` case. Do not fork LocalVisionAnalyzer.
enum RoadBackendID: String, Sendable, Equatable {
    case off
    case twinlite
    case mc5

    static let allIDs = ["off", "twinlite", "mc5"]
}

struct RoadSurfaceMaps: Sendable, Equatable {
    var backend: RoadBackendID
    var guidancePath: GuidancePath?
    var traversableGrid: TraversableGrid?
    var laneGrid: LaneGrid?
    var segmentationCues: LocalSegmentationCueSignal?
}

protocol RoadSurfaceBackend: AnyObject {
    var id: RoadBackendID { get }
    var lastInferenceMs: Double? { get }
    func infer(
        pixelBuffer: CVPixelBuffer,
        orientation: CGImagePropertyOrientation,
        config: PerceptionConfig,
        emitGrid: Bool
    ) -> RoadSurfaceMaps?
}

enum RoadSurfaceBackends {
    static func make(
        config: PerceptionConfig,
        bundle: Bundle = .main,
        twinliteURL: URL? = nil,
        mc5Runner: LocalTraversabilitySegmentationRunner? = nil
    ) -> RoadSurfaceBackend? {
        switch config.roadBackend {
        case .twinlite:
            if let twinliteURL {
                return TwinLiteNetRoadBackend(compiledModelURL: twinliteURL)
            }
            let runner = TwinLiteNetRoadBackend(bundle: bundle)
            return runner.isAvailable ? runner : nil
        case .mc5:
            let runner = mc5Runner ?? LocalTraversabilitySegmentationRunner(
                bundle: bundle,
                modelName: "VQASeeTraversabilitySeg5"
            )
            return Mc5RoadBackend(runner: runner)
        case .off:
            return nil
        }
    }
}

/// TwinLiteNet: RGB → drivable + lane logits. Experimental App overlay, not a
/// walk-role sidewalk model.
final class TwinLiteNetRoadBackend: RoadSurfaceBackend {
    let id: RoadBackendID = .twinlite
    private(set) var lastInferenceMs: Double?
    private let visionModel: VNCoreMLModel?

    var isAvailable: Bool { visionModel != nil }

    init(bundle: Bundle = .main, modelName: String = "VQASeeTwinLiteNet") {
        var loaded: VNCoreMLModel?
        if let url = bundle.url(forResource: modelName, withExtension: "mlmodelc"),
           let mlModel = try? MLModel(contentsOf: url),
           let visionModel = try? VNCoreMLModel(for: mlModel) {
            loaded = visionModel
        }
        self.visionModel = loaded
    }

    init?(compiledModelURL: URL) {
        guard let mlModel = try? MLModel(contentsOf: compiledModelURL),
              let visionModel = try? VNCoreMLModel(for: mlModel) else {
            return nil
        }
        self.visionModel = visionModel
    }

    func infer(
        pixelBuffer: CVPixelBuffer,
        orientation: CGImagePropertyOrientation,
        config: PerceptionConfig,
        emitGrid: Bool
    ) -> RoadSurfaceMaps? {
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
        var daArray: MLMultiArray?
        var laneArray: MLMultiArray?
        var unnamed: [MLMultiArray] = []
        for result in request.results ?? [] {
            guard let feature = result as? VNCoreMLFeatureValueObservation,
                  let array = feature.featureValue.multiArrayValue else {
                continue
            }
            unnamed.append(array)
            if feature.featureName == "da_logits" { daArray = array }
            if feature.featureName == "lane_logits" { laneArray = array }
        }
        if daArray == nil, laneArray == nil, unnamed.count >= 2 {
            daArray = unnamed[0]
            laneArray = unnamed[1]
        }
        guard let daSampler = Self.binarySampler(from: daArray),
              let laneSampler = Self.binarySampler(from: laneArray) else {
            return nil
        }
        _ = emitGrid
        let threshold = config.thresholds.segTraversablePixel
        let path = GuidancePathBuilder.centerline(
            width: daSampler.width,
            height: daSampler.height,
            sample: daSampler.sample,
            threshold: threshold,
            source: "twinlite"
        )
        let daGrid = Self.boolGrid(
            width: daSampler.width,
            height: daSampler.height,
            sample: daSampler.sample,
            threshold: threshold,
            cols: LocalTraversabilitySegmentationRunner.gridCols,
            rows: LocalTraversabilitySegmentationRunner.gridRows
        )
        let laneCells = Self.boolGrid(
            width: laneSampler.width,
            height: laneSampler.height,
            sample: laneSampler.sample,
            threshold: LocalLaneSegmentationRunner.laneProbThreshold,
            cols: LocalLaneSegmentationRunner.gridCols,
            rows: LocalLaneSegmentationRunner.gridRows
        )
        lastInferenceMs = Double(DispatchTime.now().uptimeNanoseconds - start.uptimeNanoseconds) / 1_000_000.0
        return RoadSurfaceMaps(
            backend: .twinlite,
            guidancePath: path,
            traversableGrid: TraversableGrid(cols: daGrid.cols, rows: daGrid.rows, cells: daGrid.cells),
            laneGrid: LaneGrid(cols: laneCells.cols, rows: laneCells.rows, cells: laneCells.cells),
            segmentationCues: nil
        )
    }

    private static func binarySampler(
        from array: MLMultiArray?
    ) -> (width: Int, height: Int, sample: (Int, Int) -> Double?)? {
        guard let array else { return nil }
        let shape = array.shape.map { $0.intValue }
        guard shape.count >= 3 else { return nil }
        let classCount = shape[shape.count - 3]
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
            let neg = array[base].doubleValue
            let pos = array[base + cStride].doubleValue
            guard neg.isFinite, pos.isFinite else { return nil }
            return 1.0 / (1.0 + exp(neg - pos))
        }
        return (width, height, sample)
    }

    private static func boolGrid(
        width: Int,
        height: Int,
        sample: (Int, Int) -> Double?,
        threshold: Double,
        cols: Int,
        rows: Int
    ) -> (cols: Int, rows: Int, cells: [Int]) {
        var cells = [Int](repeating: 0, count: cols * rows)
        for r in 0..<rows {
            let yRaw = Int((Double(r) + 0.5) / Double(rows) * Double(height))
            let y = min(max(yRaw, 0), height - 1)
            for c in 0..<cols {
                let xRaw = Int((Double(c) + 0.5) / Double(cols) * Double(width))
                let x = min(max(xRaw, 0), width - 1)
                if let value = sample(x, y), value >= threshold {
                    cells[r * cols + c] = 1
                }
            }
        }
        return (cols, rows, cells)
    }
}

/// mc5 adaptor: same maps contract, different Core ML model.
final class Mc5RoadBackend: RoadSurfaceBackend {
    let id: RoadBackendID = .mc5
    private let runner: LocalTraversabilitySegmentationRunner
    private(set) var lastInferenceMs: Double?

    init(runner: LocalTraversabilitySegmentationRunner) {
        self.runner = runner
    }

    func infer(
        pixelBuffer: CVPixelBuffer,
        orientation: CGImagePropertyOrientation,
        config: PerceptionConfig,
        emitGrid: Bool
    ) -> RoadSurfaceMaps? {
        let start = DispatchTime.now()
        guard let result = runner.analyzeDetailed(
            pixelBuffer: pixelBuffer,
            orientation: orientation,
            config: config,
            emitGrid: emitGrid
        ) else {
            return nil
        }
        lastInferenceMs = Double(DispatchTime.now().uptimeNanoseconds - start.uptimeNanoseconds) / 1_000_000.0
        return RoadSurfaceMaps(
            backend: .mc5,
            guidancePath: result.guidancePath,
            traversableGrid: result.traversableGrid,
            laneGrid: nil,
            segmentationCues: result.cue
        )
    }
}
