import ARKit
import AVFoundation
import Combine
import CoreImage
import Foundation
import UIKit

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
    @Published var status = "点击开始，移动手机观察墙面和地面"
    @Published var isScanning = false
    @Published private(set) var captureGuidance = SpatialCaptureGuidance.State.start
    private var guidance = SpatialCaptureGuidance()
    @Published var error: String?
    @Published var saved: [SpatialScanDocument] = []
    @Published var exportURL: URL?
    @Published var isSaving = false
    @Published private(set) var isFinishingAndSaving = false
    var onSaved: ((SpatialScanDocument, URL) -> Void)?
    let session = ARSession()
    private let store: SpatialScanStore
    @Published private(set) var isRestoring = false
    @Published private(set) var isCapturingMap = false
    private var committedDocument: SpatialScanDocument?

    var keyframeDirectory: URL { store.scanDirectoryURL(document.id) }

    var hasUnsavedChanges: Bool {
        // Failed startup status/events alone have no captured data to save.
        guard !document.poses.isEmpty || !document.surfaces.isEmpty || !document.keyframes.isEmpty || document.worldMap != nil else { return false }
        if let committedDocument, committedDocument.id == document.id { return document != committedDocument }
        return true
    }

    private func permitReplacement() -> Bool {
        guard !isSaving, !isFinishingAndSaving, !starting, !isCapturingMap else {
            error = "正在处理扫描，请稍候再操作"; return false
        }
        guard !hasUnsavedChanges else {
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
            do { try await store.discardUncommittedDirectories() }
            catch { self.error = "未保存扫描清理失败：\(error.localizedDescription)" }
            await refreshSaved()
        }
    }

    func start() {
        guard permitReplacement() else { return }
        guard !isScanning, !starting, !isCapturingMap, !isSaving, !isFinishingAndSaving else { return }
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
            document = SpatialScanDocument()
            document.trackingEvents = []
            lastTrackingState = nil
            lastTrackingReason = nil
            lastMappingStatus = nil
            epoch += 1
            generation = UUID()
            restoreEvidence.reset(generation: generation)
            lastPoseTime = -.infinity
            guidance.reset()
            captureGuidance = .start
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
            schedulePhotometricLock()
            beginPerformanceMeasurement()
            status = "缓慢移动手机，让墙角和地面进入画面"
            document.events.append("started_rgb_imu_no_depth")
            recordTrackingEvent(.started)
        }
    }

    func stop() {
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
        if encoding && isScanning { document.events.append("pending_keyframe_cancelled_on_stop") }
        generation = UUID() // Invalidates permission requests and pending JPEG completions.
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
            document.events.append("stopped")
            recordTrackingEvent(.stopped)
        }
        isScanning = false
        status = document.poses.isEmpty ? "尚未获得有效定位；可以重新扫描" : "已结束；线框仅表示已观测范围"
    }

    func clear() {
        guard permitReplacement() else { return }
        stop()
        generation = UUID(); restoreEvidence.reset(generation: generation); isCapturingMap = false; pendingMapData = nil
        session.pause()
        let oldID = document.id
        if !saved.contains(where: { $0.id == oldID }) {
            Task {
                do { try await store.delete(id: oldID) }
                catch { self.error = "未保存照片清理失败：\(error.localizedDescription)" }
            }
        }
        document = SpatialScanDocument()
        exportURL = nil
        error = nil
        status = "点击开始，建立新的空间"
    }

    /// One explicit user action ends acquisition and waits for map capture
    /// before committing. Failed map capture does not discard valid RGB/poses.
    func stopAndSave() {
        guard !isSaving, !isFinishingAndSaving else { return }
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
            while self.isCapturingMap && self.generation == request && self.document.id == scanID {
                do { try await Task.sleep(for: .milliseconds(50)) }
                catch { self.isFinishingAndSaving = false; return }
            }
            guard self.generation == request, self.document.id == scanID else {
                self.isFinishingAndSaving = false
                return
            }
            self.isFinishingAndSaving = false
            self.save()
        }
    }

    func save() {
        guard !isSaving else { error = "扫描正在保存，请稍候"; return }
        guard !isCapturingMap else { error = "定位地图正在获取，请稍候；结束并保存会自动完成"; return }
        guard !isFinishingAndSaving else { error = "正在结束并保存，请稍候"; return }
        guard !isScanning, !document.poses.isEmpty else {
            error = "请先结束扫描，并获得有效定位后保存"
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
                do {
                    let url = try await store.export(snapshot)
                    if generation == saveGeneration, document == snapshot { exportURL = url }
                }
                catch { self.error = "扫描已保存，但导出包生成失败：\(error.localizedDescription)" }
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
        guard !isScanning, !starting, !isSaving, !isCapturingMap, !isFinishingAndSaving else { return }
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
                guidance.reset(); error = nil
                document.relocalizationEvents = document.relocalizationEvents ?? []
                document.relocalizationEvents?.append(SpatialRelocalizationEvent(time: ProcessInfo.processInfo.systemUptime, state: "requested", sourceEpoch: reference.referenceEpoch, newEpoch: epoch))
                let config = ARWorldTrackingConfiguration()
                config.worldAlignment = .gravity; config.planeDetection = [.horizontal, .vertical]
                config.initialWorldMap = map
                livePose.clear()
                isRestoring = true; isScanning = true; beginPerformanceMeasurement(); restoreStarted = ProcessInfo.processInfo.systemUptime
                session.run(config, options: [.resetTracking, .removeExistingAnchors])
                schedulePhotometricLock()
                status = "对准原来的墙角，缓慢侧移；正在核对空间"
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
        guard !isSaving, !isFinishingAndSaving, !starting, !isCapturingMap, !(isScanning && value.id == document.id) else {
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
        SpatialScreenPerformanceProbe.shared.recordCallback(source: frame.timestamp, received: receivedAt)
        if isRestoring, !checkRestoration(frame) { livePose.clear(); recordFrameState(frame); return }
        switch frame.camera.trackingState {
        case .normal:
            if interrupted {
                epoch += 1
                interrupted = false
                document.events.append("tracking_recovered_new_segment")
                recordFrameState(frame)
                recordTrackingEvent(.trackingRecovered, frame: frame)
            } else { recordFrameState(frame) }
            let scanningStatus = "正在扫描 · \(document.surfaces.count) 个观测面 · 边界尚需核对"
            if status != scanningStatus { status = scanningStatus }
        case .notAvailable:
            recordFrameState(frame)
            markTrackingGap("定位不可用，请缓慢移动到有纹理的位置", frame: frame)
            return
        case .limited(let reason):
            recordFrameState(frame)
            let message: String
            switch reason {
            case .initializing: message = "正在建立定位，请缓慢横向移动"
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
        guard frame.timestamp - lastPoseTime >= 0.1 else { return }
        lastPoseTime = frame.timestamp
        let nextGuidance = guidance.update(pose)
        if captureGuidance != nextGuidance { captureGuidance = nextGuidance }
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
        livePose.clear()
        if status != message { status = message }
        guidance.reset()
        captureGuidance = .trackingUnavailable
        if !interrupted {
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
        document.events.append("anchors_removed:\(ids.count)")
        recordTrackingEvent(.anchorsRemoved, removedAnchorIDs: ids.sorted())
    }

    private func updatePlanes(_ anchors: [ARAnchor], updated: Bool) {
        guard isScanning else { return }
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
            if let i = document.surfaces.firstIndex(where: { $0.id == surface.id }) { document.surfaces[i] = surface }
            else { document.surfaces.append(surface) }
        }
    }

    private func planeIntersectsCamera(_ plane: ARPlaneAnchor, frame: ARFrame) -> Bool {
        let worldToCamera = simd_inverse(frame.camera.transform)
        let k = frame.camera.intrinsics
        let pixels = plane.geometry.boundaryVertices.compactMap { v -> SIMD2<Float>? in
            let p = worldToCamera * plane.transform * SIMD4<Float>(v.x, v.y, v.z, 1)
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
        let request = generation, scanID = document.id
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
                guard self.generation == request, self.document.id == scanID else { return }
                guard let jpeg else { self.error = "关键帧编码失败；空间结构可继续扫描"; return }
                guard self.keyframeBytes + jpeg.count <= SpatialScanStore.maximumKeyframeBytes else {
                    self.keyframeBytes = SpatialScanStore.maximumKeyframeBytes
                    self.document.events.append("keyframe_storage_limit")
                    self.error = "重建照片达到容量上限；后续只记录结构，请保存"
                    return
                }
                do {
                    let name = "\(frameID).jpg"
                    try await self.store.saveKeyframe(data: jpeg, scanID: scanID, filename: name)
                    guard self.generation == request, self.document.id == scanID else {
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
                } catch { self.error = "关键帧保存失败：\(error.localizedDescription)" }
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


/// Acquisition advice only. AR pose motion does not prove matched features, parallax or depth.
/// This never gates trajectory recording, image capture, persistence or reconstruction evidence.
struct SpatialCaptureGuidance {
    enum State: Equatable {
        case start, turnInPlace, moveSideways, lateralObserved, slowDown, trackingUnavailable, orientationUnavailable
        var message: String {
            switch self {
            case .start: return "对准同一墙角，缓慢向旁边移动"
            case .turnInPlace: return "只转动手机难恢复深度，请缓慢向旁边移动"
            case .moveSideways: return "保持墙角在画面中，缓慢向旁边移动"
            case .lateralObserved: return "已记录侧移，继续缓慢观察相邻区域"
            case .slowDown: return "请放慢，并保持同一墙角在画面中"
            case .trackingUnavailable: return "等待定位恢复，再缓慢向旁边移动"
            case .orientationUnavailable: return "让前方墙角进入画面，再缓慢向旁边移动"
            }
        }
    }
    private var reference: SpatialPose?
    private var previous: SpatialPose?
    private var state: State = .start
    mutating func reset() { reference = nil; previous = nil; state = .start }
    mutating func update(_ pose: SpatialPose) -> State {
        let values = [pose.position.x, pose.position.y, pose.position.z,
                      pose.forward.x, pose.forward.y, pose.forward.z]
        let horizontal = hypot(pose.forward.x, pose.forward.z)
        guard pose.time.isFinite, values.allSatisfy(\.isFinite) else {
            reset(); state = .trackingUnavailable; return state
        }
        guard horizontal > 0.1 else {
            reset(); state = .orientationUnavailable; return state
        }
        guard let origin = reference, let last = previous, origin.epoch == pose.epoch,
              pose.time > last.time, pose.time - last.time <= 1 else {
            reference = pose; previous = pose; state = .start; return state
        }
        previous = pose
        let dt = pose.time - last.time
        let step = SpatialScanGeometry.distance(last.position, pose.position)
        // Large instantaneous pose jumps are not counted as useful baseline.
        if Double(step) / dt > 0.6 {
            reference = pose; state = .slowDown; return state
        }
        let elapsed = pose.time - origin.time
        let norm = hypot(origin.forward.x, origin.forward.z)
        let dx = pose.position.x - origin.position.x, dz = pose.position.z - origin.position.z
        let lateral = abs((-origin.forward.z * dx + origin.forward.x * dz) / norm)
        let translation = SpatialScanGeometry.distance(origin.position, pose.position)
        let dot = max(-1, min(1, (origin.forward.x * pose.forward.x + origin.forward.z * pose.forward.z) / (norm * horizontal)))
        let angle = acos(dot)
        // A bounded five-second window avoids calling old sideways motion current progress.
        if elapsed >= 5 {
            reference = pose; state = .moveSideways; return state
        }
        if angle > .pi / 4 {
            state = translation < 0.08 ? .turnInPlace : .slowDown
        } else if lateral >= 0.2 {
            state = .lateralObserved
        } else if elapsed >= 1, translation < 0.08, angle > .pi / 12 {
            state = .turnInPlace
        } else if elapsed >= 1 {
            state = .moveSideways
        }
        return state
    }
}
