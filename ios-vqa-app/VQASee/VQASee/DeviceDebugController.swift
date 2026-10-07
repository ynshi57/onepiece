import AVFoundation
import Combine
import CoreImage
import ReplayKit
import UIKit

/// The pairing secret is memory-only; redirects must never forward it to another host.
private final class DeviceDebugNoRedirect: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

enum DeviceDebugPolicy {
    static func pairing(_ text: String) -> (url: String, token: String, previousSessionID: String?)? {
        if let data = text.data(using: .utf8),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: String],
           let url = object["url"], let token = object["token"], baseURL(url) != nil, token.count >= 16 {
            let previous = object["previous_session_id"]
            guard previous == nil || UUID(uuidString: previous!) != nil else { return nil }
            return (url, token, previous)
        }
        guard var components = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              let token = components.fragment, token.count >= 16 else { return nil }
        components.fragment = nil
        guard let url = components.url?.absoluteString, baseURL(url) != nil else { return nil }
        return (url, token, nil)
    }
    static func baseURL(_ value: String) -> URL? {
        guard let url = URL(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              let host = url.host, !host.isEmpty,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.isEmpty || url.path == "/" else { return nil }
        return url
    }
    static func validFilename(_ name: String) -> Bool {
        !name.isEmpty && name.count <= 150 && name != "." && name != ".."
            && !name.contains(where: { "/\\\r\n\0".contains($0) })
    }
    static func serverError(_ status: Int) -> String {
        switch status {
        case 401, 403: return "连接授权已失效，请重新选择 Mac 并允许连接"
        case 404, 409: return "调试会话不存在或已结束，请重新开始共享"
        case 413: return "资料超过 Mac 接收限制，请发送较小的样本"
        case 422: return "Mac 无法读取这份资料，请重新采集"
        case 503: return "Mac 调试服务尚未就绪，请检查 Mac 页面"
        case 507: return "Mac 存储空间不足，请清理调试资料后重试"
        default: return "Mac 未接收请求，请检查服务与连接"
        }
    }
}

/// Admission happens before dispatch, so there can be only one retained screen buffer.
final class DeviceDebugFrameGate: @unchecked Sendable {
    private let lock = NSLock()
    private var busy = false
    private var active = true
    private var last: TimeInterval = -.infinity
    func admit(at time: TimeInterval) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard active, !busy, time.isFinite, time - last >= 1 else { return false }
        busy = true; last = time
        return true
    }
    func finish() { lock.lock(); busy = false; lock.unlock() }
    func cancel() { lock.lock(); active = false; lock.unlock() }
    var isActive: Bool { lock.lock(); defer { lock.unlock() }; return active }
}

private final class DeviceDebugScreenPipe: @unchecked Sendable {
    let gate = DeviceDebugFrameGate()
    private let queue = DispatchQueue(label: "vqasee.debug.screen", qos: .utility)
    private let context = CIContext(options: [.cacheIntermediates: false])
    private let session: URLSession
    private let url: URL
    private let token: String
    private let completion: @Sendable (Result<Int, Error>) -> Void
    init(session: URLSession, url: URL, token: String,
         completion: @escaping @Sendable (Result<Int, Error>) -> Void) {
        self.session = session; self.url = url; self.token = token; self.completion = completion
    }
    func consume(_ sample: CMSampleBuffer) {
        guard gate.admit(at: ProcessInfo.processInfo.systemUptime) else { return }
        let capturedAt = Date().timeIntervalSince1970
        queue.async { [self] in
            guard gate.isActive else { gate.finish(); return }
            autoreleasepool {
                guard let pixel = CMSampleBufferGetImageBuffer(sample) else {
                    fail("无法读取屏幕画面"); return
                }
                var image = CIImage(cvPixelBuffer: pixel)
                if let value = CMGetAttachment(sample, key: RPVideoSampleOrientationKey as CFString, attachmentModeOut: nil) as? NSNumber,
                   let orientation = CGImagePropertyOrientation(rawValue: value.uint32Value) {
                    image = image.oriented(orientation)
                }
                let factor = min(1, 1280 / max(image.extent.width, image.extent.height))
                image = image.transformed(by: CGAffineTransform(scaleX: factor, y: factor))
                guard let cgImage = context.createCGImage(image, from: image.extent),
                      let jpeg = UIImage(cgImage: cgImage).jpegData(compressionQuality: 0.7),
                      jpeg.count <= 2 * 1024 * 1024 else {
                    fail("屏幕画面编码失败或过大"); return
                }
                guard gate.isActive else { gate.finish(); return }
                var request = URLRequest(url: url)
                request.httpMethod = "POST"
                request.setValue(token, forHTTPHeaderField: "X-Device-Debug-Token")
                request.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
                request.setValue(String(capturedAt), forHTTPHeaderField: "X-Capture-Timestamp")
                session.uploadTask(with: request, from: jpeg) { [self] _, response, error in
                    guard gate.isActive else { gate.finish(); return }
                    if let error { gate.finish(); completion(.failure(error)) }
                    else if let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) {
                        gate.finish()
                        completion(.success(jpeg.count))
                    } else { fail("Mac 未接收画面，请检查配对与连接") }
                }.resume()
            }
        }
    }
    private func fail(_ text: String) {
        gate.finish()
        guard gate.isActive else { return }
        completion(.failure(NSError(domain: "DeviceDebug", code: 1, userInfo: [NSLocalizedDescriptionKey: text])))
    }
}

@MainActor
final class DeviceDebugController: ObservableObject {
    enum State: Equatable { case idle, connecting, sharing, stopping, stopped, failed }
    @Published private(set) var state: State = .idle
    @Published private(set) var status = "尚未共享屏幕"
    @Published private(set) var uploadedFrames = 0
    @Published private(set) var eventFailures = 0
    @Published private(set) var attachmentStatus = ""
    @Published private(set) var attachmentFailures = 0
    @Published private(set) var sessionID: String?
    @Published private(set) var receivingMacName = ""
    private(set) var receivingMacURL = ""
    @Published private(set) var lastUploadAt: Date?
    @Published private(set) var uploadingAttachment = false
    @Published var shareOriginalSamples = false
    private var network: URLSession?
    private var pipe: DeviceDebugScreenPipe?
    private var endpoint: URL?
    private var token = ""
    private var generation = UUID()
    private struct PendingEvent { let kind: String; let details: [String: String]; let timestamp: TimeInterval }
    private var pendingEvents: [PendingEvent] = []
    private var eventTask: Task<Void, Never>?
    private var eventInFlight = false
    private var backgroundObserver: NSObjectProtocol?
    private let redirectDelegate = DeviceDebugNoRedirect()
    var isActive: Bool { state == .sharing || state == .connecting }
    var isLayoutPreview: Bool { ProcessInfo.processInfo.arguments.contains("-device-debug-layout-preview") }

    init() {
        if ProcessInfo.processInfo.arguments.contains("-device-debug-layout-preview") {
            status = "界面示例：尚未连接 Mac"
        }
        backgroundObserver = NotificationCenter.default.addObserver(forName: UIApplication.didEnterBackgroundNotification,
                                                                    object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in await self?.stop(reason: "进入后台，已停止共享") }
        }
    }
    deinit { if let backgroundObserver { NotificationCenter.default.removeObserver(backgroundObserver) } }

    func start(baseURL: String, pairingToken: String, previousSessionID: String? = nil, macName: String? = nil) async {
        guard !isLayoutPreview else { status = "UI 布局示例 · 未联网、未录屏"; return }
        guard !isActive, state != .stopping else { return }
        guard let url = DeviceDebugPolicy.baseURL(baseURL), pairingToken.count >= 16 else {
            state = .failed; status = "请先选择 Mac 并允许连接"; return
        }
        guard previousSessionID == nil || UUID(uuidString: previousSessionID!) != nil else {
            state = .failed; status = "复测关联无效，请从 Mac 重新复制配对信息"; return
        }
        let recorder = RPScreenRecorder.shared()
        guard recorder.isAvailable, !recorder.isRecording else {
            state = .failed; status = "屏幕录制不可用，请结束其他录制后重试"; return
        }
        generation = UUID(); let current = generation
        state = .connecting; status = "正在连接 Mac"
        uploadedFrames = 0; eventFailures = 0; attachmentFailures = 0; lastUploadAt = nil; attachmentStatus = ""
        pendingEvents.removeAll(); eventTask?.cancel(); eventTask = nil; eventInFlight = false
        endpoint = url; token = pairingToken; sessionID = nil
        receivingMacName = macName ?? url.host ?? "Mac"; receivingMacURL = baseURL
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 8
        configuration.timeoutIntervalForResource = 15
        configuration.httpMaximumConnectionsPerHost = 2
        configuration.urlCache = nil
        network = URLSession(configuration: configuration, delegate: redirectDelegate, delegateQueue: nil)
        do {
            var sessionInput = ["label": "VQASee App 屏幕调试"]
            if let previousSessionID { sessionInput["previous_session_id"] = previousSessionID }
            let data = try await send(path: "sessions", json: sessionInput)
            guard generation == current else { return }
            guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let id = object["id"] as? String, UUID(uuidString: id) != nil, let network else {
                throw failure("Mac 返回了无效会话")
            }
            sessionID = id
            let pipe = DeviceDebugScreenPipe(session: network, url: url.appendingPathComponent("device-debug/sessions/\(id)/screen"), token: token) { [weak self] result in
                Task { @MainActor in
                    guard let self, self.generation == current else { return }
                    switch result {
                    case .success:
                        self.uploadedFrames += 1; self.lastUploadAt = Date()
                        self.status = "正在共享 App 屏幕"
                    case .failure:
                        await self.stop(reason: "画面上传失败，请检查 Mac 连接后重新开始", failed: true)
                    }
                }
            }
            self.pipe = pipe
            recorder.isMicrophoneEnabled = false
            recorder.isCameraEnabled = false
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                recorder.startCapture(handler: { [weak self] sample, type, error in
                    if error != nil {
                        Task { @MainActor in
                            guard let self, self.generation == current else { return }
                            await self.stop(reason: "屏幕采集中断，请重新开始", failed: true)
                        }
                    } else if type == .video { pipe.consume(sample) }
                    // App audio and microphone samples are never retained or uploaded.
                }, completionHandler: { error in
                    if let error { continuation.resume(throwing: error) }
                    else { continuation.resume() }
                })
            }
            guard generation == current else {
                if recorder.isRecording { recorder.stopCapture(handler: nil) }
                return
            }
            state = .sharing; status = "已开始采集，等待首张画面送达"
            recordEvent("screen_share_started", details: ["sampling": "1 Hz maximum", "audio": "disabled", "scope": "app screen only", "original_samples_authorized": String(shareOriginalSamples)])
        } catch {
            guard generation == current else { return }
            await stop(reason: "未能开始共享，请检查配对码、网络与录屏许可", failed: true)
        }
    }

    func stop(reason: String = "已停止共享", failed: Bool = false) async {
        guard isActive || state == .stopping else { return }
        guard state != .stopping else { return }
        state = .stopping; generation = UUID()
        pipe?.gate.cancel(); pipe = nil
        let recorder = RPScreenRecorder.shared()
        if recorder.isRecording {
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                recorder.stopCapture { _ in continuation.resume() }
            }
        }
        // Recording stops immediately; give already queued semantic actions a bounded drain.
        let deadline = ProcessInfo.processInfo.systemUptime + 2
        while eventTask != nil && ProcessInfo.processInfo.systemUptime < deadline {
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
        eventFailures += pendingEvents.count
        pendingEvents.removeAll()
        let drainingEvents = eventTask
        drainingEvents?.cancel()
        if let network {
            let tasks = await network.allTasks
            tasks.forEach { $0.cancel() }
        }
        await drainingEvents?.value
        eventTask = nil
        var stopAcknowledged = true
        if let sessionID {
            do {
                _ = try await send(path: "sessions/\(sessionID)/events", json: [
                    "kind": "debug_delivery_summary", "client_timestamp": Date().timeIntervalSince1970,
                    "details": ["screen_frames_delivered": uploadedFrames,
                                "events_not_delivered": eventFailures, "attachment_failures": attachmentFailures]])
            } catch { eventFailures += 1 }
            do { _ = try await send(path: "sessions/\(sessionID)/stop", json: ["reason": reason]) }
            catch { stopAcknowledged = false }
        }
        network?.invalidateAndCancel(); network = nil; token = ""
        uploadingAttachment = false; eventInFlight = false
        state = failed ? .failed : .stopped
        status = reason + (stopAcknowledged ? "" : "；Mac 未确认停止，手机已停止发送")
    }

    /// Semantic app actions only. Callers must exclude tokens, names, recognized text and device IDs.
    func recordEvent(_ kind: String, details: [String: String] = [:]) {
        guard state == .sharing, let sessionID else { return }
        let detailBytes = details.reduce(0) { $0 + $1.key.utf8.count + $1.value.utf8.count }
        guard pendingEvents.count < 64, kind.count <= 80, !kind.isEmpty, detailBytes <= 8192 else {
            eventFailures += 1; return
        }
        pendingEvents.append(PendingEvent(kind: kind, details: details, timestamp: Date().timeIntervalSince1970))
        guard eventTask == nil else { return }
        eventTask = Task { [weak self] in
            guard let self else { return }
            while !self.pendingEvents.isEmpty && !Task.isCancelled {
                let event = self.pendingEvents.removeFirst()
                self.eventInFlight = true
                do { _ = try await self.send(path: "sessions/\(sessionID)/events", json: ["kind": event.kind, "details": event.details, "client_timestamp": event.timestamp]) }
                catch { self.eventFailures += 1 }
                self.eventInFlight = false
            }
            self.eventTask = nil
        }
    }

    /// Explicit user-selected original sample, never silently substituted for an App screen.
    @discardableResult
    func uploadAttachment(data: Data, filename: String) async -> Bool {
        guard shareOriginalSamples else { attachmentStatus = "原始样本共享未开启"; return false }
        guard state == .sharing, let sessionID else {
            reportAttachmentFailure(reason: "sharing_unavailable"); return false
        }
        guard !uploadingAttachment else {
            reportAttachmentFailure(reason: "attachment_busy"); return false
        }
        guard DeviceDebugPolicy.validFilename(filename), !data.isEmpty, data.count <= 32 * 1024 * 1024 else {
            reportAttachmentFailure(reason: "invalid_attachment")
            attachmentStatus = "样本名称无效或超过 32 MB"; return false
        }
        let current = generation
        uploadingAttachment = true; attachmentStatus = "正在发送原始样本"
        do {
            let response = try await send(path: "sessions/\(sessionID)/attachments", body: data,
                               contentType: "application/octet-stream", headers: ["X-Filename": filename])
            if generation == current {
                attachmentStatus = "原始样本已发送到 Mac"
                let object = (try? JSONSerialization.jsonObject(with: response)) as? [String: Any]
                recordEvent("sample_attachment_uploaded", details: ["filename": filename,
                    "evidence_id": object?["id"] as? String ?? "unavailable",
                    "bytes": String(data.count), "manifest": String(filename.hasSuffix("-manifest.json"))])
                uploadingAttachment = false
                return true
            }
        } catch {
            if generation == current {
                reportAttachmentFailure(reason: "upload_failed")
                attachmentStatus = (error as NSError).domain == "DeviceDebug" ? error.localizedDescription : "样本上传中断，请检查连接后补传"
            }
            else { attachmentFailures += 1; attachmentStatus = "共享停止时样本未送达，本地样本仍保留" }
        }
        if generation == current { uploadingAttachment = false }
        return false
    }

    func reportAttachmentFailure(count: Int = 1, reason: String) {
        guard count > 0 else { return }
        attachmentFailures += count
        attachmentStatus = "部分样本未送达 Mac，本地样本仍保留"
        recordEvent("sample_export_incomplete", details: ["count": String(count), "reason": reason])
    }

    private func failure(_ text: String) -> NSError {
        NSError(domain: "DeviceDebug", code: 1, userInfo: [NSLocalizedDescriptionKey: text])
    }
    private func send(path: String, json: [String: Any]) async throws -> Data {
        try await send(path: path, body: JSONSerialization.data(withJSONObject: json), contentType: "application/json")
    }
    private func send(path: String, body: Data, contentType: String, headers: [String: String] = [:]) async throws -> Data {
        guard let endpoint, let network else { throw failure("尚未配对") }
        var request = URLRequest(url: endpoint.appendingPathComponent("device-debug/\(path)"))
        request.httpMethod = "POST"; request.httpBody = body
        request.setValue(token, forHTTPHeaderField: "X-Device-Debug-Token")
        request.setValue(contentType, forHTTPHeaderField: "Content-Type")
        for (key, value) in headers { request.setValue(value, forHTTPHeaderField: key) }
        let (data, response) = try await network.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw failure("Mac 未接收请求") }
        guard (200..<300).contains(response.statusCode) else { throw failure(DeviceDebugPolicy.serverError(response.statusCode)) }
        return data
    }
}
