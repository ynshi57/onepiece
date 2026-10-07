import ARKit
import Combine
import CoreImage
import UIKit

/// Immutable, retained ARFrame snapshot. Only its encoder queue reads the buffers.
private nonisolated struct IndoorCaptureInput: @unchecked Sendable {
    let frame: ARFrame
    let orientation: Int
    let index: Int
}

@MainActor
final class IndoorCaptureRecorder: ObservableObject {
    enum State { case idle, starting, recording, stopping }
    @Published private(set) var state: State = .idle
    @Published private(set) var savedCount = 0
    @Published private(set) var missedCount = 0
    @Published private(set) var remaining = 30
    @Published private(set) var phase = 0
    @Published private(set) var message = "每秒 1 张，最长 30 秒；不是连续视频"
    @Published private(set) var entries: [IndoorCaptureEntry] = []
    @Published var error: String?
    let store: IndoorCaptureStore
    var onSavedPacket: ((IndoorCapturePacket, IndoorCaptureManifest) -> Void)?
    var onFinishedManifest: ((IndoorCaptureManifest) -> Void)?
    private var captureID: UUID?
    private var captureARSessionID: UUID?
    private var started: Double = 0
    private var nextSample: Double = 0
    private var lastFrameTimestamp: Double?
    private var timer: Timer?
    private var pending: Task<Void, Never>?
    private var busy = 0, missing = 0, duplicates = 0, failures = 0
    private let encoder = IndoorCaptureEncoder()
    private var cancelStarting = false

    init(store: IndoorCaptureStore = IndoorCaptureStore()) { self.store = store }

    func begin(event: IndoorCaptureEvent, name: String, arSessionID: UUID, objectID: UUID?) async {
        guard state == .idle else { return }
        state = .starting; cancelStarting = false
        savedCount = 0; missedCount = 0; phase = 0; remaining = 30
        busy = 0; missing = 0; duplicates = 0; failures = 0; lastFrameTimestamp = nil
        started = ProcessInfo.processInfo.systemUptime
        let id = UUID()
        do {
            let trimmed = String(name.trimmingCharacters(in: .whitespacesAndNewlines).prefix(30))
            try await store.begin(IndoorCaptureManifest(id: id, arSessionID: arSessionID,
                objectName: trimmed.isEmpty ? "未命名物品" : trimmed, objectInstanceID: objectID ?? UUID(),
                event: event, startedAt: Date(), startedUptime: started))
            captureID = id
            captureARSessionID = arSessionID
            if cancelStarting {
                state = .recording
                await finish(reason: "相机停止或进入后台")
                return
            }
            state = .recording; nextSample = started
            message = "先观察原位置，准备好后点“开始变化”"
            timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in
                Task { @MainActor in
                    guard let self, self.state == .recording else { return }
                    self.remaining = max(0, Int(ceil(30 - (ProcessInfo.processInfo.systemUptime - self.started))))
                    if self.remaining == 0 { await self.finish(reason: "达到30秒上限") }
                }
            }
        } catch { self.error = error.localizedDescription; state = .idle }
    }

    func offer(_ frame: ARFrame?, orientation: UIInterfaceOrientation, arSessionID: UUID) {
        guard state == .recording, let id = captureID else { return }
        guard captureARSessionID == arSessionID else {
            Task { await finish(reason: "空间坐标已重置") }
            return
        }
        let now = ProcessInfo.processInfo.systemUptime
        guard now < started + 30, now >= nextSample else { return }
        nextSample = now + 1
        guard pending == nil else { busy += 1; missedCount += 1; return }
        guard let frame else { missing += 1; missedCount += 1; return }
        guard frame.timestamp >= started, frame.timestamp != lastFrameTimestamp,
              now - frame.timestamp >= 0, now - frame.timestamp <= 0.25 else {
            duplicates += 1; missedCount += 1; return
        }
        lastFrameTimestamp = frame.timestamp
        let input = IndoorCaptureInput(frame: frame, orientation: orientation.rawValue, index: savedCount)
        pending = Task { [weak self] in
            guard let self else { return }
            do {
                let packet = try await self.encoder.encode(input)
                let manifest = try await self.store.append(packet, id: id)
                self.savedCount = manifest.frames.count
                self.onSavedPacket?(packet, manifest)
            } catch {
                self.failures += 1
                self.error = "采集失败，已保存帧仍保留。\(error.localizedDescription)"
            }
            self.pending = nil
            if self.failures > 0, self.state == .recording { await self.finish(reason: "编码或保存失败") }
        }
    }

    func markTransition() async {
        guard state == .recording, let id = captureID, phase < 2 else { return }
        // Freeze the timestamp at the button press, not when the disk write finishes.
        let time = ProcessInfo.processInfo.systemUptime, completed = phase == 1
        phase += 1
        do {
            try await store.mark(id: id, time: time, completed: completed)
            message = completed ? "继续观察变化后的场景；事件标签不是逐帧真值" : "正在变化，完成后请再点一次"
        } catch { self.error = error.localizedDescription; await finish(reason: "事件标记保存失败") }
    }

    func cameraStopped() {
        if state == .starting { cancelStarting = true }
        else if state == .recording { Task { await finish(reason: "相机停止或进入后台") } }
    }

    func finish(reason: String = "用户停止") async {
        guard state == .recording, let id = captureID else { return }
        state = .stopping
        timer?.invalidate(); timer = nil
        await pending?.value
        do {
            try await store.finish(id: id, reason: reason, busy: busy, missing: missing, duplicates: duplicates, failures: failures)
            message = "已停止：\(reason)，保存 \(savedCount) 帧，未采样 \(missedCount) 次"
        } catch { self.error = "结束信息未写入，样本将显示未正常结束。\(error.localizedDescription)" }
        captureID = nil; captureARSessionID = nil; state = .idle
        await reload()
        if let manifest = entries.first(where: { $0.id == id })?.manifest { onFinishedManifest?(manifest) }
    }
    func reload() async {
        do { entries = try await store.list() }
        catch { self.error = "无法读取样本列表。\(error.localizedDescription)" }
    }
    func delete(_ id: UUID) async {
        do { try await store.delete(id); await reload() }
        catch { self.error = "删除失败。\(error.localizedDescription)" }
    }
}

/// Actor isolation keeps JPEG/depth copying and disk-independent processing off the main actor.
private actor IndoorCaptureEncoder {
    private let context = CIContext()
    func encode(_ input: IndoorCaptureInput) throws -> IndoorCapturePacket {
        let frame = input.frame, buffer = frame.capturedImage
        let width = CVPixelBufferGetWidth(buffer), height = CVPixelBufferGetHeight(buffer)
        let scale = min(1, 960 / CGFloat(max(width, height)))
        let extent = CGRect(x: 0, y: 0, width: floor(CGFloat(width) * scale), height: floor(CGFloat(height) * scale))
        let image = CIImage(cvPixelBuffer: buffer).transformed(by: CGAffineTransform(
            scaleX: extent.width / CGFloat(width), y: extent.height / CGFloat(height)))
        guard let cg = context.createCGImage(image, from: extent),
              let jpeg = UIImage(cgImage: cg).jpegData(compressionQuality: 0.8) else { throw IndoorCaptureError.invalidData }
        let k = frame.camera.intrinsics
        let intrinsics = (0..<3).flatMap { column in (0..<3).map { row in k[column][row] } }
        let pose = frame.camera.transform
        let transform = (0..<4).flatMap { column in (0..<4).map { row in pose[column][row] } }
        var depthData: Data?, confidenceData: Data?, depthWidth: Int?, depthHeight: Int?
        if let depth = frame.sceneDepth {
            let buffer = depth.depthMap
            guard CVPixelBufferGetPixelFormatType(buffer) == kCVPixelFormatType_DepthFloat32 else { throw IndoorCaptureError.invalidData }
            let w = CVPixelBufferGetWidth(buffer), h = CVPixelBufferGetHeight(buffer)
            depthWidth = w; depthHeight = h
            CVPixelBufferLockBaseAddress(buffer, .readOnly)
            defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
            guard let base = CVPixelBufferGetBaseAddress(buffer) else { throw IndoorCaptureError.invalidData }
            var bytes = Data(capacity: w * h * 4)
            for y in 0..<h {
                let row = base.advanced(by: y * CVPixelBufferGetBytesPerRow(buffer)).assumingMemoryBound(to: Float32.self)
                for x in 0..<w {
                    var bits = row[x].bitPattern.littleEndian
                    withUnsafeBytes(of: &bits) { bytes.append(contentsOf: $0) }
                }
            }
            depthData = bytes
            if let confidence = depth.confidenceMap {
                guard CVPixelBufferGetPixelFormatType(confidence) == kCVPixelFormatType_OneComponent8,
                      CVPixelBufferGetWidth(confidence) == w, CVPixelBufferGetHeight(confidence) == h else { throw IndoorCaptureError.invalidData }
                CVPixelBufferLockBaseAddress(confidence, .readOnly)
                defer { CVPixelBufferUnlockBaseAddress(confidence, .readOnly) }
                guard let base = CVPixelBufferGetBaseAddress(confidence) else { throw IndoorCaptureError.invalidData }
                var bytes = Data(capacity: w * h)
                for y in 0..<h { bytes.append(base.advanced(by: y * CVPixelBufferGetBytesPerRow(confidence)).assumingMemoryBound(to: UInt8.self), count: w) }
                confidenceData = bytes
            }
        }
        let tracking: String
        switch frame.camera.trackingState {
        case .normal: tracking = "normal"
        case .notAvailable: tracking = "unavailable"
        case .limited(let reason): tracking = "limited:\(reason)"
        }
        let metadata = IndoorCaptureFrame(id: input.index, timestamp: frame.timestamp, tracking: tracking,
            sensorWidth: width, sensorHeight: height, imageWidth: cg.width, imageHeight: cg.height,
            interfaceOrientation: input.orientation, cameraToWorld: transform, intrinsics: intrinsics,
            encodedIntrinsics: IndoorCaptureStore.scaledIntrinsics(intrinsics, x: Float(cg.width) / Float(width), y: Float(cg.height) / Float(height)),
            imageSHA256: IndoorCaptureStore.hash(jpeg), depthWidth: depthWidth, depthHeight: depthHeight,
            depthSHA256: depthData.map(IndoorCaptureStore.hash), confidenceSHA256: confidenceData.map(IndoorCaptureStore.hash),
            depthUnavailableReason: depthData == nil ? "设备不支持或本帧未提供sceneDepth" : nil)
        return IndoorCapturePacket(metadata: metadata, jpeg: jpeg, depth: depthData, confidence: confidenceData)
    }
}
