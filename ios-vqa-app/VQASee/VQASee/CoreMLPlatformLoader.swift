import CoreML
import Vision

/// Platform-aware Core ML loader shared by the iOS app and the macOS harness.
///
/// On macOS (especially Intel + recent macOS), mlProgram models often SIGABRT in
/// MPSGraph (`MLIR pass manager failed`) when GPU is selected. The offline harness
/// forces CPU so daily evaluation can finish; iPhone keeps default compute units
/// (ANE/GPU) for production latency.
enum CoreMLPlatformLoader {
    static func mlModel(at url: URL) -> MLModel? {
        let configuration = MLModelConfiguration()
        #if os(macOS)
        configuration.computeUnits = .cpuOnly
        #endif
        return try? MLModel(contentsOf: url, configuration: configuration)
    }

    static func visionModel(at url: URL) -> VNCoreMLModel? {
        guard let model = mlModel(at: url) else { return nil }
        return try? VNCoreMLModel(for: model)
    }
}
