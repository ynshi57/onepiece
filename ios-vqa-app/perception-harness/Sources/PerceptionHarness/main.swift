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
    var laneModel: String?
    var lanePolylineModel: String?
    var segModel: String?
}

func parseArguments() -> Arguments {
    var manifest: String?
    var modelDir: String?
    var out: String?
    var limit = 0
    var configPath: String?
    var laneModel: String?
    var lanePolylineModel: String?
    var segModel: String?

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
        case "--lane-model":
            laneModel = iterator.next()
        case "--lane-polyline-model":
            lanePolylineModel = iterator.next()
        case "--seg-model":
            segModel = iterator.next()
        case "-h", "--help":
            print("Usage: PerceptionHarness --manifest <path.jsonl> [--model <dir>] [--seg-model <VQASeeTraversabilitySeg5.mlmodelc>] [--lane-model <VQASeeLaneSegmentation.mlmodelc>] [--lane-polyline-model <VQASeeLaneUFLDv2.mlmodelc>] [--out <path.jsonl>] [--limit N] [--config <perception_config.json>]")
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
        configPath: configPath,
        laneModel: laneModel,
        lanePolylineModel: lanePolylineModel,
        segModel: segModel
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

func posixJoin(_ base: URL, _ relative: String) -> String {
    var rel = relative.replacingOccurrences(of: "\\", with: "/")
    while rel.hasPrefix("./") {
        rel = String(rel.dropFirst(2))
    }
    if rel.hasPrefix("/") { return rel }
    return (base.path as NSString).appendingPathComponent(rel)
}

func guessRepoRoot(from start: URL) -> URL {
    let fm = FileManager.default
    var dir = start
    for _ in 0..<10 {
        if fm.fileExists(atPath: posixJoin(dir, "dataset/camvid"))
            || fm.fileExists(atPath: posixJoin(dir, "docs/datasets")) {
            return dir
        }
        let parent = dir.deletingLastPathComponent()
        if parent.path == dir.path { break }
        dir = parent
    }
    return start.deletingLastPathComponent().deletingLastPathComponent()
}

func remapCamVidPath(_ value: String, repoRoot: URL) -> String? {
    let posix = value.replacingOccurrences(of: "\\", with: "/")
    for marker in ["CamVid_RGB/", "CamVid_Label/"] {
        if let range = posix.range(of: marker) {
            let tail = String(posix[range.lowerBound...])
            return posixJoin(repoRoot, "dataset/camvid/" + tail)
        }
    }
    return nil
}

func resolveImagePath(_ row: [String: Any], manifestDir: URL) -> String? {
    let raw = (row["image_path"] as? String) ?? (row["image"] as? String)
    guard let value = raw, !value.isEmpty else { return nil }
    let fm = FileManager.default
    // Relative `dataset/camvid/...` is from the repo root, not from
    // `docs/datasets/` (the manifest directory). Prefer repo-root joins;
    // treat manifest-dir joins as last resort so a miss reports the real path.
    let repoRoot = guessRepoRoot(from: manifestDir)
    let cwd = URL(fileURLWithPath: fm.currentDirectoryPath, isDirectory: true)
    var preferred: [String] = []
    var lastResort: [String] = []
    func appendUnique(_ path: String, lastResort last: Bool = false) {
        if preferred.contains(path) || lastResort.contains(path) { return }
        if last { lastResort.append(path) } else { preferred.append(path) }
    }
    if value.hasPrefix("/") {
        appendUnique(value)
    } else {
        appendUnique(posixJoin(repoRoot, value))
        appendUnique(posixJoin(cwd, value))
        appendUnique(posixJoin(manifestDir, value), lastResort: true)
    }
    if let remapped = remapCamVidPath(value, repoRoot: repoRoot) {
        appendUnique(remapped)
    }
    if let remapped = remapCamVidPath(value, repoRoot: cwd) {
        appendUnique(remapped)
    }
    for path in preferred + lastResort {
        if fm.fileExists(atPath: path) { return path }
    }
    return preferred.first ?? lastResort.first
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

// T4 device lane channel: load the dedicated lane segmenter from an explicit
// compiled .mlmodelc path (so we don't copy the model into the shipping source
// tree). Fail loud if the caller asked for a lane model but it won't load — a
// silent "no lanes" would look like the model works and finds nothing.
var laneRunner: LocalLaneSegmentationRunner?
if let laneModelPath = args.laneModel {
    guard FileManager.default.fileExists(atPath: laneModelPath) else {
        fail("--lane-model not found: \(laneModelPath) (compile the .mlpackage with `xcrun coremlcompiler compile`)")
    }
    guard let runner = LocalLaneSegmentationRunner(compiledModelURL: URL(fileURLWithPath: laneModelPath)) else {
        fail("--lane-model failed to load as a Core ML model: \(laneModelPath)")
    }
    laneRunner = runner
    FileHandle.standardError.write(Data("lane model loaded: \(laneModelPath)\n".utf8))
}

// Product lane-line channel: UFLDv2 row/column anchors -> normalized polylines.
// Explicit opt-in because the current CULane model is large; absence means "no
// geometry channel", not "no lanes in the frame".
var lanePolylineRunner: LocalLanePolylineRunner?
if let lanePolylineModelPath = args.lanePolylineModel {
    guard FileManager.default.fileExists(atPath: lanePolylineModelPath) else {
        fail("--lane-polyline-model not found: \(lanePolylineModelPath) (compile the .mlpackage with `xcrun coremlcompiler compile`)")
    }
    guard let runner = LocalLanePolylineRunner(compiledModelURL: URL(fileURLWithPath: lanePolylineModelPath)) else {
        fail("--lane-polyline-model failed to load as a Core ML model: \(lanePolylineModelPath)")
    }
    lanePolylineRunner = runner
    FileHandle.standardError.write(Data("lane polyline model loaded: \(lanePolylineModelPath)\n".utf8))
}

// G: optional explicit segmentation model (the N=5 multiclass model compiled
// outside the shipping source tree). When given, the harness derives the
// role-conditioned walkable region (config.role: pedestrian → sidewalk, vehicle →
// road+lane) from the SAME per-frame logits. Fail loud if requested but unloadable
// — a silent fallback to the bundled binary model would misattribute the results.
var segRunner: LocalTraversabilitySegmentationRunner?
if let segModelPath = args.segModel {
    guard FileManager.default.fileExists(atPath: segModelPath) else {
        fail("--seg-model not found: \(segModelPath) (compile the .mlpackage with `xcrun coremlcompiler compile`)")
    }
    guard let runner = LocalTraversabilitySegmentationRunner(compiledModelURL: URL(fileURLWithPath: segModelPath)) else {
        fail("--seg-model failed to load as a Core ML model: \(segModelPath)")
    }
    segRunner = runner
    FileHandle.standardError.write(Data("seg model loaded: \(segModelPath) (role=\(config.role.rawValue))\n".utf8))
}

// emitTraversableGrid: export the full walkable-region raster the segmentation
// model perceives, so the platform can render "iPhone-perceived green region" and
// score region IoU against the CamVid ground truth. This is the offline path; the
// on-device app keeps this OFF to protect the real-time frame budget.
let analyzer = LocalVisionAnalyzer(
    modelBundle: modelBundle,
    config: config,
    emitTraversableGrid: true,
    laneRunner: laneRunner,
    lanePolylineRunner: lanePolylineRunner,
    segmentationRunner: segRunner
)

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
// Lane channel latency budget (T4): per-frame lane-model forward + grid build.
var laneLatenciesMs: [Double] = []
var laneFramesWithLane = 0
// Consolidated on-device perception latency budget (F): per-stage wall-clock so a
// real end-to-end p50/p95 can be reported instead of only per-model numbers.
var yoloMs: [Double] = []
var segMs: [Double] = []
var depthMs: [Double] = []
var totalMs: [Double] = []

for row in rows {
    if args.limit > 0 && predicted >= args.limit { break }
    let id = frameID(row)
    guard let imagePath = resolveImagePath(row, manifestDir: manifestDir) else {
        missingImage += 1
        continue
    }
    // Distinguish "file is gone from disk" from "file exists but won't decode".
    // Conflating them (all -> decode_failed) sent us chasing an image-corruption
    // ghost when the real cause was a purged /tmp dataset. Report the truth.
    if !FileManager.default.fileExists(atPath: imagePath) {
        missingImage += 1
        FileHandle.standardError.write(Data("image_not_found: \(imagePath)\n".utf8))
        continue
    }
    guard let buffer = makePixelBuffer(from: URL(fileURLWithPath: imagePath)) else {
        decodeErrors += 1
        FileHandle.standardError.write(Data("decode_failed: \(imagePath)\n".utf8))
        continue
    }
    let signal = analyzer.analyze(pixelBuffer: buffer, orientation: .up, depthCapability: .unsupported)
    let stageTimings = analyzer.lastTimings
    if let v = stageTimings.yoloMs { yoloMs.append(v) }
    if let v = stageTimings.segmentationMs { segMs.append(v) }
    if let v = stageTimings.depthMs { depthMs.append(v) }
    if let v = stageTimings.totalMs { totalMs.append(v) }
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

    // Predicted traversable guidance line from on-device segmentation. Emit even
    // when insufficient so the platform can see the degrade (never silently drop).
    let guidancePathOut: [String: Any] = (signal.perception.guidancePath ?? GuidancePath.insufficient).toWire()

    // NOTE: the legacy `roi` field (near/left/right decision rectangles) was
    // removed — the guidance line + traversable_grid are the signals the platform
    // draws and scores now. The prediction still carries the coarse *_status
    // values so the case layer can keep clustering until it re-anchors on region.
    var outRow: [String: Any] = [
        "frame_id": id,
        "prediction": prediction,
        "guidance_path": guidancePathOut,
        "objects": objectsOut,
    ]
    // Walkable-region raster the on-device segmentation perceives. Present only
    // when segmentation ran (model available + produced output); absent means "no
    // region perceived" — the platform states that explicitly rather than drawing
    // a fabricated green area.
    if let grid = signal.perception.traversableGrid {
        outRow["traversable_grid"] = grid.toWire()
    }
    // T4: lane-marking raster from the dedicated on-device lane segmenter. Present
    // only when a lane model was loaded AND produced output; absent means "no lane
    // channel" — stated explicitly, never a fabricated empty lane.
    if let laneGrid = signal.perception.laneGrid {
        outRow["lane_grid"] = laneGrid.toWire()
        if laneGrid.cells.contains(where: { $0 != 0 }) { laneFramesWithLane += 1 }
    }
    if !signal.perception.lanePolylines.isEmpty {
        outRow["lane_polylines"] = signal.perception.lanePolylines.map { $0.toWire() }
    }
    if let ms = stageTimings.laneMs {
        laneLatenciesMs.append(ms)
    }
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

// T4 latency budget: report the added lane-model cost so 罗根 can decide whether
// the live device path can afford a second forward pass (and whether to merge
// lane into the multi-class head instead).
if !laneLatenciesMs.isEmpty {
    let sorted = laneLatenciesMs.sorted()
    let mean = laneLatenciesMs.reduce(0, +) / Double(laneLatenciesMs.count)
    let p50 = sorted[sorted.count / 2]
    let p95 = sorted[min(sorted.count - 1, Int(Double(sorted.count) * 0.95))]
    let budget = String(
        format: "lane latency budget (n=%d): mean=%.1fms p50=%.1fms p95=%.1fms max=%.1fms · frames_with_lane=%d/%d\n",
        laneLatenciesMs.count, mean, p50, p95, sorted.last ?? 0, laneFramesWithLane, predicted
    )
    FileHandle.standardError.write(Data(budget.utf8))
}

// Consolidated on-device perception latency budget (F). Honest caveat: these are
// MAC wall-clock numbers (the harness runs on macOS, not iPhone); use them to rank
// stages + spot regressions, but the signed on-device budget must come from a real
// device run (罗根). Absent stages (e.g. lane off, native depth present) are shown
// as n=0 rather than pretended to be zero-cost.
func latencyBudget(_ name: String, _ xs: [Double]) -> String {
    let label = name.padding(toLength: 12, withPad: " ", startingAt: 0)
    guard !xs.isEmpty else { return "  \(label) n=0 (did not run)" }
    let sorted = xs.sorted()
    let mean = xs.reduce(0, +) / Double(xs.count)
    let p50 = sorted[sorted.count / 2]
    let p95 = sorted[min(sorted.count - 1, Int(Double(sorted.count) * 0.95))]
    return "  \(label) " + String(
        format: "n=%d mean=%.1fms p50=%.1fms p95=%.1fms max=%.1fms",
        xs.count, mean, p50, p95, sorted.last ?? 0
    )
}
if !totalMs.isEmpty {
    var lines = ["on-device perception latency budget (MAC wall-clock, not iPhone):"]
    lines.append(latencyBudget("yolo", yoloMs))
    lines.append(latencyBudget("segmentation", segMs))
    lines.append(latencyBudget("depth", depthMs))
    lines.append(latencyBudget("lane", laneLatenciesMs))
    lines.append(latencyBudget("total", totalMs))
    FileHandle.standardError.write(Data((lines.joined(separator: "\n") + "\n").utf8))
}
