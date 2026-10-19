// Offline candidate detector; does not change App filtering or assert object identity.
import Foundation
import Vision
import CoreML

let args = CommandLine.arguments
 guard args.count == 4 || args.count == 5 else { fatalError("usage: scene_detector_control model.mlmodelc input-directory output.json [scaleFill|scaleFit]") }
let scaling = args.count == 5 ? args[4] : "scaleFill"
guard ["scaleFill", "scaleFit"].contains(scaling) else { fatalError("invalid scaling") }
let inputs = URL(fileURLWithPath: args[2])
let destination = URL(fileURLWithPath: args[3])
guard !FileManager.default.fileExists(atPath: destination.path) else { fatalError("output exists") }
let sourceData = try Data(contentsOf: inputs.appendingPathComponent("inputs.json"))
guard let source = try JSONSerialization.jsonObject(with: sourceData) as? [String: Any],
      let sourceFrames = source["frames"] as? [[String: Any]] else { fatalError("invalid inputs manifest") }
let start = Date()
let config = MLModelConfiguration()
config.computeUnits = .cpuOnly
let model = try MLModel(contentsOf: URL(fileURLWithPath: args[1]), configuration: config)
let vision = try VNCoreMLModel(for: model)
let loadSeconds = Date().timeIntervalSince(start)
let files = try FileManager.default.contentsOfDirectory(at: inputs, includingPropertiesForKeys: nil)
    .filter { $0.lastPathComponent.hasSuffix("-input.jpg") }.sorted { $0.path < $1.path }
guard !files.isEmpty else { fatalError("no input JPEGs") }
var rows: [[String: Any]] = []
for file in files {
    let began = Date()
    var row: [String: Any] = ["inputImage": file.lastPathComponent, "status": "failed", "objects": []]
    do {
        let request = VNCoreMLRequest(model: vision)
        request.imageCropAndScaleOption = scaling == "scaleFit" ? .scaleFit : .scaleFill
        try VNImageRequestHandler(url: file, orientation: .up).perform([request])
        guard let observations = request.results else {
            throw NSError(domain: "scene-detector", code: 2, userInfo: [NSLocalizedDescriptionKey: "missing Vision result"])
        }
        guard observations.allSatisfy({ $0 is VNRecognizedObjectObservation }) else {
            throw NSError(domain: "scene-detector", code: 1, userInfo: [NSLocalizedDescriptionKey: "unexpected result type"])
        }
        row["objects"] = observations.compactMap { observation -> [String: Any]? in
            guard let object = observation as? VNRecognizedObjectObservation else { return nil }
            let b = object.boundingBox
            return ["labels": object.labels.map { ["name": $0.identifier, "modelScore": Double($0.confidence)] },
                    "pixelBBox": [Double(b.minX), Double(1-b.maxY), Double(b.maxX), Double(1-b.minY)],
                    "entityID": NSNull(), "worldPosition": NSNull()]
        }
        row["status"] = "processed"
    } catch { row["failure"] = String(describing: error) }
    guard let frame = sourceFrames.first(where: { $0["inputImage"] as? String == file.lastPathComponent }) else { fatalError("missing frame identity") }
    row["sourceFrame"] = frame
    row["elapsedSeconds"] = Date().timeIntervalSince(began)
    rows.append(row)
    print(file.lastPathComponent, row["status"]!, row["elapsedSeconds"]!)
    fflush(stdout)
}
let report: [String: Any] = ["source": source, "sourceIdentityVerified": false, "modelPath": args[1], "computeUnits": "cpuOnly", "cropAndScale": scaling,
    "loadSeconds": loadSeconds, "rows": rows, "productAccepted": false,
    "bboxConvention": "normalized upright JPEG top-left [left,top,right,bottom]",
    "outputPolicy": "all Vision detections and labels retained; compiled model internal NMS/confidence filtering applies",
    "modelMetadata": model.modelDescription.metadata.mapKeysForJSON(),
    "scope": "single-frame closed-vocabulary detection candidates; no identity, absence or 3D established"]
try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(to: destination, options: .atomic)

extension Dictionary where Key == MLModelMetadataKey, Value == Any {
    func mapKeysForJSON() -> [String: String] { Dictionary<String, String>(uniqueKeysWithValues: map { ($0.key.rawValue, String(describing: $0.value)) }) }
}
