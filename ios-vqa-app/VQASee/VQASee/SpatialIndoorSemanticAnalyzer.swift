import Foundation
import Vision
import CoreML
import ImageIO

nonisolated struct SpatialIndoorSemanticObservation: Codable, Sendable, Equatable {
    enum Status: String, Codable, Sendable { case processed, failed }
    var attemptID = UUID()
    var frameID: String
    var frameTime: Double
    var epoch: Int
    var modelID: String
    var status: Status = .failed
    var failure: String?
    var elapsedSeconds: Double = 0
    var width: Int = 0
    var height: Int = 0
    /// Native-axis, low resolution, lossless class indices and quantized max softmax score.
    var labels = Data()
    var confidence = Data()
    var taxonomy: SpatialSurfaceSemanticTaxonomy?
    var records: [SpatialSurfaceSemanticRecord] = []
    var previousAttempts: [SpatialIndoorSemanticObservation]? = nil

    var mask: SpatialSurfaceSemanticMask? {
        guard status == .processed, let taxonomy else { return nil }
        return SpatialSurfaceSemanticMask(frameID: frameID, epoch: epoch, modelID: modelID, taxonomy: taxonomy,
            width: width, height: height, labels: labels.map(Int.init), scores: confidence.map { Float($0)/255 })
    }
}

nonisolated struct SpatialIndoorSemanticManifest: Decodable, Sendable {
    var schemaVersion: Int
    var modelID: String
    var configSHA256: String
    var id2label: [String:String]
    var label2id: [String:Int]
    var inputWidth: Int
    var inputHeight: Int
    var outputWidth: Int
    var outputHeight: Int
    var outputs: [String:String]
    var valid: Bool {
        schemaVersion == 1 && configSHA256.count == 64 && configSHA256.allSatisfy(\.isHexDigit)
            && !modelID.isEmpty && id2label.count == 150 && label2id.count == 150
            && Set(id2label.keys.compactMap(Int.init)) == Set(0..<150)
            && Set(label2id.values) == Set(0..<150)
            && id2label.allSatisfy { pair in Int(pair.key).map { String($0) == pair.key && label2id[pair.value] == $0 } ?? false }
            && label2id["wall"] != nil && label2id["floor"] != nil
            && label2id["wall"] != label2id["floor"]
            && inputWidth == 512 && inputHeight == 512 && outputWidth == 128 && outputHeight == 128
            && outputs["labels"] == "labels" && outputs["scores"] == "scores"
    }
}

/// Serialized archive inference; JPEG pixels remain on the iPhone.
actor SpatialIndoorSemanticAnalyzer {
    private var model: VNCoreMLModel?
    private var manifest: SpatialIndoorSemanticManifest?
    private let modelURL: URL?
    private let manifestURL: URL?
    init(modelURL: URL?, manifestURL: URL?) { self.modelURL = modelURL; self.manifestURL = manifestURL }

    func analyze(frame: SpatialKeyframe, surfaces: [SpatialSurface], directory: URL) -> SpatialIndoorSemanticObservation {
        let began = ProcessInfo.processInfo.systemUptime
        var row = SpatialIndoorSemanticObservation(frameID: frame.id, frameTime: frame.time, epoch: frame.pose.epoch,
            modelID: "VQASeeIndoorSemantics/unavailable")
        do {
            if model == nil {
                guard let modelURL, let manifestURL else { throw Failure.missingModel }
                let spec = try JSONDecoder().decode(SpatialIndoorSemanticManifest.self, from: Data(contentsOf: manifestURL))
                guard spec.valid else { throw Failure.invalidManifest }
                let raw = try MLModel(contentsOf: modelURL, configuration: MLModelConfiguration())
                let metadata = raw.modelDescription.metadata[.creatorDefinedKey] as? [String:String]
                guard metadata?["configSHA256"] == spec.configSHA256, metadata?["modelID"] == spec.modelID else {
                    throw Failure.modelManifestMismatch
                }
                model = try VNCoreMLModel(for: raw); manifest = spec
            }
            guard let model, let spec = manifest else { throw Failure.missingModel }
            row.modelID = spec.modelID
            let url = directory.appendingPathComponent(frame.imageFile)
            guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
                  let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString:Any],
                  (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue == frame.width,
                  (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue == frame.height else { throw Failure.invalidImage }
            let request = VNCoreMLRequest(model: model)
            request.imageCropAndScaleOption = .scaleFill
            let turns = SpatialObjectCoordinates.turns(frame)
            let orientation: CGImagePropertyOrientation = [.up,.left,.down,.right][turns]
            try VNImageRequestHandler(url: url, orientation: orientation).perform([request])
            let features = request.results?.compactMap { $0 as? VNCoreMLFeatureValueObservation } ?? []
            guard let labels = features.first(where: { $0.featureName == "labels" })?.featureValue.multiArrayValue,
                  let scores = features.first(where: { $0.featureName == "scores" })?.featureValue.multiArrayValue,
                  labels.shape.map(\.intValue) == [1,128,128], scores.shape.map(\.intValue) == [1,128,128] else { throw Failure.invalidOutput }
            row.width = 128; row.height = 128
            var nativeLabels = [UInt8](repeating: 0, count: 128*128)
            var nativeScores = nativeLabels
            for y in 0..<128 { for x in 0..<128 {
                let p = Self.uprightIndex(x: x, y: y, size: 128, turns: turns)
                let index = [NSNumber(value: 0),NSNumber(value: p.y),NSNumber(value: p.x)]
                let label = labels[index].intValue, score = scores[index].floatValue
                guard (0..<150).contains(label), score.isFinite, (0...1).contains(score) else { throw Failure.invalidOutput }
                nativeLabels[y*128+x] = UInt8(label)
                nativeScores[y*128+x] = UInt8((score*255).rounded())
            }}
            row.labels = Data(nativeLabels); row.confidence = Data(nativeScores)
            row.taxonomy = SpatialSurfaceSemanticTaxonomy(configSHA256: spec.configSHA256,
                wallLabelID: spec.label2id["wall"]!, floorLabelID: spec.label2id["floor"]!, verified: true)
            row.status = .processed
            guard let mask = row.mask else { throw Failure.invalidOutput }
            row.records = SpatialSurfaceSemanticEvidence.evaluateAll(surfaces: surfaces.filter { $0.epoch == frame.pose.epoch }, frame: frame, mask: mask)
        } catch {
            row.status = .failed; row.failure = String(describing: error)
            row.labels = Data(); row.confidence = Data(); row.records = []
        }
        row.elapsedSeconds = ProcessInfo.processInfo.systemUptime-began
        return row
    }

    func reconcile(rows: [SpatialIndoorSemanticObservation], frames: [SpatialKeyframe], surfaces: [SpatialSurface]) -> [SpatialIndoorSemanticObservation] {
        let byID = Dictionary(uniqueKeysWithValues: frames.map { ($0.id,$0) })
        return rows.map { original in
            var row = original
            guard let frame = byID[row.frameID], let mask = row.mask else { return row }
            row.records = SpatialSurfaceSemanticEvidence.evaluateAll(surfaces: surfaces.filter { $0.epoch == frame.pose.epoch }, frame: frame, mask: mask)
            return row
        }
    }

    nonisolated static func uprightIndex(x: Int, y: Int, size: Int, turns: Int) -> (x:Int,y:Int) {
        switch (turns % 4 + 4) % 4 {
        case 1: return (y,size-1-x)
        case 2: return (size-1-x,size-1-y)
        case 3: return (size-1-y,x)
        default: return (x,y)
        }
    }
    private enum Failure: Error { case missingModel, invalidManifest, modelManifestMismatch, invalidImage, invalidOutput }
}
