import CoreGraphics
import CoreVideo
import Foundation
import ImageIO
import Vision

// VQASee offline perception harness.
//
// Reads a platform path-guidance manifest (JSONL with `image_path`), runs the
// REAL on-device perception stack (LocalVisionAnalyzer -> YOLO11n Core ML +
// LocalPathGuidanceEngine) over each image, and emits prediction JSONL in the
// platform schema so evaluate/parity/gate can score the actual iPhone stack.
//
// Fidelity note: macOS has no ARKit/LiDAR and the segmentation/depth models are
// not bundled, so this reflects the iPhone "camera-only" branch. That is stated
// explicitly in every emitted row (depth_capability / segmentation_capability).

struct HarnessError: Error, CustomStringConvertible {
    let message: String
    var description: String { message }
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

// MARK: - Arguments

struct Arguments {
    var manifest: String
    var modelDir: String
    var out: String?
    var limit: Int
    var configPath: String?
}

func parseArguments() -> Arguments {
    var manifest: String?
    var modelDir: String?
    var out: String?
    var limit = 0
    var configPath: String?

    var iterator = CommandLine.arguments.dropFirst().makeIterator()
    while let arg = iterator.next() {
        switch arg {
        case "--manifest":
            manifest = iterator.next()
        case "--model", "--model-dir":
            modelDir = iterator.next()
        case "--out":
            out = iterator.next()
        case "--limit":
            limit = Int(iterator.next() ?? "0") ?? 0
        case "--config":
            configPath = iterator.next()
        case "-h", "--help":
            print("Usage: PerceptionHarness --manifest <path.jsonl> [--model <dir>] [--out <path.jsonl>] [--limit N] [--config <perception_config.json>]")
            exit(0)
        default:
            fail("unknown argument: \(arg)")
        }
    }

    guard let manifestPath = manifest else {
        fail("missing required --manifest <path.jsonl>")
    }

    // Default model dir: the app's VQASee source dir (holds YOLO11nObject.mlmodelc),
    // resolved relative to this source file so it works from any CWD.
    let defaultModelDir = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent() // PerceptionHarness
        .deletingLastPathComponent() // Sources
        .deletingLastPathComponent() // perception-harness
        .deletingLastPathComponent() // ios-vqa-app
        .appendingPathComponent("VQASee/VQASee")
        .path

    return Arguments(
        manifest: manifestPath,
        modelDir: modelDir ?? defaultModelDir,
        out: out,
        limit: limit,
        configPath: configPath
    )
}

func loadConfig(_ path: String?) -> PerceptionConfig {
    guard let path else { return .default }
    guard let data = FileManager.default.contents(atPath: path) else {
        fail("cannot read --config file: \(path)")
    }
    guard let config = PerceptionConfig.from(jsonData: data) else {
        fail("--config is not a valid perception config (schema/range check failed): \(path)")
    }
    return config
}

// MARK: - Image decoding

func makeBlankPixelBuffer(width: Int = 64, height: Int = 64) -> CVPixelBuffer? {
    var pixelBuffer: CVPixelBuffer?
    let status = CVPixelBufferCreate(
        kCFAllocatorDefault, width, height, kCVPixelFormatType_32BGRA, nil, &pixelBuffer
    )
    guard status == kCVReturnSuccess else { return nil }
    return pixelBuffer
}

func makePixelBuffer(from url: URL) -> CVPixelBuffer? {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let cgImage = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        return nil
    }
    let width = cgImage.width
    let height = cgImage.height
    guard width > 0, height > 0 else { return nil }

    let attributes: [CFString: Any] = [
        kCVPixelBufferCGImageCompatibilityKey: true,
        kCVPixelBufferCGBitmapContextCompatibilityKey: true,
    ]
    var pixelBuffer: CVPixelBuffer?
    let status = CVPixelBufferCreate(
        kCFAllocatorDefault, width, height, kCVPixelFormatType_32BGRA,
        attributes as CFDictionary, &pixelBuffer
    )
    guard status == kCVReturnSuccess, let buffer = pixelBuffer else { return nil }

    CVPixelBufferLockBaseAddress(buffer, [])
    defer { CVPixelBufferUnlockBaseAddress(buffer, []) }
    // BGRA byte layout matches the luminance reader in LocalVisionAnalyzer.
    guard let context = CGContext(
        data: CVPixelBufferGetBaseAddress(buffer),
        width: width, height: height,
        bitsPerComponent: 8,
        bytesPerRow: CVPixelBufferGetBytesPerRow(buffer),
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue
    ) else {
        return nil
    }
    context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
    return buffer
}

// MARK: - Manifest IO

func readManifestRows(_ path: String) -> [[String: Any]] {
    guard let text = try? String(contentsOfFile: path, encoding: .utf8) else {
        fail("cannot read manifest: \(path)")
    }
    var rows: [[String: Any]] = []
    for line in text.split(separator: "\n") {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        if trimmed.isEmpty { continue }
        guard let data = trimmed.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            continue
        }
        rows.append(object)
    }
    return rows
}

func resolveImagePath(_ row: [String: Any], manifestDir: URL) -> String? {
    let raw = (row["image_path"] as? String) ?? (row["image"] as? String)
    guard let value = raw, !value.isEmpty else { return nil }
    if value.hasPrefix("/") { return value }
    return manifestDir.appendingPathComponent(value).path
}

func frameID(_ row: [String: Any]) -> String {
    if let value = row["frame_id"] as? String, !value.isEmpty { return value }
    if let value = row["frame"] as? String, !value.isEmpty { return value }
    if let value = row["image"] as? String, !value.isEmpty { return value }
    return UUID().uuidString
}

// MARK: - Main

let args = parseArguments()
let manifestURL = URL(fileURLWithPath: args.manifest)
let manifestDir = manifestURL.deletingLastPathComponent()

let modelURL = URL(fileURLWithPath: args.modelDir, isDirectory: true)
guard let modelBundle = Bundle(url: modelURL) else {
    fail("model directory is not a readable bundle: \(args.modelDir)")
}
let config = loadConfig(args.configPath)
let analyzer = LocalVisionAnalyzer(modelBundle: modelBundle, config: config)

// Fail loud if the YOLO model did not load: a benchmark on an empty detector
// would silently report everything as open. This is a safety-relevant guardrail.
if let probe = makeBlankPixelBuffer() {
    let probeSignal = analyzer.analyze(pixelBuffer: probe, orientation: .up, depthCapability: .unsupported)
    if probeSignal.perception.modelStatus == .unavailable {
        fail("""
        YOLO Core ML model not found under \(args.modelDir).
        Expected YOLO11nObject.mlmodelc. Run deploy/ios/export_yolo11_coreml.sh, or pass --model <dir>.
        """)
    }
}

let rows = readManifestRows(args.manifest)
var outputLines: [String] = []
var predicted = 0
var missingImage = 0
var decodeErrors = 0

for row in rows {
    if args.limit > 0 && predicted >= args.limit { break }
    let id = frameID(row)
    guard let imagePath = resolveImagePath(row, manifestDir: manifestDir) else {
        missingImage += 1
        continue
    }
    guard let buffer = makePixelBuffer(from: URL(fileURLWithPath: imagePath)) else {
        decodeErrors += 1
        FileHandle.standardError.write(Data("decode_failed: \(imagePath)\n".utf8))
        continue
    }
    let signal = analyzer.analyze(pixelBuffer: buffer, orientation: .up, depthCapability: .unsupported)
    let guidance = signal.perception.pathGuidance

    let prediction: [String: Any] = [
        "near_path_status": guidance.nearPathStatus.rawValue,
        "left_front_status": guidance.leftFrontStatus.rawValue,
        "right_front_status": guidance.rightFrontStatus.rawValue,
        "focus_direction": guidance.focusDirection.rawValue,
        "confidence": guidance.confidence,
        "depth_capability": guidance.depthCapability.rawValue,
        "segmentation_capability": guidance.segmentationCapability.rawValue,
        "prediction_source": "ios_coreml_offline_harness",
        "config_version": config.version,
    ]

    // Detected objects (Vision-normalized boxes, origin lower-left) so the
    // platform can DRAW what the on-device perception actually recognized.
    var objectsOut: [[String: Any]] = []
    for object in signal.perception.objects {
        var entry: [String: Any] = [
            "kind": object.kind.rawValue,
            "label": object.kind.chineseLabel,
            "confidence": object.confidence,
            "direction": object.direction.rawValue,
        ]
        if let box = object.normalizedBoundingBox {
            entry["box"] = [
                "x": Double(box.origin.x),
                "y": Double(box.origin.y),
                "w": Double(box.size.width),
                "h": Double(box.size.height),
            ]
        }
        objectsOut.append(entry)
    }

    // ROI rects actually used (from the config that ran) so overlays never drift
    // from the decision. Vision-normalized, origin lower-left.
    func roiDict(_ rect: CGRect) -> [String: Any] {
        ["x": Double(rect.origin.x), "y": Double(rect.origin.y), "w": Double(rect.size.width), "h": Double(rect.size.height)]
    }
    let outRow: [String: Any] = [
        "frame_id": id,
        "prediction": prediction,
        "objects": objectsOut,
        "roi": [
            "near": roiDict(config.nearROI),
            "left": roiDict(config.leftROI),
            "right": roiDict(config.rightROI),
        ],
    ]
    guard let data = try? JSONSerialization.data(withJSONObject: outRow, options: [.sortedKeys]),
          let jsonLine = String(data: data, encoding: .utf8) else {
        continue
    }
    outputLines.append(jsonLine)
    predicted += 1
}

let payload = outputLines.joined(separator: "\n") + (outputLines.isEmpty ? "" : "\n")
if let outPath = args.out {
    do {
        try payload.write(toFile: outPath, atomically: true, encoding: .utf8)
    } catch {
        fail("cannot write output: \(outPath): \(error)")
    }
} else {
    FileHandle.standardOutput.write(Data(payload.utf8))
}

FileHandle.standardError.write(Data(
    "harness done: predicted=\(predicted) missing_image=\(missingImage) decode_errors=\(decodeErrors)\n".utf8
))
