import SwiftUI
import UIKit

/// Debug uploads never delay saving AR samples. A bounded FIFO exposes loss explicitly.
@MainActor
final class DeviceDebugEvidenceBridge {
    private struct Item { let session: String; let files: [(String, Data)] }
    private var queue: [Item] = []
    private var worker: Task<Void, Never>?
    private weak var debug: DeviceDebugController?
    init(debug: DeviceDebugController) { self.debug = debug }
    func reference(_ record: IndoorMemoryRecord) {
        guard let debug, debug.isActive, debug.shareOriginalSamples, let sid = debug.sessionID else { return }
        do {
            let base = record.id.uuidString + "-reference"
            let metadata = IndoorRecordMetadata(target: record.target, hasSpatialAnchor: record.hasSpatialAnchor, spatialSource: record.spatialSource)
            enqueue(Item(session: sid, files: [(base + ".jpg", record.photo), (base + ".json", try JSONEncoder().encode(metadata))]))
        } catch { debug.reportAttachmentFailure(count: 2, reason: "参考物品资料编码失败，请重新选择该记录重试") }
    }
    func packet(_ packet: IndoorCapturePacket, manifest: IndoorCaptureManifest) {
        guard let debug, debug.isActive, debug.shareOriginalSamples, let sid = debug.sessionID else { return }
        do {
            let base = "\(manifest.id.uuidString)-\(packet.metadata.id)"
            var files = [(base + ".jpg", packet.jpeg), (base + ".json", try JSONEncoder().encode(packet.metadata))]
            if let depth = packet.depth { files.append((base + ".depth", depth)) }
            if let confidence = packet.confidence { files.append((base + ".confidence", confidence)) }
            enqueue(Item(session: sid, files: files))
        } catch { debug.recordEvent("sample_export_failed", details: ["reason": "metadata_encoding"]) }
    }
    func manifest(_ manifest: IndoorCaptureManifest) {
        guard let debug, debug.isActive, debug.shareOriginalSamples, let sid = debug.sessionID else { return }
        do { enqueue(Item(session: sid, files: [(manifest.id.uuidString + "-manifest.json", try JSONEncoder().encode(manifest))])) }
        catch { debug.recordEvent("sample_export_failed", details: ["reason": "manifest_encoding"]) }
    }
    private func enqueue(_ item: Item) {
        guard queue.count < 4 else {
            debug?.reportAttachmentFailure(count: item.files.count, reason: "样本上传队列已满，本机样本仍保留，可在样本回放中补传")
            debug?.recordEvent("sample_export_dropped", details: ["reason": "upload_queue_full", "local_sample": "retained"])
            return
        }
        queue.append(item)
        guard worker == nil else { return }
        worker = Task { [weak self] in
            guard let self else { return }
            while !self.queue.isEmpty {
                let item = self.queue.removeFirst()
                guard let debug = self.debug else { continue }
                guard debug.isActive, debug.shareOriginalSamples, debug.sessionID == item.session else {
                    debug.reportAttachmentFailure(count: item.files.count, reason: "共享已停止或授权已关闭，本机样本可补传")
                    continue
                }
                for (index, file) in item.files.enumerated() {
                    let (name, data) = file
                    guard debug.isActive, debug.shareOriginalSamples, debug.sessionID == item.session else {
                        debug.reportAttachmentFailure(count: item.files.count-index, reason: "部分样本未送达，本机仍保留，可补传")
                        break
                    }
                    await debug.uploadAttachment(data: data, filename: name)
                }
            }
            self.worker = nil
        }
    }

    static func resend(_ manifest: IndoorCaptureManifest, store: IndoorCaptureStore, debug: DeviceDebugController) async -> Bool {
        guard debug.isActive, debug.shareOriginalSamples else { return false }
        let session = debug.sessionID
        do {
            for index in manifest.frames.indices {
                guard debug.sessionID == session else { return false }
                let packet = try await store.readFrame(id: manifest.id, index: index)
                let base = "\(manifest.id.uuidString)-\(packet.metadata.id)"
                var files = [(base + ".jpg", packet.jpeg), (base + ".json", try JSONEncoder().encode(packet.metadata))]
                if let depth = packet.depth { files.append((base + ".depth", depth)) }
                if let confidence = packet.confidence { files.append((base + ".confidence", confidence)) }
                for (name, data) in files {
                    guard await debug.uploadAttachment(data: data, filename: name) else { return false }
                }
            }
            return await debug.uploadAttachment(data: try JSONEncoder().encode(manifest), filename: manifest.id.uuidString + "-manifest.json")
        } catch {
            debug.reportAttachmentFailure(reason: "本机样本未通过完整性检查，不能补传")
            return false
        }
    }
}

/// Passive touch coordinates, including attempts on disabled controls. No text/keystrokes.
struct DeviceDebugTouchObserver: UIViewRepresentable {
    let debug: DeviceDebugController
    func makeUIView(context: Context) -> TouchAttachment { TouchAttachment(debug: debug) }
    func updateUIView(_ view: TouchAttachment, context: Context) {}
    static func dismantleUIView(_ view: TouchAttachment, coordinator: ()) { view.detach() }
    final class TouchAttachment: UIView {
        let observer: Observer
        weak var attachedWindow: UIWindow?
        init(debug: DeviceDebugController) {
            observer = Observer(debug: debug)
            super.init(frame: .zero); isUserInteractionEnabled = false
        }
        required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
        override func didMoveToWindow() {
            super.didMoveToWindow(); detach()
            if let window { window.addGestureRecognizer(observer); attachedWindow = window }
        }
        func detach() { attachedWindow?.removeGestureRecognizer(observer); attachedWindow = nil }
    }
    final class Observer: UIGestureRecognizer, UIGestureRecognizerDelegate {
        weak var debug: DeviceDebugController?
        init(debug: DeviceDebugController) {
            self.debug = debug; super.init(target: nil, action: nil)
            cancelsTouchesInView = false; delaysTouchesBegan = false; delaysTouchesEnded = false; delegate = self
        }
        override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent) {
            if let touch = touches.first, let window = view, window.bounds.width > 0, window.bounds.height > 0 {
                let p = touch.location(in: window)
                debug?.recordEvent("touch_ended", details: ["x": String(format: "%.4f", p.x / window.bounds.width),
                    "y": String(format: "%.4f", p.y / window.bounds.height), "coordinate_space": "window_normalized",
                    "meaning": "touch_only_not_action_confirmation"])
            }
            state = .failed
        }
        override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent) { state = .failed }
        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer) -> Bool { true }
        override func canPrevent(_ preventedGestureRecognizer: UIGestureRecognizer) -> Bool { false }
        override func canBePrevented(by preventingGestureRecognizer: UIGestureRecognizer) -> Bool { false }
    }
}
