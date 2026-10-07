import ARKit
import AVFoundation
import Combine
import CoreImage
import SwiftUI

@MainActor
final class IndoorMemoryController: NSObject, ObservableObject, ARSessionDelegate {
    enum State: Equatable {
        case idle, requestingPermission, scanning, ready, paused(String), denied, unsupported, failed
    }
    struct Draft: Identifiable {
        var id: UUID
        let recordedAt: Date
        let sessionID: UUID
        let photo: Data
        var suggestedName = "未命名物品"
        var target: IndoorTargetEvidence? = nil
        var hasSpatialAnchor = false
        var spatialSource: String? = nil
        var targetSelectionStatus = "点照片中的物品，提取轮廓；也可先保存照片"
        var normalizedTargetRect: CGRect? { target?.normalizedBounds }
    }
    struct Marker {
        let point: CGPoint
        let distance: Float
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var records: [IndoorMemoryRecord] = []
    @Published private(set) var selectedID: UUID?
    @Published private(set) var marker: Marker?
    @Published private(set) var guidanceArrow: String?
    @Published private(set) var guidance = "对准物品，记录照片后点选目标"
    @Published private(set) var canRecord = false
    @Published private(set) var recordHint = "相机画面就绪后可以记录照片"
    var visibleRect: CGRect = .zero
    @Published private(set) var draft: Draft?
    @Published var error: String?
    enum CheckStatus { case idle, checking, present, absent, uncertain }
    @Published private(set) var checkStatus: CheckStatus = .idle
    @Published private(set) var detail = "尚未检查"
    @Published private(set) var checkedAt: Date?
    @Published private(set) var comparisonPhoto: Data?
    @Published private(set) var comparisonVisualization: IndoorComparisonVisualizationResult?
    @Published private(set) var liveObservation: IndoorLiveObservation = .unavailable
    @Published private(set) var liveAppearanceMessage = "等待看回原位置"
    private(set) var liveAppearanceEvidence: [String: String] = [:]
    private var lastAppearanceResultAt: TimeInterval = -.infinity
    private var liveVisualBusy = false
    private var lastVisualTimestamp: TimeInterval = -.infinity
    private(set) var liveObservationEvidence: [String: String] = [:]
    private var depthObservation = IndoorDepthObservationFilter()
    private var observationRecordID: UUID?
    private var lastObservationFrame: TimeInterval = -.infinity
    @Published private(set) var isSelectingTarget = false
    @Published private(set) var persistenceMessage = "保存后仅留在本机，不上传"
    private let store: IndoorMemoryStore
    private var lastTargetSource: String?
    private var loaded = false
    private var storageLoadFailed = false
    private var savedMap: ARWorldMap?
    private var restoringSince: TimeInterval?
    private var draftFrame: ARFrame?
    private var draftOrientation: UIInterfaceOrientation = .portrait
    private var selectionGeneration = UUID()
    private var checkGeneration = UUID()
    private var persistenceTask: Task<Void, Never>?
    private let visionQueue = DispatchQueue(label: "vqasee.indoor.vision", qos: .userInitiated)
    var canCheckSelectedObject: Bool { selected != nil && active && !isSelectingTarget && checkStatus != .checking && state == .ready }
    let session = ARSession()
    let capture: IndoorCaptureRecorder = {
#if DEBUG
        if ProcessInfo.processInfo.arguments.contains("-indoor-capture-preview") {
            return IndoorCaptureRecorder(store: IndoorCaptureStore(root: FileManager.default.temporaryDirectory.appendingPathComponent("capture-ui-" + UUID().uuidString)))
        }
#endif
        return IndoorCaptureRecorder()
    }()
    private var capturePreviewSeeded = false
    func prepareCapturePreview() async {
#if DEBUG
        guard ProcessInfo.processInfo.arguments.contains("-indoor-capture-preview"), !capturePreviewSeeded else { return }
        capturePreviewSeeded = true
        let id = UUID(), time = ProcessInfo.processInfo.systemUptime, photo = Self.layoutPhoto()
        do {
            try await capture.store.begin(IndoorCaptureManifest(id: id, arSessionID: UUID(), objectName: "界面合成样本",
                objectInstanceID: UUID(), event: .removed, startedAt: Date(), startedUptime: time))
            let k: [Float] = [500, 0, 0, 0, 500, 0, 300, 225, 1]
            for index in 0..<2 {
                let frame = IndoorCaptureFrame(id: index, timestamp: time + Double(index), tracking: "unavailable:synthetic-fixture",
                    sensorWidth: 600, sensorHeight: 450, imageWidth: 600, imageHeight: 450,
                    interfaceOrientation: UIInterfaceOrientation.landscapeLeft.rawValue,
                    cameraToWorld: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], intrinsics: k, encodedIntrinsics: k,
                    imageSHA256: IndoorCaptureStore.hash(photo), depthUnavailableReason: "UI合成样本，无真实深度")
                _ = try await capture.store.append(IndoorCapturePacket(metadata: frame, jpeg: photo), id: id)
            }
            try await capture.store.finish(id: id, reason: "界面示例，非真实采集", busy: 0, missing: 0, duplicates: 0, failures: 0)
        } catch { capture.error = error.localizedDescription }
#endif
    }
    enum CaptureAvailability: Equatable {
        case ready, layoutPreview, unsupported, permissionRequired, paused, restoring, waitingForFrame
        var message: String {
            switch self {
            case .ready: return "后置相机已就绪，可以开始采样。"
            case .layoutPreview: return "当前是界面示例，不能采集真实画面。请从手机主屏幕重新打开 App。"
            case .unsupported: return "此设备不支持空间采集；已有样本仍可回放。"
            case .permissionRequired: return "请在系统设置中允许 VQASee 使用相机。"
            case .paused: return "相机已暂停，请恢复相机后开始。"
            case .restoring: return "正在找回原来的空间位置，请对准记录时的环境；最长等待15秒。"
            case .waitingForFrame: return "尚未收到新的相机画面，请稍等；持续无画面可重启相机。"
            }
        }
    }
    var captureAvailability: CaptureAvailability {
        if isLayoutPreview { return .layoutPreview }
        if state == .unsupported { return .unsupported }
        if state == .denied { return .permissionRequired }
        if !active { return .paused }
        if restoringSince != nil { return .restoring }
        guard let frame = session.currentFrame else { return .waitingForFrame }
        let age = ProcessInfo.processInfo.systemUptime - frame.timestamp
        guard age >= 0, age <= IndoorMemoryPolicy.maximumFrameAge else { return .waitingForFrame }
        return .ready
    }
    var canStartCapture: Bool { captureAvailability == .ready }
    func beginCapture(event: IndoorCaptureEvent, name: String) async {
        guard canStartCapture else { capture.error = captureAvailability.message; return }
        await capture.begin(event: event, name: name, arSessionID: sessionID, objectID: selected?.id)
    }
    weak var view: ARSCNView?
    private var sessionID = UUID()
    private var active = false
    private var permissionGeneration = UUID()
    private var timer: Timer?
    private let imageContext = CIContext()
    private var readySince: TimeInterval?
    private var refreshDurations: [Double] = []
    private var frameAges: [Double] = []
    private var refreshCount = 0
    private var unavailableCount = 0

    var diagnosticSummary: String {
        func p95(_ samples: [Double]) -> String {
            guard !samples.isEmpty else { return "尚无数据" }
            let sorted = samples.sorted()
            return String(format: "%.1f ms", sorted[min(sorted.count - 1, Int(ceil(Double(sorted.count) * 0.95)) - 1)])
        }
        return "刷新次数：\(refreshCount)\n定位不可用：\(unavailableCount)\n最近 500 次刷新 p95：\(p95(refreshDurations))\n最近 500 帧年龄 p95：\(p95(frameAges))\n\n刷新耗时不含屏幕渲染，不能替代端到端延迟。真机位置误差需要对照桌面实物测量。"
    }

    var selected: IndoorMemoryRecord? { records.first { $0.id == selectedID } }
    var status: String {
        switch state {
        case .idle: return "准备记录"
        case .requestingPermission: return "等待相机权限"
        case .scanning: return "正在认识周围环境"
        case .ready: return "空间定位可用"
        case .paused(let reason): return reason
        case .denied: return "需要相机权限"
        case .unsupported: return "此设备无法进行空间定位"
        case .failed: return "空间定位已停止"
        }
    }

    override init() {
        store = IndoorMemoryStore()
        super.init()
        session.delegate = self
        session.delegateQueue = .main
    }

    init(store: IndoorMemoryStore) {
        self.store = store
        super.init()
        session.delegate = self
        session.delegateQueue = .main
    }

    var isLayoutPreview: Bool {
#if DEBUG
        ProcessInfo.processInfo.arguments.contains("-indoor-layout-preview")
#else
        false
#endif
    }

    func start() async {
#if DEBUG
        if isLayoutPreview {
            loadLayoutPreview()
            // UI regression input: reproduce live AR's 20 Hz published changes,
            // without claiming the simulator has camera or tracking evidence.
            if ProcessInfo.processInfo.arguments.contains("-indoor-ui-refresh-stress") {
                timer?.invalidate()
                let refreshTimer = Timer(timeInterval: 0.05, repeats: true) { [weak self] _ in
                    MainActor.assumeIsolated { self?.refreshLayoutPreview() }
                }
                timer = refreshTimer
                RunLoop.main.add(refreshTimer, forMode: .common)
            }
            return
        }
#endif
        guard !active else { return }
        await loadRecords()
        guard ARWorldTrackingConfiguration.isSupported else {
            state = .unsupported
            guidance = "请在支持 ARKit 的 iPhone 上试用"
            return
        }
        let generation = UUID()
        permissionGeneration = generation
        state = .requestingPermission
        let allowed: Bool
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: allowed = true
        case .notDetermined: allowed = await AVCaptureDevice.requestAccess(for: .video)
        default: allowed = false
        }
        guard permissionGeneration == generation else { return }
        guard allowed else {
            state = .denied
            guidance = "在系统设置中允许相机访问后重试"
            return
        }
        sessionID = UUID()
        active = true
        state = .scanning
        guidance = records.isEmpty ? "对准物品，记录照片后点选目标" : "照片已保留，正在检查空间位置"
        let configuration = Self.makeIndoorConfiguration()
        if let savedMap {
            configuration.initialWorldMap = savedMap
            restoringSince = ProcessInfo.processInfo.systemUptime
            guidance = "对准记录时的环境，正在找回空间位置"
        }
        session.run(configuration, options: [.resetTracking, .removeExistingAnchors])
        timer?.invalidate()
        let refreshTimer = Timer(timeInterval: 0.05, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refresh() }
        }
        timer = refreshTimer
        RunLoop.main.add(refreshTimer, forMode: .common)
    }

    /// Never reuses a coordinate system across backgrounding, mode switches or interruption.
    func stop() {
        liveAppearanceEvidence = [:]; liveAppearanceMessage = "相机已暂停"
        liveObservation = .unavailable; liveObservationEvidence = [:]
        guidanceArrow = nil; depthObservation.reset()
        capture.cameraStopped()
        permissionGeneration = UUID()
        active = false
        timer?.invalidate()
        timer = nil
        session.pause()
        sessionID = UUID()
        draft = nil
        marker = nil
        canRecord = false
        readySince = nil
        restoringSince = nil
        selectionGeneration = UUID()
        draftFrame = nil
        isSelectingTarget = false
        resetCheck()
        state = .paused("空间定位暂停")
        guidance = "恢复后重新扫描；旧记录仍可查看照片"
    }

    func clear() {
        storageLoadFailed = false // Explicit user reset is the only overwrite after a load error.
        records.removeAll()
        selectedID = nil
        stop()
        savedMap = nil
        persist()
    }

    private var orientation: UIInterfaceOrientation {
        view?.window?.windowScene?.effectiveGeometry.interfaceOrientation ?? .portrait
    }

    static func makeIndoorConfiguration() -> ARWorldTrackingConfiguration {
        let configuration = ARWorldTrackingConfiguration()
        configuration.userFaceTrackingEnabled = false
        configuration.planeDetection = [.horizontal, .vertical]
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
            configuration.frameSemantics.insert(.sceneDepth)
        }
        return configuration
    }

    func restartCaptureCamera() async {
        guard capture.state == .idle else { return }
        stop()
        await start()
    }

    private func handleRestorationTimeout(now: TimeInterval) -> Bool {
        guard active, IndoorMemoryPolicy.restorationExpired(startedAt: restoringSince, now: now) else { return false }
        capture.cameraStopped()
        restoringSince = nil
        savedMap = nil
        sessionID = UUID()
        readySince = nil
        state = .scanning
        session.run(Self.makeIndoorConfiguration(), options: [.resetTracking, .removeExistingAnchors])
        guidance = "未找回空间位置，照片仍保留；可重新记录"
        error = "本次未找回旧空间，已开始新的扫描。旧记录可继续查看照片。"
        return true
    }

    private func refresh() {
        // Never retain a live judgment across tracking failure, stale frames or record switches.
        liveObservation = .unavailable
        liveObservationEvidence = [:]
        guidanceArrow = nil
        defer {
            if liveObservation == .unavailable || liveObservation == .outsideView || liveObservation == .photoOnly {
                depthObservation.reset(); liveAppearanceEvidence = [:]; liveAppearanceMessage = "等待看回原位置"
            }
        }
        if ProcessInfo.processInfo.systemUptime-lastAppearanceResultAt > 1 {
            liveAppearanceMessage = "正在检查原位置的外观…"; liveAppearanceEvidence = [:]
        }
        if liveVisualBusy, ProcessInfo.processInfo.systemUptime-lastVisualTimestamp > 2 {
            liveAppearanceMessage = "外观检查耗时过长，暂时无法确认"
            liveAppearanceEvidence = ["failure": "vision_worker_over_budget", "elapsed_ms": String((ProcessInfo.processInfo.systemUptime-lastVisualTimestamp)*1000)]
        }
        // Timeout is wall-clock policy: absence of a frame must not prevent recovery.
        if handleRestorationTimeout(now: ProcessInfo.processInfo.systemUptime) {
            marker = nil
            canRecord = false
            return
        }
        capture.offer(session.currentFrame, orientation: orientation, arSessionID: sessionID)
#if DEBUG
        if isLayoutPreview { refreshLayoutPreview(); return }
#endif
        let refreshStarted = ProcessInfo.processInfo.systemUptime
        defer {
            refreshCount += 1
            if state != .ready { unavailableCount += 1 }
            refreshDurations.append((ProcessInfo.processInfo.systemUptime - refreshStarted) * 1000)
            if refreshDurations.count > 500 { refreshDurations.removeFirst() }
        }
        marker = nil
        canRecord = false
        recordHint = "相机画面稳定后可以记录照片"
        guard active, let view, let frame = session.currentFrame else { return }
        let age = ProcessInfo.processInfo.systemUptime - frame.timestamp
        if age.isFinite && age >= 0 {
            frameAges.append(age * 1000)
            if frameAges.count > 500 { frameAges.removeFirst() }
        }
        guard age >= 0, age <= IndoorMemoryPolicy.maximumFrameAge else {
            state = .paused("画面更新暂停")
            guidance = "请重新扫描后再记录"
            readySince = nil
            return
        }
        canRecord = records.count < IndoorMemoryPolicy.capacity && draft == nil
        recordHint = canRecord ? "可先记录照片；点选物品后尝试补全空间位置" : "已满十条或正在编辑记录"
        guard case .normal = frame.camera.trackingState else {
            readySince = nil
            switch frame.camera.trackingState {
            case .limited(.excessiveMotion): guidance = "移动太快，请放慢手机"
            case .limited(.insufficientFeatures): guidance = "请对准有纹理、光线充足的环境"
            case .limited(.relocalizing): guidance = "定位暂停，请对准刚才的环境"
            default: guidance = "缓慢移动手机，观察物品周围"
            }
            state = .paused("空间定位尚未稳定")
            return
        }
        // Require a continuous normal interval after any tracking degradation.
        if readySince == nil { readySince = frame.timestamp }
        guard frame.timestamp - (readySince ?? frame.timestamp) >= 0.5 else {
            state = .scanning
            guidance = "请稍等，正在确认位置"
            return
        }
        state = .ready
        if restoringSince != nil {
            let restoredIDs = Set(frame.anchors.map(\.identifier))
            if records.contains(where: { $0.hasSpatialAnchor && restoredIDs.contains($0.id) }) {
                // Restored map anchors and sustained normal tracking are both required.
                records = records.map { record in
                    IndoorMemoryRecord(id: record.id, name: record.name, recordedAt: record.recordedAt,
                        sessionID: restoredIDs.contains(record.id) ? sessionID : record.sessionID,
                        photo: record.photo, target: record.target, hasSpatialAnchor: record.hasSpatialAnchor,
                        spatialSource: record.spatialSource, lastObservation: record.lastObservation)
                }
                restoringSince = nil
            } else { guidance = "正在找回记录位置；请观察原来的环境"; return }
        }
        guidance = recordHint
        guard let selected else { return }
        guard selected.hasSpatialAnchor else {
            liveObservation = .photoOnly; depthObservation.reset()
            guidance = "只记住了照片；请重新记录并点选物品以尝试取得位置"
            return
        }
        if observationRecordID != selected.id {
            observationRecordID = selected.id; depthObservation.reset(); lastObservationFrame = -.infinity
            liveAppearanceEvidence = [:]; lastAppearanceResultAt = -.infinity; lastVisualTimestamp = -.infinity
        }
        guard IndoorMemoryPolicy.canProject(recordSession: selected.sessionID, currentSession: sessionID,
                                             trackingNormal: true, frameAge: age),
              let anchor = frame.anchors.first(where: { $0.identifier == selected.id }) else {
            guidance = "这条记录的空间位置尚未取得或恢复，可查看照片"
            return
        }
        let world = anchor.transform.columns.3
        let cameraPoint = simd_inverse(frame.camera.transform) * world
        let position = SIMD3<Float>(world.x, world.y, world.z)
        let projected = frame.camera.projectPoint(position, orientation: orientation, viewportSize: view.bounds.size)
        if cameraPoint.z < 0, projected.x.isFinite, projected.y.isFinite,
           visibleRect.insetBy(dx: 24, dy: 24).contains(projected) {
            marker = Marker(point: projected, distance: simd_length(SIMD3(cameraPoint.x, cameraPoint.y, cameraPoint.z)))
            updateLiveObservation(record: selected, frame: frame, cameraPoint: cameraPoint)
            offerLiveAppearance(record: selected, frame: frame, position: position)
            guidance = selected.spatialSource == "plane" ? "平面参考位置，不保证是物品表面；请对照照片" : "标记是记录时的位置，未确认物品现在是否还在"
        } else {
            depthObservation.reset(); liveObservation = .outsideView
            let dx = projected.x-visibleRect.midX, dy = projected.y-visibleRect.midY
            guidanceArrow = cameraPoint.z >= 0 ? "arrow.uturn.backward" : abs(dx) > abs(dy) ? (dx > 0 ? "arrow.right" : "arrow.left") : (dy < 0 ? "arrow.up" : "arrow.down")
            guidance = IndoorMemoryPolicy.turnHint(isBehind: cameraPoint.z >= 0, projected: projected, visibleRect: visibleRect)
        }
    }

    private func offerLiveAppearance(record: IndoorMemoryRecord, frame: ARFrame, position: SIMD3<Float>) {
        guard let reference = record.target else { liveAppearanceMessage = "未保存目标轮廓，请重新记录并点选物品"; return }
        guard !liveVisualBusy, frame.timestamp-lastVisualTimestamp >= 1,
              let photo = encodePhoto(frame), let image = UIImage(data: photo)?.cgImage else { return }
        let pointInPhoto = frame.camera.projectPoint(position, orientation: orientation, viewportSize: CGSize(width: image.width, height: image.height))
        let point = CGPoint(x: pointInPhoto.x/CGFloat(image.width), y: pointInPhoto.y/CGFloat(image.height))
        guard point.x.isFinite, point.y.isFinite, (0..<1).contains(point.x), (0..<1).contains(point.y) else { return }
        liveVisualBusy = true; lastVisualTimestamp = frame.timestamp
        let recordID = record.id, generation = sessionID, capturedAt = frame.timestamp
        Task { [weak self] in
            guard let self else { return }
            defer { self.liveVisualBusy = false }
            do {
                let result: (IndoorTargetEvidence, Float?) = try await withCheckedThrowingContinuation { continuation in
                    self.visionQueue.async {
                        do {
                            let vision = IndoorTargetVision()
                            let current = try vision.extract(image: image, normalizedTap: point)
                            let compared = try vision.compare(reference: reference, current: current, context: IndoorTargetComparisonContext())
                            continuation.resume(returning: (current, compared.featureDistance))
                        } catch { continuation.resume(throwing: error) }
                    }
                }
                guard self.liveAppearanceResultIsCurrent(recordID: recordID, generation: generation, capturedAt: capturedAt) else { return }
                self.lastAppearanceResultAt = capturedAt
                self.liveAppearanceEvidence = ["processing_ms": String((ProcessInfo.processInfo.systemUptime-capturedAt)*1000)]
                self.liveAppearanceMessage = "原位置提取到前景轮廓，是否原物品尚未确认"
                if let distance = result.1 { self.liveAppearanceEvidence["appearance_distance_uncalibrated"] = String(distance) }
                self.liveAppearanceEvidence["appearance_frame_timestamp"] = String(capturedAt)
            } catch {
                guard self.liveAppearanceResultIsCurrent(recordID: recordID, generation: generation, capturedAt: capturedAt) else { return }
                self.lastAppearanceResultAt = capturedAt
                self.liveAppearanceEvidence = [:]
                self.liveAppearanceMessage = "未取得可比较轮廓；不能据此判断物品消失"
                self.liveAppearanceEvidence["appearance_error"] = error.localizedDescription
            }
        }
    }

    private func liveAppearanceResultIsCurrent(recordID: UUID, generation: UUID, capturedAt: TimeInterval) -> Bool {
        let now = ProcessInfo.processInfo.systemUptime
        guard active, selectedID == recordID, sessionID == generation, marker != nil,
              now-capturedAt >= 0, now-capturedAt <= 1, let latest = session.currentFrame,
              now-latest.timestamp >= 0, now-latest.timestamp <= IndoorMemoryPolicy.maximumFrameAge,
              case .normal = latest.camera.trackingState else { return false }
        return true
    }

    private func updateLiveObservation(record: IndoorMemoryRecord, frame: ARFrame, cameraPoint: SIMD4<Float>) {
        let started = ProcessInfo.processInfo.systemUptime
        defer { liveObservationEvidence["processing_ms"] = String(format: "%.3f", (ProcessInfo.processInfo.systemUptime-started)*1000) }
        liveObservationEvidence = ["source": record.spatialSource ?? "none", "frame_timestamp": String(frame.timestamp), "scope": "local_surface_not_object_identity"]
        guard record.spatialSource == "depth" else {
            depthObservation.reset(); liveObservation = .referenceOnly; return
        }
        guard let depth = frame.sceneDepth, let confidence = depth.confidenceMap else {
            depthObservation.reset(); liveObservation = .noDepth; return
        }
        let expected = -cameraPoint.z
        let k = frame.camera.intrinsics, resolution = frame.camera.imageResolution
        let sx = (cameraPoint.x / expected * k.columns.0.x + k.columns.2.x) / Float(resolution.width)
        let sy = (-cameraPoint.y / expected * k.columns.1.y + k.columns.2.y) / Float(resolution.height)
        guard sx.isFinite, sy.isFinite, sx > 0, sx < 1, sy > 0, sy < 1 else { depthObservation.reset(); return }
        let map = depth.depthMap
        CVPixelBufferLockBaseAddress(map, .readOnly); CVPixelBufferLockBaseAddress(confidence, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(map, .readOnly); CVPixelBufferUnlockBaseAddress(confidence, .readOnly) }
        guard CVPixelBufferGetWidth(map) == CVPixelBufferGetWidth(confidence), CVPixelBufferGetHeight(map) == CVPixelBufferGetHeight(confidence),
              let base = CVPixelBufferGetBaseAddress(map), let conf = CVPixelBufferGetBaseAddress(confidence) else { depthObservation.reset(); liveObservation = .noDepth; return }
        let width = CVPixelBufferGetWidth(map), height = CVPixelBufferGetHeight(map)
        let px = Int(sx * Float(width)), py = Int(sy * Float(height))
        var samples: [Float] = []
        for dy in -1...1 { for dx in -1...1 {
            let x = px+dx, y = py+dy
            guard x >= 0, x < width, y >= 0, y < height else { continue }
            let quality = conf.advanced(by: y * CVPixelBufferGetBytesPerRow(confidence)).assumingMemoryBound(to: UInt8.self)[x]
            let value = base.advanced(by: y * CVPixelBufferGetBytesPerRow(map)).assumingMemoryBound(to: Float32.self)[x]
            if quality == ARConfidenceLevel.high.rawValue, value.isFinite, value > 0 { samples.append(value) }
        }}
        lastObservationFrame = frame.timestamp
        liveObservationEvidence["expected_depth_m"] = String(expected)
        liveObservationEvidence["valid_samples"] = String(samples.count)
        if !samples.isEmpty { liveObservationEvidence["median_depth_m"] = String(samples.sorted()[samples.count/2]) }
        liveObservation = depthObservation.offer(expected: expected, samples: samples, timestamp: frame.timestamp)
    }

    func beginRecord() {
#if DEBUG
        if isLayoutPreview {
            draft = Draft(id: UUID(), recordedAt: Date(), sessionID: sessionID, photo: Self.layoutPhoto())
            canRecord = false
            return
        }
#endif
        refresh()
        guard canRecord, let frame = session.currentFrame else {
            error = "相机画面尚未就绪，请稍后重试。"
            return
        }
        guard let photo = encodePhoto(frame) else {
            error = "记录照片失败，请重试。"
            return
        }
        let age = max(0, ProcessInfo.processInfo.systemUptime - frame.timestamp)
        draftFrame = frame
        draftOrientation = orientation
        draft = Draft(id: UUID(), recordedAt: Date().addingTimeInterval(-age), sessionID: sessionID,
                      photo: photo, suggestedName: "物品 \(records.count + 1)")
        canRecord = false
    }

    private func encodePhoto(_ frame: ARFrame) -> Data? {
        let imageOrientation: CGImagePropertyOrientation
        switch orientation {
        case .landscapeLeft: imageOrientation = .up
        case .landscapeRight: imageOrientation = .down
        case .portraitUpsideDown: imageOrientation = .left
        default: imageOrientation = .right
        }
        let image = CIImage(cvPixelBuffer: frame.capturedImage).oriented(imageOrientation)
        let scale = min(1, 960 / max(image.extent.width, image.extent.height))
        let resized = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        guard let cgImage = imageContext.createCGImage(resized, from: resized.extent) else { return nil }
        return UIImage(cgImage: cgImage).jpegData(compressionQuality: 0.7)
    }

    func save(name: String) {
        guard !isSelectingTarget, let draft,
              let name = IndoorMemoryPolicy.validName(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? draft.suggestedName : name), records.count < IndoorMemoryPolicy.capacity,
              draft.sessionID == sessionID else { return }
        records.insert(IndoorMemoryRecord(id: draft.id, name: name, recordedAt: draft.recordedAt,
                                          sessionID: draft.sessionID, photo: draft.photo,
                                          target: draft.target, hasSpatialAnchor: draft.hasSpatialAnchor,
                                          spatialSource: draft.spatialSource), at: 0)
        selectedID = draft.id
        self.draft = nil
        draftFrame = nil
        resetCheck()
        persist()
        refresh()
    }

    func cancelDraft() {
        selectionGeneration = UUID()
        isSelectingTarget = false
        draftFrame = nil
        if let draft, let anchor = session.currentFrame?.anchors.first(where: { $0.identifier == draft.id }) {
            session.remove(anchor: anchor)
        }
        draft = nil
    }

    func select(_ record: IndoorMemoryRecord) {
        selectedID = record.id
        marker = nil
        resetCheck()
        refresh()
    }

    func delete(_ record: IndoorMemoryRecord) {
        if let anchor = session.currentFrame?.anchors.first(where: { $0.identifier == record.id }) {
            session.remove(anchor: anchor)
        }
        records.removeAll { $0.id == record.id }
        if selectedID == record.id { selectedID = nil }
        resetCheck()
        savedMap = nil // Never retain a deleted anchor in a future restore.
        persist()
        refresh()
    }

    private func resetCheck() {
        checkGeneration = UUID()
        checkStatus = .idle
        comparisonPhoto = nil
        comparisonVisualization = nil
        checkedAt = nil
        detail = "尚未检查；记录不代表物品仍在原处"
    }

    func loadRecords() async {
        guard !loaded else { return }
        loaded = true
        do {
            guard let snapshot = try await store.load() else { return }
            records = try snapshot.records.map { stored in
                let metadata = try stored.metadata.map { try JSONDecoder().decode(IndoorRecordMetadata.self, from: $0) }
                return IndoorMemoryRecord(id: stored.id, name: stored.name, recordedAt: stored.recordedAt,
                    sessionID: stored.sessionID, photo: stored.photo, target: metadata?.target,
                    hasSpatialAnchor: metadata?.hasSpatialAnchor ?? false, spatialSource: metadata?.spatialSource,
                    lastObservation: metadata?.lastObservation)
            }
            selectedID = records.first?.id
            persistenceMessage = "已读取本机记录 · 不上传"
            if let data = snapshot.worldMapData {
                do {
                    savedMap = try NSKeyedUnarchiver.unarchivedObject(ofClass: ARWorldMap.self, from: data)
                    if savedMap == nil { throw CocoaError(.coderReadCorrupt) }
                } catch {
                    self.error = "空间地图无法读取，照片仍然保留。\(error.localizedDescription)"
                }
            }
        } catch {
            storageLoadFailed = true
            self.error = "读取记录失败，原文件未覆盖。\(error.localizedDescription)"
            persistenceMessage = "本地记录未能读取，请先反馈问题或明确清除记录"
        }
    }

    private func persist() {
        guard !isLayoutPreview else { persistenceMessage = "界面示例，不写入真实记录"; return }
        guard !storageLoadFailed else { error = "原记录未能读取，已停止覆盖。请先反馈问题。"; return }
        let snapshotRecords = records
        let generation = sessionID
        let previous = persistenceTask
        persistenceMessage = "正在保存到本机…"
        persistenceTask = Task { [weak self] in
            await previous?.value
            guard let self else { return }
            var photosSaved = false
            do {
                let stored = try snapshotRecords.map { record in
                    IndoorMemoryStoredRecord(id: record.id, name: record.name, recordedAt: record.recordedAt,
                        sessionID: record.sessionID, photo: record.photo,
                        metadata: try JSONEncoder().encode(IndoorRecordMetadata(target: record.target, hasSpatialAnchor: record.hasSpatialAnchor, spatialSource: record.spatialSource, lastObservation: record.lastObservation)))
                }
                // Commit photos first: map generation must not put the user's record at risk.
                try await self.store.save(IndoorMemorySnapshot(records: stored, worldMapData: nil))
                photosSaved = true
                self.persistenceMessage = "照片已存本机；正在尝试保存空间地图"
                var map = snapshotRecords.isEmpty ? nil : self.savedMap
                if self.active, self.sessionID == generation,
                   let frame = self.session.currentFrame,
                   frame.worldMappingStatus == .mapped {
                    let result = await self.captureWorldMap()
                    if self.sessionID == generation { map = result }
                }
                // Persist only anchors belonging to this saved record set.
                if let map { map.anchors.removeAll { anchor in !snapshotRecords.contains(where: { $0.id == anchor.identifier }) } }
                let mapData = try map.map { try NSKeyedArchiver.archivedData(withRootObject: $0, requiringSecureCoding: true) }
                try await self.store.save(IndoorMemorySnapshot(records: stored, worldMapData: mapData))
                if self.records.map(\.id) == snapshotRecords.map(\.id) { self.savedMap = map }
                self.persistenceMessage = mapData == nil ? "照片已存本机；空间地图未就绪，重开后仅能看照片" : "已存本机；重开后需重新找回空间位置"
            } catch {
                self.persistenceMessage = photosSaved ? "照片已存本机；空间地图保存失败" : "保存失败，当前新记录可能在退出后丢失"
                self.error = "保存失败，请重试。\(error.localizedDescription)"
            }
        }
    }

    private func captureWorldMap() async -> ARWorldMap? {
        await withCheckedContinuation { continuation in
            let request = IndoorMapRequest(continuation)
            session.getCurrentWorldMap { map, _ in
                Task { @MainActor in request.finish(map) }
            }
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(5))
                request.finish(nil)
            }
        }
    }

    func retrySave() { persist() }

    func selectDraftTarget(normalizedPoint: CGPoint) async {
        guard var draft, !isSelectingTarget, let image = UIImage(data: draft.photo)?.cgImage else { return }
        let generation = UUID()
        selectionGeneration = generation
        isSelectingTarget = true
        if draft.hasSpatialAnchor, let anchor = session.currentFrame?.anchors.first(where: { $0.identifier == draft.id }) {
            session.remove(anchor: anchor)
        }
        draft.target = nil
        draft.hasSpatialAnchor = false
        draft.spatialSource = nil
        draft.targetSelectionStatus = "正在提取所点物品的轮廓…"
        self.draft = draft
        do {
            let evidence = try await extractTarget(image: image, point: normalizedPoint)
            guard selectionGeneration == generation, self.draft?.id == draft.id else { return }
            if draft.hasSpatialAnchor, let anchor = session.currentFrame?.anchors.first(where: { $0.identifier == draft.id }) {
                session.remove(anchor: anchor)
            }
            draft.target = evidence
            draft.hasSpatialAnchor = false
            if let frame = draftFrame, draft.sessionID == sessionID,
               let transform = targetTransform(point: normalizedPoint, frame: frame, orientation: draftOrientation) {
                let anchor = ARAnchor(name: "indoor-memory", transform: transform)
                session.add(anchor: anchor)
                draft.id = anchor.identifier
                draft.hasSpatialAnchor = true
                draft.spatialSource = lastTargetSource
            }
            draft.targetSelectionStatus = draft.hasSpatialAnchor ?
                (draft.spatialSource == "depth" ? "轮廓与深度位置已取得" : "轮廓与平面参考位置已取得；参考点可能在物品后方") :
                "已取得轮廓；尚无可靠空间位置，本次仅保存照片"
            self.draft = draft
        } catch {
            guard selectionGeneration == generation else { return }
            self.draft?.targetSelectionStatus = "\(error.localizedDescription) 可换一个点，或先保存照片。"
        }
        if selectionGeneration == generation { isSelectingTarget = false }
    }

    private func extractTarget(image: CGImage, point: CGPoint) async throws -> IndoorTargetEvidence {
        try await withCheckedThrowingContinuation { continuation in
            visionQueue.async {
                do { continuation.resume(returning: try IndoorTargetVision().extract(image: image, normalizedTap: point)) }
                catch { continuation.resume(throwing: error) }
            }
        }
    }

    /// Use measured depth or an observed plane. Never invent a distance on the camera ray.
    private func targetTransform(point: CGPoint, frame: ARFrame, orientation: UIInterfaceOrientation) -> simd_float4x4? {
        lastTargetSource = nil
        guard case .normal = frame.camera.trackingState,
              case .normal = session.currentFrame?.camera.trackingState else { return nil }
        let sensor: CGPoint
        switch orientation {
        case .landscapeLeft: sensor = point
        case .landscapeRight: sensor = CGPoint(x: 1 - point.x, y: 1 - point.y)
        case .portraitUpsideDown: sensor = CGPoint(x: 1 - point.y, y: point.x)
        default: sensor = CGPoint(x: point.y, y: 1 - point.x)
        }
        let resolution = frame.camera.imageResolution
        let k = frame.camera.intrinsics
        let x = (Float(sensor.x * resolution.width) - k.columns.2.x) / k.columns.0.x
        let y = (Float(sensor.y * resolution.height) - k.columns.2.y) / k.columns.1.y
        let cameraRay = SIMD3<Float>(x, -y, -1)
        let origin4 = frame.camera.transform.columns.3
        let origin = SIMD3(origin4.x, origin4.y, origin4.z)
        let ray4 = frame.camera.transform * SIMD4<Float>(cameraRay.x, cameraRay.y, cameraRay.z, 0)
        let direction = simd_normalize(SIMD3(ray4.x, ray4.y, ray4.z))
        if let depth = frame.sceneDepth, let confidence = depth.confidenceMap {
            let map = depth.depthMap
            CVPixelBufferLockBaseAddress(map, .readOnly)
            CVPixelBufferLockBaseAddress(confidence, .readOnly)
            defer { CVPixelBufferUnlockBaseAddress(map, .readOnly); CVPixelBufferUnlockBaseAddress(confidence, .readOnly) }
            let px = min(CVPixelBufferGetWidth(map) - 1, max(0, Int(sensor.x * CGFloat(CVPixelBufferGetWidth(map)))))
            let py = min(CVPixelBufferGetHeight(map) - 1, max(0, Int(sensor.y * CGFloat(CVPixelBufferGetHeight(map)))))
            if let base = CVPixelBufferGetBaseAddress(map), let conf = CVPixelBufferGetBaseAddress(confidence) {
                let meters = base.advanced(by: py * CVPixelBufferGetBytesPerRow(map)).assumingMemoryBound(to: Float32.self)[px]
                let quality = conf.advanced(by: py * CVPixelBufferGetBytesPerRow(confidence)).assumingMemoryBound(to: UInt8.self)[px]
                if quality == ARConfidenceLevel.high.rawValue, meters.isFinite, meters >= 0.15, meters <= 3 {
                    var transform = matrix_identity_float4x4
                    transform.columns.3 = frame.camera.transform * SIMD4<Float>(x * meters, -y * meters, -meters, 1)
                    lastTargetSource = "depth"
                    return transform
                }
            }
        }
        let query = ARRaycastQuery(origin: origin, direction: direction, allowing: .existingPlaneGeometry, alignment: .any)
        guard let hit = session.raycast(query).first else { return nil }
        let p = hit.worldTransform.columns.3
        let distance = simd_distance(origin, SIMD3(p.x, p.y, p.z))
        lastTargetSource = "plane"
        return (0.15...3).contains(distance) ? hit.worldTransform : nil
    }

    func checkSelectedObject() async {
        guard canCheckSelectedObject, let record = selected, let frame = session.currentFrame,
              ProcessInfo.processInfo.systemUptime - frame.timestamp <= IndoorMemoryPolicy.maximumFrameAge,
              let photo = encodePhoto(frame), let image = UIImage(data: photo)?.cgImage else { return }
        comparisonPhoto = photo
        comparisonVisualization = nil
        checkedAt = Date()
        var tap: CGPoint?
        if let anchor = frame.anchors.first(where: { $0.identifier == record.id }), record.sessionID == sessionID {
            let world = anchor.transform.columns.3
            let cameraPoint = simd_inverse(frame.camera.transform) * world
            let projected = frame.camera.projectPoint(SIMD3(world.x, world.y, world.z), orientation: orientation,
                viewportSize: CGSize(width: image.width, height: image.height))
            let point = CGPoint(x: projected.x / CGFloat(image.width), y: projected.y / CGFloat(image.height))
            if cameraPoint.z < 0, point.x.isFinite, point.y.isFinite,
               (0..<1).contains(point.x), (0..<1).contains(point.y) { tap = point }
        }
        await renderComparison(photo: photo, tap: tap)
    }

    /// Reuses the exact observed photograph; a tap must never silently capture a different frame.
    func selectComparisonTarget(_ point: CGPoint) async {
        guard checkStatus != .checking, let photo = comparisonPhoto else { return }
        await renderComparison(photo: photo, tap: point)
    }

    private func renderComparison(photo: Data, tap: CGPoint?) async {
        guard let record = selected, let reference = record.target,
              let referenceImage = UIImage(data: record.photo)?.cgImage,
              let image = UIImage(data: photo)?.cgImage else {
            checkStatus = .uncertain
            detail = "这条记录没有物品轮廓，请重新记录并点选物品。"
            return
        }
        checkStatus = .checking
        let selectedID = record.id, generation = sessionID, checkID = UUID()
        checkGeneration = checkID
        do {
            let result: IndoorComparisonVisualizationResult = try await withCheckedThrowingContinuation { continuation in
                visionQueue.async {
                    do { continuation.resume(returning: try IndoorComparisonVisualizer.render(
                        referenceImage: referenceImage, referenceTarget: reference, currentImage: image, currentTap: tap)) }
                    catch { continuation.resume(throwing: error) }
                }
            }
            guard self.selectedID == selectedID, sessionID == generation, checkGeneration == checkID, active else { return }
            comparisonVisualization = result
            checkStatus = .uncertain
            detail = result.statusMessage
        } catch {
            guard self.selectedID == selectedID, sessionID == generation, checkGeneration == checkID, active else { return }
            checkStatus = .uncertain
            detail = "轮廓比较失败：\(error.localizedDescription)。请重试。"
        }
    }

    func cancelComparison() {
        checkGeneration = UUID()
        if checkStatus == .checking {
            checkStatus = .uncertain
            detail = "本次比较已取消，可重新查看。"
        }
    }

    func confirmSelectedObject(present: Bool) {
        guard let photo = comparisonPhoto, let observedAt = checkedAt,
              let index = records.firstIndex(where: { $0.id == selectedID }), checkStatus != .checking else { return }
        checkStatus = present ? .present : .absent
        detail = present ? "你确认：仍在原处（本次照片）" : "你确认：原处未见；尚不知道新位置（本次照片）"
        records[index].lastObservation = IndoorMemoryObservation(observedAt: observedAt, confirmedAt: Date(), photo: photo, present: present)
        persist()
    }

#if DEBUG
    // Explicitly labelled UI fixtures: exercise forms and presentation, never AR accuracy.
    private func loadLayoutPreview() {
        state = .ready
        if records.isEmpty {
            records = [IndoorMemoryRecord(id: UUID(), name: "蓝色杯子", recordedAt: Date().addingTimeInterval(-120), sessionID: sessionID, photo: Self.layoutPhoto())]
            selectedID = records.first?.id
        }
        refreshLayoutPreview()
    }

    private func refreshLayoutPreview() {
        state = .ready
        canRecord = records.count < IndoorMemoryPolicy.capacity && draft == nil
        recordHint = canRecord ? "对准物品，记录照片后点选目标" : "请先完成当前记录"
        guidance = "标记是记录时的位置，未确认物品现在是否还在"
        marker = selected == nil ? nil : Marker(point: CGPoint(x: 190, y: 340), distance: 1.2)
    }

    private static func layoutPhoto() -> Data {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 600, height: 450))
        return renderer.jpegData(withCompressionQuality: 0.7) { context in
            UIColor.darkGray.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 600, height: 450))
            UIColor.brown.setFill()
            context.fill(CGRect(x: 0, y: 210, width: 600, height: 240))
            UIColor.systemBlue.setFill()
            context.fill(CGRect(x: 250, y: 160, width: 90, height: 110))
            ("UI 测试示意 · 非相机照片" as NSString).draw(at: CGPoint(x: 20, y: 35), withAttributes: [.foregroundColor: UIColor.white, .font: UIFont.systemFont(ofSize: 24)])
        }
    }
#endif

    nonisolated func sessionWasInterrupted(_ session: ARSession) {
        Task { @MainActor [weak self] in
            self?.stop()
            self?.guidance = "相机已中断，请点重新扫描；旧记录仅保留照片"
        }
    }

    nonisolated func session(_ session: ARSession, didFailWithError error: Error) {
        Task { @MainActor [weak self] in
            self?.stop()
            self?.state = .failed
            self?.error = "空间定位失败，请重新扫描。\(error.localizedDescription)"
        }
    }
}

@MainActor
private final class IndoorMapRequest {
    private var continuation: CheckedContinuation<ARWorldMap?, Never>?
    init(_ continuation: CheckedContinuation<ARWorldMap?, Never>) { self.continuation = continuation }
    func finish(_ map: ARWorldMap?) {
        let pending = continuation
        continuation = nil
        pending?.resume(returning: map)
    }
}

struct IndoorMemoryCamera: UIViewRepresentable {
    let controller: IndoorMemoryController
    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView(frame: .zero)
        view.session = controller.session
        view.backgroundColor = .black
        controller.view = view
        return view
    }
    func updateUIView(_ uiView: ARSCNView, context: Context) {}
    static func dismantleUIView(_ uiView: ARSCNView, coordinator: ()) { uiView.session.pause() }
}
