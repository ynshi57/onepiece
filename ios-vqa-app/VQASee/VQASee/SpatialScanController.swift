import ARKit
import AVFoundation
import Combine
import CoreImage
import Foundation
import UIKit
import ImageIO

/// High-frequency live values are isolated from the large persisted document.
/// A live pose is always an actual ARKit observation, never an interpolation.
@MainActor final class SpatialLivePoseState: ObservableObject {
    @Published private(set) var pose: SpatialPose?
    private(set) var receivedAt = 0.0
    private var lastTime = -Double.infinity
    func update(_ value: SpatialPose, receivedAt: Double = ProcessInfo.processInfo.systemUptime) {
        guard value.position.isFinite, value.forward.isFinite, value.time.isFinite else { clear(); return }
        guard value.time > lastTime else { return }
        // The native camera commonly produces 60 Hz; retain a single latest value.
        guard value.time - lastTime >= 1.0 / 60.0 else { return }
        lastTime = value.time
        self.receivedAt = receivedAt
        pose = value
    }
    func clear() {
        lastTime = -.infinity
        if pose != nil { pose = nil }
    }
}

/// A cached plane set is not a new observation. Only a fresh accepted AR
/// anchor-update timestamp may advance one stability check in this generation.
nonisolated struct SpatialRestorationUpdateEvidence {
    private(set) var generation: UUID
    private(set) var updateBatchCount = 0
    private(set) var consumedBatchCount = 0
    private(set) var stableCheckCount = 0
    private(set) var lastUpdateTimestamp: Double?
    private(set) var lastConsumedUpdateTimestamp: Double?
    private(set) var anchorUpdateTimestamps: [UUID: Double] = [:]
    var freshAnchorIDs: Set<UUID> {
        Set(anchorUpdateTimestamps.filter { lastConsumedUpdateTimestamp == nil || $0.value > lastConsumedUpdateTimestamp! }.map(\.key))
    }

    init(generation: UUID) { self.generation = generation }
    mutating func reset(generation: UUID) {
        self = Self(generation: generation)
    }
    mutating func recordUpdate(timestamp: Double, anchorID: UUID, generation: UUID) {
        guard generation == self.generation, timestamp.isFinite,
              lastConsumedUpdateTimestamp == nil || timestamp > lastConsumedUpdateTimestamp! else { return }
        if timestamp > (anchorUpdateTimestamps[anchorID] ?? -.infinity) { anchorUpdateTimestamps[anchorID] = timestamp }
        if lastUpdateTimestamp == nil || timestamp > lastUpdateTimestamp! {
            lastUpdateTimestamp = timestamp
            updateBatchCount += 1
        }
    }
    mutating func consume(sufficient: Bool, generation: UUID) -> Bool {
        guard generation == self.generation, updateBatchCount > consumedBatchCount, freshAnchorIDs.count >= 2 else { return false }
        consumedBatchCount = updateBatchCount
        lastConsumedUpdateTimestamp = lastUpdateTimestamp
        stableCheckCount = sufficient ? stableCheckCount + 1 : 0
        return true
    }
}

/// RGB + IMU only. This session deliberately does not request sceneDepth or mesh reconstruction.
@MainActor
final class SpatialScanController: NSObject, ObservableObject, ARSessionDelegate {
    let livePose = SpatialLivePoseState()
    @Published var document = SpatialScanDocument()
    #if DEBUG
    @Published private(set) var structureP0Preview: (mapURL:URL?, message:String)?
    #endif
    @Published var status = "点击开始，移动手机观察墙面和地面"
    @Published var isScanning = false
    @Published private(set) var captureGuidance = SpatialCaptureGuidance.State.start
    @Published private(set) var captureSummary: String?
    @Published private(set) var coachMuted = UserDefaults.standard.bool(forKey: SpatialScanCoachSpeech.mutedKey)
    private var guidance = SpatialCaptureGuidance()
    private let coach = SpatialScanCoachSpeech()
    @Published var error: String?
    @Published var saved: [SpatialScanDocument] = []
    @Published var exportURL: URL?
    @Published var isSaving = false
    @Published private(set) var isLocatingObjects = false
    @Published private(set) var objectLocationCompleted = 0
    @Published private(set) var isAnalyzingObjects = false
    @Published private(set) var objectAnalysisCompleted = 0
    @Published private(set) var isFinishingAndSaving = false
    var onSaved: ((SpatialScanDocument, URL) -> Void)?
    @Published private(set) var liveObjectResult: SpatialLiveObjectResult?
    @Published private(set) var liveObjectStatus = "开始扫描后识别物品"
    @Published private(set) var liveObjectCounters = SpatialLiveObjectCounters()
    private let liveObjectDetector = SpatialLiveObjectDetector(modelURL: Bundle.main.url(forResource: "YOLO11nObject", withExtension: "mlmodelc"))
    private var liveObjectRequestID: UUID?
    private var liveObjectRequestBegan: Double?
    private var liveObjectRun = UUID()
    private var liveObjectViewportVersion = UUID()
    private(set) var liveObjectMeasurements: [SpatialLiveObjectMeasurement] = []
    private(set) var previousLiveObjectCounters: SpatialLiveObjectCounters?
    private var previousLiveObjectRun: UUID?
    let liveObjectMeasurementWindowLimit = 256
    private(set) var liveObjectCompletedCount = 0
    private(set) var liveObjectFailedCompletionCount = 0
    private(set) var liveObjectInvalidatedCompletionCount = 0
    private var liveObjectValidity = UUID()
    private var liveObjectCounts = SpatialLiveObjectCounters()
    private var lastLiveCounterPublication: Double = -.infinity
    private var liveObjectViewport: (size: CGSize, orientation: UIInterfaceOrientation)?

    /// Must come from the actual ARSCNView, not the full window or SwiftUI screen.
    func updateLiveObjectViewport(size: CGSize, orientation: UIInterfaceOrientation) {
        guard size.width > 0, size.height > 0, orientation != .unknown else {
            liveObjectViewport = nil
            liveObjectViewportVersion = UUID()
            liveObjectResult = nil
            return
        }
        if liveObjectViewport == nil || liveObjectViewport?.size != size || liveObjectViewport?.orientation != orientation {
            liveObjectViewportVersion = UUID()
            liveObjectResult = nil
        }
        liveObjectViewport = (size, orientation)
    }

    private func resetLiveObjects(scanning: Bool) {
        if liveObjectCounts.submitted > 0 {
            previousLiveObjectCounters = liveObjectCounts
            previousLiveObjectRun = liveObjectRun
        }
        liveObjectRun = UUID()
        liveObjectValidity = UUID()
        liveObjectResult = nil
        liveObjectCounts = SpatialLiveObjectCounters()
        liveObjectCounters = liveObjectCounts
        liveObjectStatus = scanning ? "正在识别物品" : "物品识别已停止"
        // Keep physical in-flight ownership until completion; restarting must not enqueue another request.
    }

    private func submitLiveObjects(_ frame: ARFrame) {
        guard liveObjectRequestID == nil else {
            liveObjectCounts.busy += 1
            let slow = ProcessInfo.processInfo.systemUptime - (liveObjectRequestBegan ?? ProcessInfo.processInfo.systemUptime) > 2
            let message = slow ? "识别耗时过长 · 当前处理尚未结束" : "物品识别处理中 · 部分画面未分析"
            if liveObjectStatus != message { liveObjectStatus = message }
            if ProcessInfo.processInfo.systemUptime - lastLiveCounterPublication >= 0.5 {
                liveObjectCounters = liveObjectCounts
                lastLiveCounterPublication = ProcessInfo.processInfo.systemUptime
            }
            return
        }
        let viewportVersion = liveObjectViewportVersion, validity = liveObjectValidity
        let requestID = UUID(), run = liveObjectRun, scanID = document.id, requestGeneration = generation, requestEpoch = epoch
        liveObjectRequestID = requestID // Claim synchronously before creating the task.
        liveObjectRequestBegan = ProcessInfo.processInfo.systemUptime
        liveObjectCounts.submitted += 1
        if liveObjectStatus != "正在识别物品" { liveObjectStatus = "正在识别物品" }
        let transform = liveObjectViewport.map { frame.displayTransform(for: $0.orientation, viewportSize: $0.size) }
        let affine = transform.map { [$0.a,$0.b,$0.c,$0.d,$0.tx,$0.ty].map(Float.init) }
        let viewport = liveObjectViewport.map { [Float($0.size.width),Float($0.size.height)] }
        let matrix = frame.camera.transform
        let input = SpatialLiveObjectInput(image: frame.capturedImage, scanID: scanID, epoch: requestEpoch,
            capturedAt: frame.timestamp, cameraToWorld: [matrix.columns.0,matrix.columns.1,matrix.columns.2,matrix.columns.3].flatMap { [$0.x,$0.y,$0.z,$0.w] },
            nativeToViewport: affine, viewportSize: viewport)
        Task {
            let result = await liveObjectDetector.analyze(input)
            if liveObjectRequestID == requestID { liveObjectRequestID = nil; liveObjectRequestBegan = nil }
            let invalidated = !isScanning || liveObjectRun != run || liveObjectValidity != validity || document.id != scanID
                || generation != requestGeneration || epoch != requestEpoch || liveObjectViewportVersion != viewportVersion
            liveObjectMeasurements.append(SpatialLiveObjectMeasurement(scanID: scanID, runID: run,
                capturedAt: result.capturedAt, completedAt: result.completedAt, elapsedSeconds: result.elapsedSeconds,
                invalidated: invalidated, failure: result.failure))
            liveObjectCompletedCount += 1
            if result.failure != nil { liveObjectFailedCompletionCount += 1 }
            if invalidated { liveObjectInvalidatedCompletionCount += 1 }
            if liveObjectMeasurements.count > liveObjectMeasurementWindowLimit {
                liveObjectMeasurements.removeFirst(liveObjectMeasurements.count - liveObjectMeasurementWindowLimit)
            }
            if invalidated {
                if liveObjectRun == run {
                    liveObjectCounts.invalidated += 1
                    if result.failure != nil { liveObjectCounts.failed += 1 }
                    liveObjectCounters = liveObjectCounts
                } else if previousLiveObjectRun == run {
                    previousLiveObjectCounters?.invalidated += 1
                    if result.failure != nil { previousLiveObjectCounters?.failed += 1 }
                }
                return
            }
            liveObjectResult = result
            if result.failure != nil {
                liveObjectCounts.failed += 1
                let message = result.detections.isEmpty ? "物品识别失败，请重试" : "部分物品未能识别"
                if liveObjectStatus != message { liveObjectStatus = message }
            } else {
                let message = result.nativeToViewport == nil ? "物品已识别 · 画面尚未对齐" : "物品识别已更新"
                if liveObjectStatus != message { liveObjectStatus = message }
            }
            liveObjectCounters = liveObjectCounts
        }
    }

    let session = ARSession()
    private let store: SpatialScanStore
    @Published private(set) var isRestoring = false
    @Published private(set) var isCapturingMap = false
    private var committedDocument: SpatialScanDocument?

    var keyframeDirectory: URL {
        return store.scanDirectoryURL(document.id)
    }

    var hasUnsavedChanges: Bool {
        // Failed startup status/events alone have no captured data to save.
        guard !document.poses.isEmpty || !document.surfaces.isEmpty || !document.keyframes.isEmpty || document.worldMap != nil else { return false }
        if let committedDocument, committedDocument.id == document.id { return document != committedDocument }
        return true
    }

    private func permitReplacement(discardUnsaved: Bool = false) -> Bool {
        guard !encoding, !isArchivingObjects, !isAnalyzingIndoorSemantics, !isSaving, !isFinishingAndSaving, !starting, !isCapturingMap, !isAnalyzingObjects, !isLocatingObjects else {
            error = "正在处理扫描，请稍候再操作"; return false
        }
        guard discardUnsaved || !hasUnsavedChanges else {
            error = "本次新增内容尚未保存，请先结束并保存"; return false
        }
        return true
    }

    private var pendingMapData: Data?
    private var restoreStarted: Double = 0
    private var restoreMapAnchorIDs = Set<UUID>()
    private var restorePlanes: [UUID: ARPlaneAnchor] = [:]
    private var restoreUpdatedMapAnchorIDs = Set<UUID>()
    private var restoreEvidence = SpatialRestorationUpdateEvidence(generation: UUID())
    private var restoreLastCheck: Double = 0
    private var lastRestoreDiagnosticTime: Double = -.infinity
    private var lastRestoreDiagnosticState = ""
    private var epoch = 0
    private var lastPoseTime: Double = -.infinity
    private var lastKeyframeTime: Double = -.infinity
    private var interrupted = false
    @Published private(set) var isArchivingObjects = false
    @Published private(set) var isAnalyzingIndoorSemantics = false
    @Published private(set) var indoorSemanticCompleted = 0
    private var archiveGeneration = UUID()
    private var archiveFrameIDs = Set<String>()
    private var requiresSemanticReconciliation = false
    private var indoorAnalysisBegan: Double?
    private let archiveObjectAnalyzer = SpatialObjectAnalyzer(modelURL: Bundle.main.url(forResource: "YOLO11nObject", withExtension: "mlmodelc"))
    private let indoorSemanticAnalyzer = SpatialIndoorSemanticAnalyzer(
        modelURL: Bundle.main.url(forResource: "VQASeeIndoorSemantics", withExtension: "mlmodelc"),
        manifestURL: Bundle.main.url(forResource: "VQASeeIndoorSemantics.labels", withExtension: "json"))
    private var pendingKeyframeFailure: String?

    private var nextArchiveFrame: SpatialKeyframe? {
        let objects = Set((document.objectObservations ?? []).map(\.frameID))
        let semantics = Set((document.indoorSemanticObservations ?? []).map(\.frameID))
        return document.keyframes.first { archiveFrameIDs.contains($0.id) && (!objects.contains($0.id) || !semantics.contains($0.id)) }
    }

    /// Called only for a real JPEG already committed to the scan directory.
    func archiveSavedKeyframe(_ frameID: String) {
        guard document.keyframes.contains(where: { $0.id == frameID }) else { return }
        archiveFrameIDs.insert(frameID)
        requiresSemanticReconciliation = true
        pumpArchiveObjects()
    }

    private func pumpArchiveObjects() {
        guard !isArchivingObjects, !isAnalyzingObjects, !isAnalyzingIndoorSemantics, !isSaving,
              nextArchiveFrame != nil else { return }
        isArchivingObjects = true
        let request = archiveGeneration, scanID = document.id, directory = keyframeDirectory
        Task {
            defer { isArchivingObjects = false; isAnalyzingIndoorSemantics = false }
            while archiveGeneration == request, document.id == scanID, let frame = nextArchiveFrame {
                if !(document.objectObservations ?? []).contains(where: { $0.frameID == frame.id }) {
                    var result = await archiveObjectAnalyzer.analyze(frame, directory: directory)
                    guard archiveGeneration == request, document.id == scanID else { return }
                    if result.attemptID == nil { result.attemptID = UUID() }
                    if document.objectObservations == nil { document.objectObservations = [] }
                    document.objectObservations?.append(result)
                    if let failure = result.failure { error = "照片物品识别未完整完成：\(failure)；失败结果已保留" }
                }
                if !(document.indoorSemanticObservations ?? []).contains(where: { $0.frameID == frame.id }) {
                    isAnalyzingIndoorSemantics = true
                    let row = await indoorSemanticAnalyzer.analyze(frame: frame, surfaces: document.surfaces, directory: directory)
                    isAnalyzingIndoorSemantics = false
                    guard archiveGeneration == request, document.id == scanID else { return }
                    if document.indoorSemanticObservations == nil { document.indoorSemanticObservations = [] }
                    document.indoorSemanticObservations?.append(row)
                    requestLiveSemantics()
                    indoorSemanticCompleted += 1
                    if let failure = row.failure { error = "照片结构识别失败：\(failure)；失败结果已保留" }
                }
            }
        }
    }

    @Published private(set) var liveSemanticSnapshot: SpatialLiveSemanticSnapshot?
    var semanticDisplayDocument: SpatialScanDocument {
        guard isScanning else { return document }
        var display = document
        if let snapshot = liveSemanticSnapshot, snapshot.scanID == document.id, snapshot.epoch == epoch,
           SpatialLiveSemanticPolicy.sameScene(snapshot.surfaces, document.surfaces) {
            display.indoorSemanticObservations = snapshot.rows
        } else { display.indoorSemanticObservations = [] }
        return display
    }
    private let liveSemanticReprojector = SpatialLiveSemanticReprojector()
    private var liveSemanticTask: Task<Void, Never>?
    private var liveSemanticTaskID: UUID?
    private var liveSemanticGeneration = UUID()
    private var liveSemanticDirty = false
    private(set) var liveSemanticLastElapsedSeconds: Double?

    private func cancelLiveSemantics() {
        liveSemanticGeneration = UUID()
        liveSemanticSnapshot = nil
        liveSemanticDirty = false
        liveSemanticTask?.cancel()
        // Physical ownership remains until completion; a new run cannot enqueue concurrent work.
    }

    private func requestLiveSemantics() {
        guard isScanning, !isRestoring else { return }
        if let snapshot = liveSemanticSnapshot, !SpatialLiveSemanticPolicy.sameScene(snapshot.surfaces, document.surfaces) {
            liveSemanticSnapshot = nil
        }
        liveSemanticDirty = true
        guard liveSemanticTask == nil else { return }
        let taskID = UUID()
        liveSemanticTaskID = taskID
        liveSemanticTask = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.liveSemanticTaskID == taskID {
                    self.liveSemanticTask = nil
                    self.liveSemanticTaskID = nil
                    // Only an actual update schedules another pass, never a rejected result alone.
                    if self.liveSemanticDirty && self.isScanning { self.requestLiveSemantics() }
                }
            }
            do { try await Task.sleep(for: .milliseconds(100)) } catch { return }
            guard !Task.isCancelled, self.isScanning else { return }
            self.liveSemanticDirty = false
            let request = self.liveSemanticGeneration, scanID = self.document.id, requestEpoch = self.epoch
            let rows = SpatialLiveSemanticPolicy.window(rows:self.document.indoorSemanticObservations ?? [],epoch:requestEpoch)
            guard !rows.isEmpty else { return }
            let surfaces = self.document.surfaces, frames = self.document.keyframes
            let began = ProcessInfo.processInfo.systemUptime
            let computed = await self.liveSemanticReprojector.reconcile(rows:rows,frames:frames,surfaces:surfaces)
            guard !Task.isCancelled, self.isScanning, self.liveSemanticGeneration == request,
                  self.document.id == scanID, self.epoch == requestEpoch else { return }
            self.liveSemanticLastElapsedSeconds = ProcessInfo.processInfo.systemUptime-began
            guard SpatialLiveSemanticPolicy.sameScene(surfaces,self.document.surfaces) else {
                self.liveSemanticSnapshot = nil
                self.liveSemanticDirty = true
                return
            }
            let current = self.document.indoorSemanticObservations ?? []
            let retained = SpatialLiveSemanticPolicy.retainAttempts(computed:computed,current:current)
            self.liveSemanticSnapshot = SpatialLiveSemanticSnapshot(scanID:scanID,epoch:requestEpoch,surfaces:surfaces,rows:retained)
        }
    }

    private func reconcileIndoorThenSave() {
        guard !isAnalyzingIndoorSemantics else { return }
        isAnalyzingIndoorSemantics = true
        let request = archiveGeneration, scanID = document.id
        let rows = document.indoorSemanticObservations ?? [], frames = document.keyframes, surfaces = document.surfaces
        Task {
            let reconciled = await indoorSemanticAnalyzer.reconcile(rows: rows, frames: frames, surfaces: surfaces)
            guard archiveGeneration == request, document.id == scanID else { isAnalyzingIndoorSemantics = false; return }
            document.indoorSemanticObservations = reconciled
            requiresSemanticReconciliation = false
            isAnalyzingIndoorSemantics = false
            save()
        }
    }

    func analyzeIndoorSemantics() {
        guard !encoding, !isArchivingObjects, !isAnalyzingIndoorSemantics, !isAnalyzingObjects, !isLocatingObjects,
              !isScanning, !isSaving, !isFinishingAndSaving, !isCapturingMap, !document.keyframes.isEmpty else { return }
        isAnalyzingIndoorSemantics = true
        indoorAnalysisBegan = ProcessInfo.processInfo.systemUptime
        indoorSemanticCompleted = 0
        let request = archiveGeneration, scanID = document.id, frames = document.keyframes, directory = keyframeDirectory
        Task {
            for frame in frames {
                var row = await indoorSemanticAnalyzer.analyze(frame: frame, surfaces: document.surfaces, directory: directory)
                guard archiveGeneration == request, document.id == scanID else { isAnalyzingIndoorSemantics = false; return }
                if document.indoorSemanticObservations == nil { document.indoorSemanticObservations = [] }
                if let index = document.indoorSemanticObservations?.firstIndex(where: { $0.frameID == frame.id }),
                   let old = document.indoorSemanticObservations?[index] {
                    row.previousAttempts = [old]
                    document.indoorSemanticObservations?[index] = row
                } else { document.indoorSemanticObservations?.append(row) }
                indoorSemanticCompleted += 1
            }
            requiresSemanticReconciliation = true
            isAnalyzingIndoorSemantics = false
            reconcileIndoorThenSave()
        }
    }

    private var encoding = false
    private let encoderQueue = DispatchQueue(label: "vqasee.spatial-keyframe", qos: .utility)
    private var generation = UUID()
    private var performanceTask: Task<Void, Never>?
    private var starting = false
    private var keyframeLimitReported = false
    private(set) var keyframeBytes = 0
    private(set) var photometricLocked = false
    private var photometricLockTask: Task<Void, Never>?
    private var lastTrackingState: SpatialTrackingEvent.TrackingState?
    private var lastTrackingReason: SpatialTrackingEvent.TrackingReason?
    private var lastMappingStatus: SpatialTrackingEvent.MappingStatus?

    override convenience init() {
        self.init(store: SpatialScanStore())
    }

    init(store: SpatialScanStore) {
        self.store = store
        super.init()
        session.delegate = self
        session.delegateQueue = .main
        Task {
            #if DEBUG && targetEnvironment(simulator)
            if ProcessInfo.processInfo.arguments.contains("-spatial-metadata-preview") {
                do {
                    let url = URL.cachesDirectory.appendingPathComponent("SpatialMetadataPreview.json")
                    let data = try Data(contentsOf: url)
                    document = try JSONDecoder().decode(SpatialScanDocument.self, from: data)
                    status = document.events.contains("synthetic_scene_publication_fixture")
                        ? "合成发布示例（非实测）" : "结构回放（未载入照片）"
                } catch {
                    self.error = "结构回放读取失败：\(error.localizedDescription)"
                }
                return // Metadata-only preview: no AR, image loading, store load/save or cleanup.
            }
            #endif
            do { try await store.discardUncommittedDirectories() }
            catch { self.error = "未保存扫描清理失败：\(error.localizedDescription)" }
            await refreshSaved()
            #if DEBUG
            if ProcessInfo.processInfo.arguments.contains("-spatial-structure-vector-verify") {
                await runStructureVectorVerification(); return
            }
            if ProcessInfo.processInfo.arguments.contains("-spatial-saved-preview-verify") {
                if let latest = saved.filter({ !$0.keyframes.isEmpty }).max(by:{ $0.createdAt < $1.createdAt }) {
                    load(latest)
                    status = "已打开保存的空间预览"
                } else {
                    error = "没有可预览的已保存扫描"
                }
                return
            }
            if ProcessInfo.processInfo.arguments.contains("-spatial-indoor-render-verify") {
                guard let scan = saved.first(where: { !$0.keyframes.isEmpty }) else {
                    error = "没有可核对的扫描"
                    return
                }
                load(scan)
                await renderIndoorVerification()
                return
            }
            if ProcessInfo.processInfo.arguments.contains("-spatial-indoor-verify") {
                guard let scan = saved.first(where: { !$0.keyframes.isEmpty }) else {
                    error = "设备上没有带照片的扫描，无法运行结构识别自检"
                    await writeIndoorVerification(failure: error)
                    return
                }
                load(scan)
                analyzeIndoorSemantics()
                return
            }
            if ProcessInfo.processInfo.arguments.contains("-spatial-correspondence-verify") {
                let began = ProcessInfo.processInfo.systemUptime
                let engine = SpatialLocalCorrespondence()
                var report: [String: Any]
                do {
                    let result = try await engine.calibrate()
                    report = ["status": "passed", "elapsedSeconds": result.elapsedSeconds,
                              "forwardMedianPixels": result.forwardMedianPixels, "reverseMedianPixels": result.reverseMedianPixels,
                              "forwardMatchCount": result.forwardMatchCount, "reverseMatchCount": result.reverseMatchCount,
                              "triangulatedSupportCount": result.triangulatedSupportCount, "medianDepthErrorMeters": result.medianDepthErrorMeters,
                              "depthGridCandidateCount":result.depthGridCandidateCount,"depthGridValidCount":result.depthGridValidCount,
                              "depthGridReasonCounts":result.depthGridReasonCounts,"depthGridTriangulatedCount":result.depthGridTriangulatedCount,
                              "depthGridMedianDepthErrorMeters":result.depthGridMedianDepthErrorMeters,
                              "scope": "synthetic translation only; no private photo or metric accuracy claim"]
                } catch {
                    report = ["status": "failed", "failure": String(describing: error),
                              "elapsedSeconds": ProcessInfo.processInfo.systemUptime-began]
                    self.error = "本机空间核对校准失败，尚不能推算物品位置"
                }
                do {
                    try FileManager.default.createDirectory(at: store.baseURL, withIntermediateDirectories: true)
                    try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
                        .write(to: store.baseURL.appendingPathComponent("correspondence-check.json"), options: [.atomic, .completeFileProtection])
                } catch { self.error = "本地校准报告无法保存" }
                return
            }
            if ProcessInfo.processInfo.arguments.contains("-spatial-objects-verify") {
                recordObjectVerification(["status": "loading", "savedScanCount": saved.count, "failure": error ?? ""])
                guard let scan = saved.first(where: { !$0.keyframes.isEmpty }) else {
                    error = error ?? "设备上没有带照片的扫描，无法运行识别自检"
                    recordObjectVerification(["status": "failed", "failure": error ?? ""])
                    return
                }
                load(scan)
                analyzeObjects()
            }
            #endif
        }
    }

    func start() {
        start(discardUnsaved: false)
    }

    func start(discardUnsaved: Bool) {
        guard !isAnalyzingIndoorSemantics, !isAnalyzingObjects, !isLocatingObjects else { error = "请等待照片识别完成"; return }
        guard permitReplacement(discardUnsaved: discardUnsaved) else { return }
        guard !encoding, !isArchivingObjects, !isAnalyzingIndoorSemantics, !isAnalyzingObjects, !isLocatingObjects, !isScanning, !starting, !isCapturingMap, !isSaving, !isFinishingAndSaving else { return }
        guard ARWorldTrackingConfiguration.isSupported else {
            error = "当前设备无法进行空间扫描；可查看已保存的空间"
            status = "相机空间定位不可用"
            return
        }
        starting = true
        status = "准备相机"
        let request = generation
        Task {
            let granted = await AVCaptureDevice.requestAccess(for: .video)
            guard request == generation else { return }
            guard granted else {
                starting = false
                error = "请在系统设置中允许 VQASee 使用相机"
                status = "相机权限未开启"
                return
            }
            guard await discardUncommitted() else { starting = false; return }
            guard request == generation else { return }
            starting = false
            pendingMapData = nil
            cancelLiveSemantics()
            archiveGeneration = UUID()
            archiveFrameIDs.removeAll(); requiresSemanticReconciliation = false; indoorSemanticCompleted = 0; indoorAnalysisBegan = nil
            pendingKeyframeFailure = nil
            document = SpatialScanDocument()
            document.trackingEvents = []
            lastTrackingState = nil
            lastTrackingReason = nil
            lastMappingStatus = nil
            cancelLiveSemantics()
            epoch += 1
            generation = UUID()
            restoreEvidence.reset(generation: generation)
            lastPoseTime = -.infinity
            coach.stop()
            guidance.reset()
            captureGuidance = .start
            captureSummary = nil
            lastKeyframeTime = -.infinity
            error = nil
            exportURL = nil
            interrupted = false
            keyframeLimitReported = false
            keyframeBytes = 0
            let config = ARWorldTrackingConfiguration()
            config.planeDetection = [.horizontal, .vertical]
            config.userFaceTrackingEnabled = false
            config.worldAlignment = .gravity
            session.run(config, options: [.resetTracking, .removeExistingAnchors])
            isScanning = true
            resetLiveObjects(scanning: true)
            schedulePhotometricLock()
            beginPerformanceMeasurement()
            status = "缓慢移动手机，让墙角和地面进入画面"
            document.events.append("started_rgb_imu_no_depth")
            recordTrackingEvent(.started)
        }
    }

    func setCoachMuted(_ muted: Bool) {
        coach.isMuted = muted
        if muted { coach.stop() }
        if coachMuted != muted { coachMuted = muted }
    }

    func stop() {
        cancelLiveSemantics()
        resetLiveObjects(scanning: false)
        livePose.clear()
        finishPerformanceMeasurement()
        // The outdoor AVCaptureSession shares this camera and must not inherit a locked exposure.
        photometricLockTask?.cancel(); photometricLockTask = nil
        setPhotometricLock(false)
        // View disappearance/background callbacks must not cancel an already
        // stopped, explicit finish-and-save request awaiting its map callback.
        if isFinishingAndSaving && !isScanning { return }
        if isRestoring, let reference = document.worldMap {
            document.relocalizationEvents?.append(SpatialRelocalizationEvent(time: ProcessInfo.processInfo.systemUptime,
                state: "cancelled", sourceEpoch: reference.referenceEpoch, newEpoch: epoch,
                detail: "核对完成前会话结束，未合并新轨迹"))
            persistRestorationDiagnostic(nil)
        }
        generation = UUID() // Invalidates live/session requests; archiveGeneration keeps admitted JPEG work alive.
        restoreEvidence.reset(generation: generation)
        restorePlanes.removeAll(); restoreUpdatedMapAnchorIDs.removeAll()
        starting = false
        let wasScanning = isScanning
        isCapturingMap = false
        isRestoring = false
        if wasScanning, document.worldMap == nil, session.currentFrame?.worldMappingStatus == .mapped || session.currentFrame?.worldMappingStatus == .extending {
            isCapturingMap = true
            let request = generation
            let referenceEpoch = epoch
            let references = document.surfaces.filter { $0.epoch == referenceEpoch }
            Task { @MainActor [weak self] in
                try? await Task.sleep(for: .seconds(8))
                guard let self, self.generation == request, self.isCapturingMap else { return }
                self.isCapturingMap = false
                self.session.pause()
                self.error = "定位地图获取超时；照片和轨迹仍可保存，重新扫描可重试"
            }
            session.getCurrentWorldMap { [weak self] map, failure in
                Task { @MainActor [weak self] in
                    guard let self, self.generation == request, self.isCapturingMap else { return }
                    defer { self.isCapturingMap = false; self.session.pause() }
                    do {
                        guard let map else { throw failure ?? NSError(domain: "SpatialMap", code: 1) }
                        let data = try NSKeyedArchiver.archivedData(withRootObject: map, requiringSecureCoding: true)
                        self.stageWorldMapArchive(data, referenceEpoch: referenceEpoch, references: references)
                        self.status = "已结束；定位地图待保存，结构精度仍需核对"
                    } catch {
                        self.error = "定位地图获取失败，照片和轨迹仍可保存：\(error.localizedDescription)"
                    }
                }
            }
        } else {
            session.pause()
            if wasScanning, document.worldMap == nil { error = "地图尚未建立充分；可保存照片和轨迹，但不能恢复定位" }
        }
        if isScanning {
            let summary = guidance.completionMessage()
            captureSummary = summary
            document.events.append("stopped")
            document.events.append("guidance:complete")
            recordTrackingEvent(.stopped)
            coach.announceCompletion(summary)
        }
        isScanning = false
        pumpArchiveObjects()
        if !(document.indoorSemanticObservations ?? []).isEmpty { requiresSemanticReconciliation = true }
        status = document.poses.isEmpty ? "尚未获得有效定位；可以重新扫描" : "已结束；线框仅表示已观测范围"
    }

    func clear() {
        guard permitReplacement(discardUnsaved: true) else { return }
        stop()
        generation = UUID(); restoreEvidence.reset(generation: generation); isCapturingMap = false; pendingMapData = nil
        session.pause()
        let oldID = document.id
        let keepSavedCopy = committedDocument?.id == oldID || saved.contains(where: { $0.id == oldID })
        if !keepSavedCopy {
            Task {
                do { try await store.delete(id: oldID) }
                catch { self.error = "未保存照片清理失败：\(error.localizedDescription)" }
            }
        }
        cancelLiveSemantics()
        archiveGeneration = UUID()
        archiveFrameIDs.removeAll(); requiresSemanticReconciliation = false; indoorSemanticCompleted = 0; indoorAnalysisBegan = nil
        pendingKeyframeFailure = nil
        coach.stop()
        guidance.reset()
        captureGuidance = .start
        captureSummary = nil
        committedDocument = nil
        livePose.clear()
        resetLiveObjects(scanning: false)
        document = SpatialScanDocument()
        exportURL = nil
        error = nil
        interrupted = false
        lastPoseTime = -.infinity
        lastKeyframeTime = -.infinity
        status = "点击开始，建立新的空间"
    }

    /// One explicit user action ends acquisition and waits for map capture
    /// before committing. Failed map capture does not discard valid RGB/poses.
    func stopAndSave() {
        guard !isSaving, !isFinishingAndSaving else { return }
        guard (!isAnalyzingIndoorSemantics || isArchivingObjects), !isAnalyzingObjects, !isLocatingObjects else {
            error = "照片处理尚未结束，请稍候再结束并保存"
            return
        }
        stop()
        let request = generation, scanID = document.id
        guard !document.poses.isEmpty else {
            error = "尚未获得有效定位，不能保存空扫描"
            return
        }
        isFinishingAndSaving = true
        status = isCapturingMap ? "正在结束并保存，请稍候" : "正在保存扫描"
        Task { @MainActor [weak self] in
            guard let self else { return }
            let deadline = ProcessInfo.processInfo.systemUptime + 60
            self.pumpArchiveObjects()
            while (self.isCapturingMap || self.encoding || self.isArchivingObjects || self.nextArchiveFrame != nil),
                  self.generation == request, self.document.id == scanID {
                if ProcessInfo.processInfo.systemUptime >= deadline {
                    self.isFinishingAndSaving = false
                    self.error = "照片保存或识别等待超时，尚未提交扫描；已完成结果保留，请稍后再次保存"
                    return
                }
                self.pumpArchiveObjects()
                do { try await Task.sleep(for: .milliseconds(50)) }
                catch { self.isFinishingAndSaving = false; return }
            }
            guard self.generation == request, self.document.id == scanID else {
                self.isFinishingAndSaving = false
                return
            }
            self.isFinishingAndSaving = false
            if let failure = self.pendingKeyframeFailure {
                self.error = "关键帧未完整保存：\(failure)；尚未自动提交扫描"
                return
            }
            self.save()
        }
    }

    #if DEBUG
    /// Measures preparation and verifies persistence of the same structure revision; no image-model inference.
    private func runStructureVectorVerification() async {
        let began=ProcessInfo.processInfo.systemUptime,runID=UUID()
        let reportURL=store.baseURL.appendingPathComponent("structure-vector-check.json")
        let imageURL=store.baseURL.appendingPathComponent("structure-vector-top.png")
        structureP0Preview=(nil,"正在核对结构矢量；尚非完整房间地图")
        do {
            if FileManager.default.fileExists(atPath:imageURL.path) {try FileManager.default.removeItem(at:imageURL)}
            let args=ProcessInfo.processInfo.arguments
            let metadataOnly=args.contains("-spatial-vector-metadata-only")
            let scan:SpatialScanDocument
            if metadataOnly {
                scan=try JSONDecoder().decode(SpatialScanDocument.self,from:Data(contentsOf:URL.cachesDirectory.appendingPathComponent("SpatialMetadataPreview.json")))
            } else {
                guard let index=args.firstIndex(of:"-spatial-verification-scan-id"),args.indices.contains(index+1),
                      let id=UUID(uuidString:args[index+1]),let selected=saved.first(where:{$0.id == id}) else {throw CocoaError(.fileReadNoSuchFile)}
                scan=selected
            }
            let snapshotStart=ProcessInfo.processInfo.systemUptime
            let snapshot=try await SpatialStructureSnapshotCache.shared.snapshot(for:scan)
            let snapshotSeconds=ProcessInfo.processInfo.systemUptime-snapshotStart
            var updated=scan;updated.structureSnapshot=snapshot
            let saveStart=ProcessInfo.processInfo.systemUptime
            let restored:SpatialScanDocument
            if metadataOnly {
                restored=try JSONDecoder().decode(SpatialScanDocument.self,from:JSONEncoder().encode(updated))
            } else {
                try await store.save(updated)
                restored=try await store.load(id:scan.id)
            }
            guard restored.structureSnapshot == snapshot else {throw NSError(domain:"StructureRevision",code:1,userInfo:[NSLocalizedDescriptionKey:"保存回读的结构版本不一致"])}
            let persistenceSeconds=ProcessInfo.processInfo.systemUptime-saveStart
            document=restored
            if let i=saved.firstIndex(where:{$0.id == restored.id}) {saved[i]=restored}
            let map=snapshot.map
            let renderStart=ProcessInfo.processInfo.systemUptime
            try SpatialStructureP0Renderer.top(map:map,document:restored,output:imageURL)
            let renderSeconds=ProcessInfo.processInfo.systemUptime-renderStart
            let receipt=SpatialStructureP0Receipt(scanID:map.scanID,epoch:map.epoch,structureRevision:map.revision,keyframeIDs:map.frameIDs,corners:map.corners,floorRegions:map.floorRegions,wallSegments:map.wallSegments,tapeSegments:map.wallSegments.map {.init(wallSegmentID:$0.id,measuredMeters:nil,note:"独立测量未提供；候选跨度不是实测墙长")},builderMessage:map.quality.message,quality:map.quality,sourceSurfaceIDs:map.sourceSurfaceIDs,gaps:map.gaps,rejectedCornerCandidates:map.rejectedCornerCandidates)
            let encoder=JSONEncoder();encoder.outputFormatting=[.sortedKeys]
            var report=SpatialStructureVectorVerificationReport(runID:runID,map:map,receipt:receipt,inputSignature:snapshot.inputSignature,sourceFrameCount:scan.keyframes.count,snapshotSeconds:snapshotSeconds,persistenceRoundTripSeconds:persistenceSeconds,renderSeconds:renderSeconds,previewPreparationSeconds:ProcessInfo.processInfo.systemUptime-began,persistedRoundTripEqual:!metadataOnly)
            if metadataOnly {report.metadataCodingRoundTripSeconds=persistenceSeconds;report.persistenceRoundTripSeconds=0;report.meaning="Real metadata replay only; Codable round trip checked. Archive persistence and photo availability NOT verified. Timings exclude camera, inference and first UI presentation."}
            report.floorRegionSummaries=map.floorRegions.map {.init(id:$0.id,outerVertexCount:$0.outerRing.count,holeCount:$0.holes.count,areaMeters2:$0.areaMeters2,closedByExistingEdges:true)}
            try encoder.encode(report).write(to:reportURL,options:[.atomic,.completeFileProtection])
            structureP0Preview=(imageURL,"结构未完成 · 墙段\(map.wallSegments.count) · 地面闭环\(map.floorRegions.count) · revision \(map.revision) · \(metadataOnly ? "仅metadata回放，未验证存档" : "已保存回读")")
            status="结构矢量核对结束，完整房间尚未重建"
        } catch {
            self.error="结构矢量核对失败：\(error.localizedDescription)"
            structureP0Preview=(nil,self.error ?? "结构矢量核对失败")
            let failure:[String:Any]=["runID":runID.uuidString,"state":"incomplete","status":"failed","failure":String(describing:error),"elapsedSeconds":ProcessInfo.processInfo.systemUptime-began]
            if let data=try? JSONSerialization.data(withJSONObject:failure,options:.sortedKeys) {try? data.write(to:reportURL,options:.atomic)}
        }
    }

    private func renderIndoorVerification() async {
        let rows = document.indoorSemanticObservations ?? []
        let chosen = await Task.detached(priority: .utility) {
            rows.filter { $0.status == .processed && $0.taxonomy != nil }.max { a,b in
                func coverage(_ row: SpatialIndoorSemanticObservation) -> Int {
                    guard let taxonomy = row.taxonomy else { return 0 }
                    var wall = 0, floor = 0
                    for (label,score) in zip(row.labels,row.confidence) where score >= 153 {
                        if Int(label) == taxonomy.wallLabelID { wall += 1 }
                        if Int(label) == taxonomy.floorLabelID { floor += 1 }
                    }
                    return min(wall,floor)
                }
                return coverage(a) < coverage(b)
            }
        }.value
        let output = store.baseURL.appendingPathComponent("indoor-semantic-result.jpg")
        let receipt = store.baseURL.appendingPathComponent("indoor-semantic-render-check.json")
        do {
            guard let row = chosen, let frame = document.keyframes.first(where: { $0.id == row.frameID }) else {
                throw CocoaError(.fileReadNoSuchFile)
            }
            let window = SpatialLiveSemanticPolicy.window(rows:rows,epoch:row.epoch)
            let frames = document.keyframes, surfaces = document.surfaces
            let reprojector = SpatialLiveSemanticReprojector()
            var reprojectionSeconds: [Double] = []
            var reprojectionRecordCount = 0
            for _ in 0..<10 {
                let began = ProcessInfo.processInfo.systemUptime
                let projected = await reprojector.reconcile(rows:window,frames:frames,surfaces:surfaces)
                reprojectionSeconds.append(ProcessInfo.processInfo.systemUptime-began)
                reprojectionRecordCount = projected.reduce(0) { $0 + $1.records.count }
            }
            try SpatialIndoorSemanticVerificationRenderer.render(frame: frame, row: row, directory: keyframeDirectory, outputURL: output)
            let report: [String:Any] = ["status":"completed", "selection":"balanced-model-wall-floor-candidate", "bytes":try output.resourceValues(forKeys:[.fileSizeKey]).fileSize ?? 0,
                "liveReprojectionSeconds":reprojectionSeconds, "liveReprojectionRowCount":window.count,
                "liveReprojectionRecordCount":reprojectionRecordCount,
                "liveReprojectionStaticReplayOnly":true]
            try JSONSerialization.data(withJSONObject:report).write(to:receipt,options:.atomic)
        } catch {
            self.error = "本地核对图无法生成：\(error.localizedDescription)"
            let report = ["status":"failed", "failure":String(describing:error)]
            try? JSONSerialization.data(withJSONObject:report).write(to:receipt,options:.atomic)
        }
    }

    private func writeIndoorVerification(failure: String? = nil) async {
        guard ProcessInfo.processInfo.arguments.contains("-spatial-indoor-verify") else { return }
        let rows = document.indoorSemanticObservations ?? []
        let surfaces = document.surfaces, frameCount = document.keyframes.count, began = indoorAnalysisBegan
        let directory = store.baseURL
        // Aggregate geometry and JSON serialization are background work, not a main-actor rendering stall.
        do {
            try await Task.detached(priority: .utility) {
                let records = rows.flatMap(\.records)
                let bySurface = Dictionary(grouping: records, by: \.surfaceID)
                let resolved = surfaces.map { SpatialSurfaceSemanticEvidence.resolve(surface: $0, records: bySurface[$0.id] ?? []) }
                let report: [String: Any] = ["frameCount": frameCount, "observationCount": rows.count,
                    "processedCount": rows.filter { $0.status == .processed }.count,
                    "failedCount": rows.filter { $0.status == .failed }.count,
                    "elapsedSeconds": rows.map(\.elapsedSeconds),
                    "wallClockSeconds": began.map { ProcessInfo.processInfo.systemUptime - $0 } ?? 0,
                    "wallClockAvailable": began != nil,
                    "resolvedWallCount": resolved.filter { $0 == .wall }.count,
                    "resolvedFloorCount": resolved.filter { $0 == .floor }.count,
                    "otherCount": resolved.filter { $0 == .other }.count,
                    "unknownCount": resolved.filter { $0 == .unknown }.count,
                    "recordCount": records.count,
                    "sampledRecordCount": records.filter { $0.status == .sampled }.count,
                    "unavailableRecordCount": records.filter { $0.status == .unavailable }.count,
                    "failedRecordCount": records.filter { $0.status == .failed }.count,
                    "wallSupportSamples": records.reduce(0) { $0 + $1.wallSupport },
                    "floorSupportSamples": records.reduce(0) { $0 + $1.floorSupport },
                    "otherOppositionSamples": records.reduce(0) { $0 + $1.otherOpposition },
                    "uncertainSamples": records.reduce(0) { $0 + $1.uncertain },
                    "occludedSamples": records.reduce(0) { $0 + $1.occluded },
                    "failure": failure ?? ""]
                try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
                try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
                    .write(to: directory.appendingPathComponent("indoor-semantics-check.json"), options: [.atomic, .completeFileProtection])
            }.value
        } catch { self.error = "结构自检统计无法保存：\(error.localizedDescription)" }
    }

    private func recordObjectVerification(_ report: [String: Any]) {
        guard ProcessInfo.processInfo.arguments.contains("-spatial-objects-verify") else { return }
        do {
            try FileManager.default.createDirectory(at: store.baseURL, withIntermediateDirectories: true)
            try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
                .write(to: store.baseURL.appendingPathComponent("object-observation-check.json"), options: [.atomic, .completeFileProtection])
        } catch { self.error = "自检状态无法写入：\(error.localizedDescription)" }
    }
    #endif

    func analyzeObjects() {
        guard !encoding, !isArchivingObjects, !isScanning, !isSaving, !isFinishingAndSaving, !isCapturingMap, !isAnalyzingIndoorSemantics, !isAnalyzingObjects, !isLocatingObjects, !document.keyframes.isEmpty else { return }
        let scanID = document.id, analysisGeneration = generation
        let frames = document.keyframes, directory = keyframeDirectory
        let analyzer = archiveObjectAnalyzer
        isAnalyzingObjects = true
        #if DEBUG
        recordObjectVerification(["status": "running", "totalFrameCount": frames.count, "completed": 0])
        #endif
        error = nil
        objectAnalysisCompleted = 0
        if document.objectObservations == nil { document.objectObservations = [] }
        Task {
            defer { isAnalyzingObjects = false }
            for frame in frames {
                var result = await analyzer.analyze(frame, directory: directory)
                guard generation == analysisGeneration, document.id == scanID else {
                    if document.id == scanID { error = "识别被中断，本次完成\(objectAnalysisCompleted)/\(frames.count)张；新增结果尚未保存，请重试或保存" }
                    #if DEBUG
                    recordObjectVerification(["status": "interrupted", "completed": objectAnalysisCompleted, "failure": error ?? "scan generation changed"])
                    #endif
                    return
                }
                if let index = document.objectObservations?.firstIndex(where: { $0.frameID == frame.id }), let old = document.objectObservations?[index] {
                    result.previousAttempts = [old]
                    document.objectObservations?[index] = result
                } else { document.objectObservations?.append(result) }
                objectAnalysisCompleted += 1
                #if DEBUG
                if objectAnalysisCompleted == 1 || objectAnalysisCompleted % 25 == 0 {
                    recordObjectVerification(["status": "running", "totalFrameCount": frames.count, "completed": objectAnalysisCompleted, "lastFailure": result.failure ?? ""])
                }
                #endif
            }
            // Use the existing atomic scan store and surface any save failure.
            isAnalyzingObjects = false
            save()
        }
    }

    func locateObjectSupports() {
        guard !encoding, !isArchivingObjects, !isScanning, !isSaving, !isFinishingAndSaving, !isCapturingMap,
              !isAnalyzingIndoorSemantics, !isAnalyzingObjects, !isLocatingObjects else { return }
        let rows = document.objectObservations ?? []
        guard !rows.isEmpty else { error = "请先识别已采集照片"; return }
        guard rows.allSatisfy({ $0.attemptID != nil }) else {
            error = "请先重新识别照片，为位置核对建立可追溯的来源"; return
        }
        let scanID = document.id, requestGeneration = generation
        let frames = document.keyframes, directory = keyframeDirectory
        let analyzer = SpatialObjectSupportAnalyzer()
        isLocatingObjects = true; objectLocationCompleted = 0; error = nil
        Task {
            defer { isLocatingObjects = false }
            for row in rows {
                guard let frame = frames.first(where: { $0.id == row.frameID }) else { continue }
                let attempts = await analyzer.analyze(source: frame, observation: row, frames: frames, directory: directory)
                guard generation == requestGeneration, document.id == scanID else {
                    error = "位置核对已中断，已完成结果仍保留，请保存或重试"; return
                }
                if document.objectSupportAttempts == nil { document.objectSupportAttempts = [] }
                document.objectSupportAttempts?.append(contentsOf: attempts)
                objectLocationCompleted += 1
            }
            isLocatingObjects = false
            save()
        }
    }

    func save() {
        guard !encoding, !isArchivingObjects else { error = "照片正在保存或识别，完成后可再次保存"; return }
        guard nextArchiveFrame == nil else {
            pumpArchiveObjects()
            error = "照片识别归档尚未完成，完成后可再次保存"
            return
        }
        guard !isAnalyzingIndoorSemantics, !isAnalyzingObjects, !isLocatingObjects else { error = "照片识别尚未结束，请稍候保存"; return }
        guard !isSaving else { error = "扫描正在保存，请稍候"; return }
        guard !isCapturingMap else { error = "定位地图正在获取，请稍候；结束并保存会自动完成"; return }
        guard !isFinishingAndSaving else { error = "正在结束并保存，请稍候"; return }
        guard !isScanning, !document.poses.isEmpty else {
            error = "请先结束扫描，并获得有效定位后保存"
            return
        }
        if requiresSemanticReconciliation {
            reconcileIndoorThenSave()
            return
        }
        var snapshot = document
        if snapshot.worldMap == nil {
            snapshot.events.append("saved_without_relocalization_map")
        }
        let mapData = pendingMapData
        let originalDocument = document
        let saveGeneration = generation
        isSaving = true
        error = nil
        Task {
            defer { isSaving = false }
            do {
                if let mapData, snapshot.worldMap != nil {
                    // Do not begin a mutating optional-chain access to snapshot
                    // before evaluating an async RHS which reads snapshot.id.
                    // Swift's runtime exclusivity check aborts in that case.
                    let scanID = snapshot.id
                    let checksum = try await store.saveWorldMap(mapData, scanID: scanID)
                    snapshot.worldMap?.sha256 = checksum
                }
                try await store.save(snapshot)
                guard generation == saveGeneration, document == originalDocument else {
                    error = "保存期间内容发生变化，新增内容仍在本次扫描中，请再次保存"
                    await refreshSaved()
                    return
                }
                committedDocument = snapshot
                document = snapshot
                status = snapshot.worldMap == nil ? "已保存照片和轨迹；没有定位地图，不能恢复定位" : "已保存；可从历史记录重开查看"
                onSaved?(snapshot, store.baseURL.appendingPathComponent(snapshot.id.uuidString))
                await refreshSaved()
                #if DEBUG
                if ProcessInfo.processInfo.arguments.contains("-spatial-objects-verify"), let rows = snapshot.objectObservations {
                    let report: [String: Any] = [
                        "status": "completed", "scanID": snapshot.id.uuidString,
                        "totalFrameCount": snapshot.keyframes.count,
                        "sameAfterStoreReload": saved.first(where: { $0.id == snapshot.id })?.objectObservations == rows,
                        "rows": rows.map { row -> [String: Any] in
                            ["frameID": row.frameID, "status": row.status.rawValue,
                             "elapsedSeconds": row.elapsedSeconds, "detectionCount": row.detections.count,
                             "labels": row.detections.map(\.label), "clippedDetectionCount": row.detections.filter(\.isClipped).count,
                             "detectionFailures": row.detectionFailures ?? [], "failure": row.failure ?? ""]
                        }
                    ]
                    do {
                        if let row = rows.first(where: { !$0.detections.isEmpty }), let frame = snapshot.keyframes.first(where: { $0.id == row.frameID }) {
                            try SpatialObjectVerificationRenderer.save(frame: frame, row: row,
                                directory: store.scanDirectoryURL(snapshot.id), output: store.baseURL.appendingPathComponent("object-observation-view.jpg"))
                        }
                        try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
                            .write(to: store.baseURL.appendingPathComponent("object-observation-check.json"), options: [.atomic, .completeFileProtection])
                    } catch { self.error = "识别已保存，但自检摘要无法写入：\(error.localizedDescription)" }
                }
                #endif
                do {
                    let url = try await store.export(snapshot)
                    if generation == saveGeneration, document == snapshot { exportURL = url }
                }
                catch { self.error = "扫描已保存，但导出包生成失败：\(error.localizedDescription)" }
                #if DEBUG
                await writeIndoorVerification(failure: self.error)
                #endif
            } catch { self.error = "保存失败：\(error.localizedDescription)；扫描仍在本次内存中" }
        }
    }

    /// Stages archive bytes from ARKit; persistence is exercised independently
    /// from camera hardware, including the exact async save path.
    func stageWorldMapArchive(_ data: Data, referenceEpoch: Int, references: [SpatialSurface]) {
        pendingMapData = data
        document.worldMap = SpatialWorldMapReference(sha256: "", referenceEpoch: referenceEpoch,
            capturedAt: ProcessInfo.processInfo.systemUptime, mappingStatus: "captured", referenceSurfaces: references)
    }

    func load(_ value: SpatialScanDocument) {
        if value.id == document.id, hasUnsavedChanges {
            error = "已保留本次新增内容，请先结束并保存"
            return
        }
        guard permitReplacement() else { return }
        stop()
        generation = UUID(); restoreEvidence.reset(generation: generation); isCapturingMap = false
        session.pause()
        let oldID = document.id
        if oldID != value.id, !saved.contains(where: { $0.id == oldID }) {
            Task {
                do { try await store.delete(id: oldID) }
                catch { self.error = "未保存照片清理失败：\(error.localizedDescription)" }
            }
        }
        pendingMapData = nil
        cancelLiveSemantics()
        archiveGeneration = UUID()
        archiveFrameIDs.removeAll(); requiresSemanticReconciliation = false; indoorSemanticCompleted = 0; indoorAnalysisBegan = nil
        pendingKeyframeFailure = nil
        document = value
        committedDocument = value
        exportURL = nil
        keyframeBytes = SpatialScanStore.maximumKeyframeBytes // conservative while disk accounting is pending
        let loadGeneration = generation
        Task {
            do {
                let bytes = try await store.keyframeStorageBytes(value)
                if generation == loadGeneration, document == value { keyframeBytes = bytes }
                let url = try await store.export(value)
                if generation == loadGeneration, document == value { exportURL = url }
            } catch { self.error = "导出失败：\(error.localizedDescription)" }
        }
        status = "历史空间 · 未与当前相机重新定位"
    }

    func restore() {
        guard !encoding, !isArchivingObjects, !isScanning, !starting, !isSaving, !isCapturingMap, !isFinishingAndSaving, !isAnalyzingIndoorSemantics, !isAnalyzingObjects, !isLocatingObjects else { return }
        guard let reference = document.worldMap else { error = "这份旧记录没有定位地图，请开始新的扫描"; return }
        let scanID = document.id
        starting = true
        Task {
            defer { starting = false }
            do {
                guard await AVCaptureDevice.requestAccess(for: .video) else { error = "请允许相机权限"; return }
                let restoreDocument = document, restoreGeneration = generation
                let bytes = try await store.keyframeStorageBytes(restoreDocument)
                guard generation == restoreGeneration, document == restoreDocument else { return }
                keyframeBytes = bytes
                let data = try await store.loadWorldMap(restoreDocument)
                guard generation == restoreGeneration, document == restoreDocument else { return }
                guard let map = try NSKeyedUnarchiver.unarchivedObject(ofClass: ARWorldMap.self, from: data) else { throw SpatialScanStore.StoreError.corruptArchive }
                restoreMapAnchorIDs = Set(map.anchors.map(\.identifier))
                restorePlanes = [:]; restoreUpdatedMapAnchorIDs = []; restoreLastCheck = 0
                lastRestoreDiagnosticTime = -.infinity; lastRestoreDiagnosticState = ""
                epoch = max(epoch, document.poses.map(\.epoch).max() ?? 0) + 1
                generation = UUID(); restoreEvidence.reset(generation: generation); interrupted = false; lastPoseTime = -.infinity; lastKeyframeTime = -.infinity
                coach.stop(); guidance.reset(); captureGuidance = .start; captureSummary = nil; error = nil
                document.relocalizationEvents = document.relocalizationEvents ?? []
                document.relocalizationEvents?.append(SpatialRelocalizationEvent(time: ProcessInfo.processInfo.systemUptime, state: "requested", sourceEpoch: reference.referenceEpoch, newEpoch: epoch))
                let config = ARWorldTrackingConfiguration()
                config.worldAlignment = .gravity; config.planeDetection = [.horizontal, .vertical]
                config.initialWorldMap = map
                livePose.clear()
                resetLiveObjects(scanning: true)
                isRestoring = true; isScanning = true; beginPerformanceMeasurement(); restoreStarted = ProcessInfo.processInfo.systemUptime
                session.run(config, options: [.resetTracking, .removeExistingAnchors])
                schedulePhotometricLock()
                status = "对准原来的墙角，缓慢移动；正在核对空间"
                persistRestorationDiagnostic(nil)
            } catch { self.error = "无法恢复定位地图：\(error.localizedDescription)；原记录保留" }
        }
    }

    private func checkRestoration(_ frame: ARFrame) -> Bool {
        defer { persistRestorationDiagnostic(frame) }
        guard let reference = document.worldMap else { return false }
        if ProcessInfo.processInfo.systemUptime - restoreStarted > 45 {
            document.relocalizationEvents?.append(SpatialRelocalizationEvent(time: frame.timestamp, state: "timeout", sourceEpoch: reference.referenceEpoch, newEpoch: epoch, detail: "未获得充分新平面几何证据"))
            restoreEvidence.reset(generation: generation)
            restorePlanes.removeAll(); restoreUpdatedMapAnchorIDs.removeAll()
            livePose.clear(); finishPerformanceMeasurement()
            isRestoring = false; isScanning = false; session.pause()
            status = "未能核对空间；原记录保留"
            error = "恢复超时，请回到原扫描的墙角后重试；没有合并新轨迹"
            return false
        }
        guard case .normal = frame.camera.trackingState else {
            restoreEvidence.reset(generation: generation); restorePlanes.removeAll(); restoreUpdatedMapAnchorIDs.removeAll()
            status = "正在恢复定位，请观察原来的墙角"; return false
        }
        guard frame.timestamp - restoreLastCheck >= 0.15 else { return false }
        restoreLastCheck = frame.timestamp
        let freshIDs = restoreEvidence.freshAnchorIDs
        let freshPlanes = restorePlanes.filter { freshIDs.contains($0.key) }
        guard freshPlanes.count >= 2 else {
            status = "等待墙面和地面的新观测，正在核对空间"
            return false
        }
        let candidates = freshPlanes.values.map { plane -> SpatialSurface in
            let vertices = plane.geometry.boundaryVertices.map { v -> SpatialPoint in
                let w = plane.transform * SIMD4<Float>(v.x,v.y,v.z,1)
                return SpatialPoint(x:w.x,y:w.y,z:w.z)
            }
            return SpatialSurface(id: plane.identifier.uuidString, kind: plane.alignment == .vertical ? "vertical" : "horizontal", vertices: vertices)
        }
        let matches = SpatialMapGeometry.matches(reference.referenceSurfaces, candidates)
        guard restoreEvidence.consume(sufficient: matches.sufficient, generation: generation) else { return false }
        status = "已恢复定位，正在核对当前观测面（\(matches.count)处）"
        guard restoreEvidence.stableCheckCount >= 10 else { return false }
        document.relocalizationEvents?.append(SpatialRelocalizationEvent(time: frame.timestamp, state: "geometryConsistent", sourceEpoch: reference.referenceEpoch, newEpoch: epoch, matchedPlaneCount: matches.count, maxPlaneDistance: matches.maxDistance, detail: "恢复后的ARKit平面更新与保存平面一致；非独立尺寸或位置真值"))
        isRestoring = false
        status = "空间几何一致；真实位置精度仍需核对"
        return true
    }

    private struct RestorationDiagnostic: Codable {
        let schemaVersion: Int
        let time: Double
        let state: String
        let status: String
        let tracking: String
        let candidatePlaneCount: Int
        let restoredAnchorCount: Int
        let updatedSavedAnchorCount: Int
        let stableCheckCount: Int
        let planeUpdateBatchCount: Int
        let consumedPlaneUpdateBatchCount: Int
        let lastPlaneUpdateTimestamp: Double?
        let freshPlaneUpdateCount: Int
        let lastConsumedPlaneUpdateTimestamp: Double?
        let events: [SpatialRelocalizationEvent]
    }

    private func persistRestorationDiagnostic(_ frame: ARFrame?) {
        let now = ProcessInfo.processInfo.systemUptime
        let state = document.relocalizationEvents?.last?.state ?? "unknown"
        guard state != lastRestoreDiagnosticState || now - lastRestoreDiagnosticTime >= 1 else { return }
        lastRestoreDiagnosticTime = now; lastRestoreDiagnosticState = state
        let tracking: String
        if let frame {
            switch frame.camera.trackingState {
            case .normal: tracking = "normal"
            case .notAvailable: tracking = "notAvailable"
            case .limited(let reason): tracking = "limited:\(reason)"
            }
        } else { tracking = "no_frame" }
        let evidence = RestorationDiagnostic(schemaVersion: 2, time: now, state: state, status: status,
            tracking: tracking, candidatePlaneCount: restorePlanes.count,
            restoredAnchorCount: restoreMapAnchorIDs.count, updatedSavedAnchorCount: restoreUpdatedMapAnchorIDs.count,
            stableCheckCount: restoreEvidence.stableCheckCount,
            planeUpdateBatchCount: restoreEvidence.updateBatchCount,
            consumedPlaneUpdateBatchCount: restoreEvidence.consumedBatchCount,
            lastPlaneUpdateTimestamp: restoreEvidence.lastUpdateTimestamp,
            freshPlaneUpdateCount: restoreEvidence.freshAnchorIDs.count,
            lastConsumedPlaneUpdateTimestamp: restoreEvidence.lastConsumedUpdateTimestamp,
            events: Array((document.relocalizationEvents ?? []).suffix(64)))
        let scanID = document.id
        do {
            let data = try JSONEncoder().encode(evidence)
            Task {
                do { try await store.saveRestorationDiagnostic(data, scanID: scanID) }
                catch { self.error = "恢复诊断保存失败：\(error.localizedDescription)；原空间记录保留" }
            }
        } catch { self.error = "恢复诊断编码失败：\(error.localizedDescription)" }
    }

    func remove(_ value: SpatialScanDocument) {
        guard !encoding, !isArchivingObjects, !isSaving, !isFinishingAndSaving, !starting, !isCapturingMap, !isAnalyzingIndoorSemantics, !isAnalyzingObjects, !isLocatingObjects, !(isScanning && value.id == document.id) else {
            error = "正在处理扫描，请稍候再删除"; return
        }
        guard value.id != document.id || !hasUnsavedChanges else {
            error = "本次新增内容尚未保存，不能删除这份记录"; return
        }
        Task {
            do { try await store.remove(value); await refreshSaved() }
            catch { self.error = "删除失败：\(error.localizedDescription)" }
        }
    }

    private func refreshSaved() async {
        do { saved = try await store.loadAll() }
        catch { self.error = "历史空间读取失败：\(error.localizedDescription)" }
    }

    private func discardUncommitted() async -> Bool {
        guard !saved.contains(where: { $0.id == document.id }) else { return true }
        do { try await store.delete(id: document.id); return true }
        catch { self.error = "未保存照片清理失败：\(error.localizedDescription)"; return false }
    }

    private func beginPerformanceMeasurement() {
        performanceTask?.cancel()
        let scanID = document.id
        SpatialScreenPerformanceProbe.shared.begin(scanID: scanID.uuidString)
        performanceTask = Task { @MainActor [weak self] in
            var ticks = 0
            while !Task.isCancelled {
                guard let self, self.isScanning, self.document.id == scanID else { break }
                SpatialScreenPerformanceProbe.shared.sampleMemory(poseCount: self.document.poses.count, surfaceCount: self.document.surfaces.count, keyframeCount: self.document.keyframes.count)
                if ticks % 5 == 0 { self.persistPerformanceSnapshot(scanID: scanID) }
                ticks += 1
                do { try await Task.sleep(for: .seconds(1)) } catch { break }
            }
        }
    }

    private func finishPerformanceMeasurement() {
        guard performanceTask != nil else { return }
        performanceTask?.cancel(); performanceTask = nil
        SpatialScreenPerformanceProbe.shared.finish()
        persistPerformanceSnapshot(scanID: document.id)
    }

    private func persistPerformanceSnapshot(scanID: UUID) {
        do {
            let data = try SpatialScreenPerformanceProbe.shared.encodedSnapshot()
            Task {
                do { try await store.savePerformanceDiagnostic(data, scanID: scanID) }
                catch { self.error = "性能证据保存失败：\(error.localizedDescription)" }
            }
        } catch { self.error = "性能证据编码失败：\(error.localizedDescription)" }
    }

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let receivedAt = ProcessInfo.processInfo.systemUptime
        guard isScanning else { return }
        liveObjectCounts.received += 1
        SpatialScreenPerformanceProbe.shared.recordCallback(source: frame.timestamp, received: receivedAt)
        if isRestoring, !checkRestoration(frame) {
            liveObjectCounts.trackingUnavailable += 1
            livePose.clear(); recordFrameState(frame); return
        }
        switch frame.camera.trackingState {
        case .normal:
            if interrupted {
                cancelLiveSemantics()
                epoch += 1
                interrupted = false
                document.events.append("tracking_recovered_new_segment")
                recordFrameState(frame)
                recordTrackingEvent(.trackingRecovered, frame: frame)
            } else { recordFrameState(frame) }
            let scanningStatus = "正在扫描 · \(document.surfaces.count) 个观测面 · 边界尚需核对"
            if status != scanningStatus { status = scanningStatus }
        case .notAvailable:
            liveObjectCounts.trackingUnavailable += 1
            recordFrameState(frame)
            markTrackingGap("定位不可用，请缓慢移动到有纹理的位置", frame: frame)
            return
        case .limited(let reason):
            liveObjectCounts.trackingUnavailable += 1
            recordFrameState(frame)
            let message: String
            switch reason {
            case .initializing: message = "正在建立定位，请缓慢移动"
            case .excessiveMotion: message = "移动过快，请放慢"
            case .insufficientFeatures: message = "画面纹理不足，请对准墙角或有纹理的位置"
            case .relocalizing: message = "正在恢复定位，轨迹暂不连接"
            @unknown default: message = "定位暂不可用，请缓慢移动"
            }
            markTrackingGap(message, frame: frame)
            return
        }
        let t = frame.camera.transform
        let pose = SpatialPose(time: frame.timestamp,
            position: SpatialPoint(x: t.columns.3.x, y: t.columns.3.y, z: t.columns.3.z),
            forward: SpatialPoint(x: -t.columns.2.x, y: -t.columns.2.y, z: -t.columns.2.z), epoch: epoch)
        livePose.update(pose, receivedAt: receivedAt)
        submitLiveObjects(frame)
        guard frame.timestamp - lastPoseTime >= 0.1 else { return }
        lastPoseTime = frame.timestamp
        applyGuidance(SpatialCaptureGuidance.Observation(time: frame.timestamp, pose: pose, tracking: .normal,
            planes: guidancePlanes(in: frame)))
        if SpatialScanGeometry.shouldSample(previous: document.poses.last, candidate: pose,
            minimumDistance: 0.02, minimumAngle: .pi / 90, maximumInterval: 0.2) { document.poses.append(pose) }
        if document.poses.count >= 12_000 {
            stop()
            error = "本次扫描达到容量限制，请保存后开始新的空间"
            return
        }
        captureKeyframe(frame, pose: pose)
    }

    private func markTrackingGap(_ message: String, frame: ARFrame? = nil) {
        liveObjectValidity = UUID()
        if liveObjectResult != nil { liveObjectResult = nil }
        if liveObjectStatus != "定位暂不可用 · 物品画面未对齐" { liveObjectStatus = "定位暂不可用 · 物品画面未对齐" }
        if ProcessInfo.processInfo.systemUptime - lastLiveCounterPublication >= 0.5 {
            liveObjectCounters = liveObjectCounts
            lastLiveCounterPublication = ProcessInfo.processInfo.systemUptime
        }
        livePose.clear()
        if status != message { status = message }
        applyTrackingGuidance(frame)
        if !interrupted {
            cancelLiveSemantics()
            document.events.append("tracking_gap:\(message)")
            recordTrackingEvent(.trackingGap, frame: frame, detail: message)
            interrupted = true
        }
    }

    func session(_ session: ARSession, didAdd anchors: [ARAnchor]) { updatePlanes(anchors, updated: false) }
    func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) { updatePlanes(anchors, updated: true) }
    func session(_ session: ARSession, didRemove anchors: [ARAnchor]) {
        guard isScanning else { return }
        let ids = Set(anchors.map { $0.identifier.uuidString })
        for id in anchors.map(\.identifier) { restorePlanes.removeValue(forKey: id) }
        if isRestoring { restoreEvidence.reset(generation: generation); return }
        document.surfaces.removeAll { ids.contains($0.id) }
        requestLiveSemantics()
        document.events.append("anchors_removed:\(ids.count)")
        recordTrackingEvent(.anchorsRemoved, removedAnchorIDs: ids.sorted())
    }

    private func updatePlanes(_ anchors: [ARAnchor], updated: Bool) {
        guard isScanning else { return }
        var geometryChanged = false
        for plane in anchors.compactMap({ $0 as? ARPlaneAnchor }) {
            if isRestoring {
                guard let frame = session.currentFrame else { continue }
                let normal: Bool
                if case .normal = frame.camera.trackingState { normal = true } else { normal = false }
                let savedAnchor = restoreMapAnchorIDs.contains(plane.identifier)
                guard SpatialMapObservationPolicy.accepts(savedAnchor: savedAnchor, updated: updated,
                    trackingNormal: normal, elapsed: ProcessInfo.processInfo.systemUptime - restoreStarted,
                    intersectsCameraView: planeIntersectsCamera(plane, frame: frame)) else { continue }
                restorePlanes[plane.identifier] = plane
                if updated { restoreEvidence.recordUpdate(timestamp: frame.timestamp, anchorID: plane.identifier, generation: generation) }
                if savedAnchor { restoreUpdatedMapAnchorIDs.insert(plane.identifier) }
                continue
            }
            let geometry = plane.geometry
            let vertices = (0..<geometry.boundaryVertices.count).map { index -> SpatialPoint in
                let v = geometry.boundaryVertices[index]
                let world = plane.transform * SIMD4<Float>(v.x, v.y, v.z, 1)
                return SpatialPoint(x: world.x, y: world.y, z: world.z)
            }
            guard vertices.count >= 3 else { continue }
            let surface = SpatialSurface(id: plane.identifier.uuidString,
                kind: plane.alignment == .vertical ? "vertical" : "horizontal", vertices: vertices, observed: true,
                observationTime: session.currentFrame?.timestamp ?? 0, epoch: epoch)
            if let i = document.surfaces.firstIndex(where: { $0.id == surface.id }) {
                let old = document.surfaces[i]
                if old.vertices != surface.vertices || old.epoch != surface.epoch || old.kind != surface.kind { geometryChanged = true }
                document.surfaces[i] = surface
            } else { document.surfaces.append(surface); geometryChanged = true }
        }
        if geometryChanged { requestLiveSemantics() }
    }

    private func applyGuidance(_ observation: SpatialCaptureGuidance.Observation) {
        let next = guidance.update(observation)
        guard captureGuidance != next else { return }
        captureGuidance = next
        document.events.append(next.eventName)
        if isScanning { coach.announce(next) }
    }

    private func applyTrackingGuidance(_ frame: ARFrame?) {
        let tracking: SpatialCaptureGuidance.Tracking
        if let frame {
            switch frame.camera.trackingState {
            case .normal: tracking = .normal
            case .notAvailable: tracking = .unavailable
            case .limited(let reason):
                switch reason {
                case .excessiveMotion: tracking = .excessiveMotion
                case .insufficientFeatures: tracking = .insufficientFeatures
                case .initializing: tracking = .initializing
                case .relocalizing: tracking = .relocalizing
                @unknown default: tracking = .unavailable
                }
            @unknown default: tracking = .unavailable
            }
        } else {
            tracking = .unavailable
        }
        let pose: SpatialPose
        if let frame {
            let t = frame.camera.transform
            pose = SpatialPose(time: frame.timestamp,
                position: SpatialPoint(x: t.columns.3.x, y: t.columns.3.y, z: t.columns.3.z),
                forward: SpatialPoint(x: -t.columns.2.x, y: -t.columns.2.y, z: -t.columns.2.z), epoch: epoch)
        } else {
            pose = document.poses.last ?? SpatialPose(time: ProcessInfo.processInfo.systemUptime,
                position: SpatialPoint(x: 0, y: 0, z: 0), forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: epoch)
        }
        applyGuidance(SpatialCaptureGuidance.Observation(time: pose.time, pose: pose, tracking: tracking, planes: []))
    }

    private func guidancePlanes(in frame: ARFrame) -> [SpatialCaptureGuidance.Plane] {
        document.surfaces.map { surface in
            let ys = surface.vertices.map(\.y)
            let height = (ys.max() ?? 0) - (ys.min() ?? 0)
            let count = Float(max(surface.vertices.count, 1))
            let cx = surface.vertices.map(\.x).reduce(0, +) / count
            let cz = surface.vertices.map(\.z).reduce(0, +) / count
            return SpatialCaptureGuidance.Plane(
                id: surface.id,
                kind: surface.kind,
                height: height,
                centroidX: cx,
                centroidZ: cz,
                extent: SpatialScanGeometry.observedBoundarySpan(surface) ?? height,
                inView: worldPointsIntersectCamera(surface.vertices, frame: frame))
        }
    }

    private func planeIntersectsCamera(_ plane: ARPlaneAnchor, frame: ARFrame) -> Bool {
        let vertices = (0..<plane.geometry.boundaryVertices.count).map { index -> SpatialPoint in
            let v = plane.geometry.boundaryVertices[index]
            let world = plane.transform * SIMD4<Float>(v.x, v.y, v.z, 1)
            return SpatialPoint(x: world.x, y: world.y, z: world.z)
        }
        return worldPointsIntersectCamera(vertices, frame: frame)
    }

    private func worldPointsIntersectCamera(_ vertices: [SpatialPoint], frame: ARFrame) -> Bool {
        let worldToCamera = simd_inverse(frame.camera.transform)
        let k = frame.camera.intrinsics
        let pixels = vertices.compactMap { v -> SIMD2<Float>? in
            let p = worldToCamera * SIMD4<Float>(v.x, v.y, v.z, 1)
            guard p.z < -0.05 else { return nil }
            let image = k * SIMD3<Float>(p.x, -p.y, -p.z)
            let pixel = SIMD2<Float>(image.x / image.z, image.y / image.z)
            return pixel.x.isFinite && pixel.y.isFinite ? pixel : nil
        }
        guard pixels.count >= 3 else { return false }
        let width = Float(CVPixelBufferGetWidth(frame.capturedImage))
        let height = Float(CVPixelBufferGetHeight(frame.capturedImage))
        return (pixels.map(\.x).max() ?? -1) >= 0 && (pixels.map(\.x).min() ?? width + 1) <= width
            && (pixels.map(\.y).max() ?? -1) >= 0 && (pixels.map(\.y).min() ?? height + 1) <= height
    }

    /// The Mac Gaussian trainer has no per-image appearance model, so auto exposure and
    /// white balance drift between keyframes is learned as smears. Lock both once they settle.
    private func schedulePhotometricLock() {
        photometricLockTask?.cancel()
        let request = generation
        photometricLockTask = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(1.5))
            guard let self, !Task.isCancelled, self.generation == request, self.isScanning else { return }
            self.setPhotometricLock(true)
        }
    }

    private func setPhotometricLock(_ locked: Bool) {
        defer { if !locked { photometricLocked = false } }
        guard let device = ARWorldTrackingConfiguration.configurableCaptureDeviceForPrimaryCamera else {
            if locked { document.events.append("photometric_lock_unavailable") }
            return
        }
        do { try device.lockForConfiguration() } catch {
            if locked { document.events.append("photometric_lock_failed:\(error.localizedDescription)") }
            return
        }
        defer { device.unlockForConfiguration() }
        let exposure: AVCaptureDevice.ExposureMode = locked ? .locked : .continuousAutoExposure
        let balance: AVCaptureDevice.WhiteBalanceMode = locked ? .locked : .continuousAutoWhiteBalance
        if device.isExposureModeSupported(exposure) { device.exposureMode = exposure }
        if device.isWhiteBalanceModeSupported(balance) { device.whiteBalanceMode = balance }
        guard locked else { return }
        photometricLocked = device.exposureMode == .locked && device.whiteBalanceMode == .locked
        document.events.append(photometricLocked ? "photometric_locked" : "photometric_lock_unsupported")
    }

    private func captureKeyframe(_ frame: ARFrame, pose: SpatialPose) {
        if document.keyframes.count >= 240 || keyframeBytes >= 100 * 1_024 * 1_024 {
            if !keyframeLimitReported {
                keyframeLimitReported = true
                document.events.append("keyframe_limit_reached_remaining_scan_has_no_rgb")
                error = "重建照片达到张数或容量上限；后续只记录轨迹和结构，请结束并保存"
            }
            return
        }
        guard !encoding, frame.timestamp - lastKeyframeTime >= 1, document.keyframes.count < 240 else { return }
        encoding = true
        lastKeyframeTime = frame.timestamp
        let image = frame.capturedImage // One retained source only while this encode runs.
        let k = frame.camera.intrinsics
        let width = CVPixelBufferGetWidth(image), height = CVPixelBufferGetHeight(image)
        let frameID = UUID().uuidString
        let request = archiveGeneration, scanID = document.id
        let intrinsics = [k.columns.0.x,k.columns.0.y,k.columns.0.z,k.columns.1.x,k.columns.1.y,k.columns.1.z,k.columns.2.x,k.columns.2.y,k.columns.2.z]
        let t = frame.camera.transform
        let cameraToWorld = [t.columns.0,t.columns.1,t.columns.2,t.columns.3].flatMap { [$0.x,$0.y,$0.z,$0.w] }
        let display = displayMetadata(for: frame)
        let exposureDuration = frame.camera.exposureDuration, exposureOffset = frame.camera.exposureOffset
        let locked = photometricLocked
        encoderQueue.async { [weak self] in
            // Keep native dimensions/orientation: resizing would require changing K.
            let context = CIContext()
            let jpeg = context.jpegRepresentation(of: CIImage(cvPixelBuffer: image), colorSpace: CGColorSpaceCreateDeviceRGB(), options: [:])
            Task { @MainActor [weak self] in
                guard let self else { return }
                defer { self.encoding = false }
                guard self.archiveGeneration == request, self.document.id == scanID else { return }
                guard let jpeg else {
                    self.pendingKeyframeFailure = "关键帧编码失败"
                    self.document.events.append("keyframe_encode_failed")
                    self.error = "关键帧编码失败；空间结构可继续扫描"
                    return
                }
                guard self.keyframeBytes + jpeg.count <= SpatialScanStore.maximumKeyframeBytes else {
                    self.keyframeBytes = SpatialScanStore.maximumKeyframeBytes
                    self.pendingKeyframeFailure = "关键帧容量不足，最后一张照片未保存"
                    self.document.events.append("keyframe_storage_limit")
                    self.error = "重建照片达到容量上限；后续只记录结构，请保存"
                    return
                }
                do {
                    let name = "\(frameID).jpg"
                    try await self.store.saveKeyframe(data: jpeg, scanID: scanID, filename: name)
                    guard self.archiveGeneration == request, self.document.id == scanID else {
                        if self.document.id != scanID, !self.saved.contains(where: { $0.id == scanID }) {
                            do { try await self.store.delete(id: scanID) }
                            catch { self.error = "未保存照片清理失败：\(error.localizedDescription)" }
                        }
                        return
                    }
                    self.document.keyframes.append(SpatialKeyframe(id: frameID, time: pose.time, pose: pose, intrinsics: intrinsics, width: width, height: height, imageFile: name, cameraToWorld: cameraToWorld,
                        displayOrientation: display?.orientation, displayTransform: display?.transform,
                        displayViewport: display?.viewport,
                        exposureDuration: exposureDuration.isFinite && exposureDuration > 0 ? exposureDuration : nil,
                        exposureOffset: exposureOffset.isFinite ? exposureOffset : nil,
                        photometricLocked: locked))
                    if display == nil { self.document.events.append("keyframe_display_orientation_unknown:\(frameID)") }
                    self.keyframeBytes += jpeg.count
                    self.archiveSavedKeyframe(frameID)
                } catch {
                    self.pendingKeyframeFailure = error.localizedDescription
                    self.document.events.append("keyframe_write_failed")
                    self.error = "关键帧保存失败：\(error.localizedDescription)"
                }
            }
        }
    }

    private func recordFrameState(_ frame: ARFrame) {
        let state: SpatialTrackingEvent.TrackingState
        var reason: SpatialTrackingEvent.TrackingReason?
        switch frame.camera.trackingState {
        case .normal: state = .normal
        case .notAvailable: state = .notAvailable
        case .limited(let value):
            state = .limited
            switch value {
            case .initializing: reason = .initializing
            case .excessiveMotion: reason = .excessiveMotion
            case .insufficientFeatures: reason = .insufficientFeatures
            case .relocalizing: reason = .relocalizing
            @unknown default: reason = .unknown
            }
        }
        let mapping: SpatialTrackingEvent.MappingStatus
        switch frame.worldMappingStatus {
        case .notAvailable: mapping = .notAvailable
        case .limited: mapping = .limited
        case .extending: mapping = .extending
        case .mapped: mapping = .mapped
        @unknown default: mapping = .unknown
        }
        let trackingChanged = state != lastTrackingState || reason != lastTrackingReason
        let mappingChanged = mapping != lastMappingStatus
        lastTrackingState = state
        lastTrackingReason = reason
        lastMappingStatus = mapping
        if trackingChanged { recordTrackingEvent(.trackingChanged, frame: frame) }
        if mappingChanged { recordTrackingEvent(.mappingChanged, frame: frame) }
    }

    private func recordTrackingEvent(_ kind: SpatialTrackingEvent.Kind, frame: ARFrame? = nil,
                                     reason: SpatialTrackingEvent.TrackingReason? = nil,
                                     removedAnchorIDs: [String]? = nil, detail: String? = nil) {
        // Never backfill the historical string-only event list when loading an older scan.
        guard document.trackingEvents != nil else { return }
        document.trackingEvents?.append(SpatialTrackingEvent(kind: kind,
            time: frame?.timestamp ?? ProcessInfo.processInfo.systemUptime,
            timeSource: frame == nil ? .systemUptime : .frame, epoch: epoch,
            trackingState: lastTrackingState, trackingReason: reason ?? lastTrackingReason,
            mappingStatus: lastMappingStatus, removedAnchorIDs: removedAnchorIDs, detail: detail))
    }

    /// Use ARKit's actual image-to-view mapping rather than inferring rotation from image dimensions,
    /// device gravity or a hard-coded camera mounting direction. Ambiguous scenes remain unknown.
    private func displayMetadata(for frame: ARFrame) -> (orientation: String, transform: [Float], viewport: [Float])? {
        let windows = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            .filter { $0.activationState == .foregroundActive }
            .flatMap { $0.windows }.filter { $0.isKeyWindow }
        guard windows.count == 1, let window = windows.first, let scene = window.windowScene,
              window.bounds.width > 0, window.bounds.height > 0 else { return nil }
        let name: String
        switch scene.interfaceOrientation {
        case .portrait: name = "portrait"
        case .portraitUpsideDown: name = "portraitUpsideDown"
        case .landscapeLeft: name = "landscapeLeft"
        case .landscapeRight: name = "landscapeRight"
        default: return nil
        }
        // Apple documents normalized captured-image -> normalized viewport coordinates,
        // including the correct rotation and aspect-fill for this interface orientation.
        let transform = frame.displayTransform(for: scene.interfaceOrientation, viewportSize: window.bounds.size)
        let affine = [transform.a, transform.b, transform.c, transform.d, transform.tx, transform.ty].map { Float($0) }
        guard affine.allSatisfy(\.isFinite) else { return nil }
        return (name, affine, [Float(window.bounds.width), Float(window.bounds.height)])
    }

    func sessionWasInterrupted(_ session: ARSession) {
        guard isScanning else { return }
        restoreEvidence.reset(generation: generation)
        restorePlanes.removeAll(); restoreUpdatedMapAnchorIDs.removeAll()
        recordTrackingEvent(.interrupted, reason: .interrupted)
        // The camera may reset exposure while interrupted; later frames must not claim a lock.
        if photometricLocked { photometricLocked = false; document.events.append("photometric_lock_lost_on_interruption") }
        markTrackingGap("扫描被中断，请结束后重新开始")
    }
    func session(_ session: ARSession, didFailWithError value: Error) {
        if isScanning { recordTrackingEvent(.failed, reason: .failed, detail: value.localizedDescription) }
        stop()
        error = "空间定位失败：\(value.localizedDescription)"
    }
}
