import XCTest
import CryptoKit
import simd
@testable import VQASee

final class SpatialScanTests: XCTestCase {
    func testRestoredLandmarkIdentityDoesNotDiscardFreshObservation() {
        XCTAssertFalse(SpatialMapObservationPolicy.accepts(savedAnchor: true, updated: false,
            trackingNormal: true, elapsed: 2, intersectsCameraView: true), "Cached didAdd must not prove recovery")
        XCTAssertTrue(SpatialMapObservationPolicy.accepts(savedAnchor: true, updated: true,
            trackingNormal: true, elapsed: 2, intersectsCameraView: true), "Fresh update of the same wall must be usable")
        XCTAssertFalse(SpatialMapObservationPolicy.accepts(savedAnchor: true, updated: true,
            trackingNormal: false, elapsed: 2, intersectsCameraView: true))
        XCTAssertFalse(SpatialMapObservationPolicy.accepts(savedAnchor: true, updated: true,
            trackingNormal: true, elapsed: 2, intersectsCameraView: false))
        XCTAssertFalse(SpatialMapObservationPolicy.accepts(savedAnchor: true, updated: true,
            trackingNormal: true, elapsed: 0.1, intersectsCameraView: true))
    }

    @MainActor
    func testControllerSavesPendingMapWithoutAsyncExclusiveAccessConflict() async throws {
        // Storage protocol bytes, not a simulated claim of AR alignment accuracy.
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        let controller = SpatialScanController(store: store)
        controller.document.poses = [pose(1, 0)]
        let archive = Data("pending-map-regression-fixture".utf8)
        controller.stageWorldMapArchive(archive, referenceEpoch: 0, references: [])
        for _ in 0..<2 {
            controller.save()
            let deadline = Date().addingTimeInterval(10)
            while controller.isSaving && Date() < deadline {
                try await Task.sleep(for: .milliseconds(20))
            }
            XCTAssertFalse(controller.isSaving)
            XCTAssertNil(controller.error)
            XCTAssertEqual(controller.document.worldMap?.sha256.count, 64)
            let loaded = try await store.loadWorldMap(controller.document)
            XCTAssertEqual(loaded, archive)
            XCTAssertNotNil(controller.exportURL, "User's complete save/export path must finish")
            let saved = try await store.loadAll()
            XCTAssertEqual(saved.count, 1)
            XCTAssertEqual(saved.first?.worldMap?.sha256, controller.document.worldMap?.sha256)
        }
    }

    func testMapGeometryRequiresIndependentNonparallelSurfacesAndRejectsOffset() {
        let wall = SpatialSurface(id: "wall", kind: "vertical", vertices: [SpatialPoint(x:0,y:0,z:0),SpatialPoint(x:2,y:0,z:0),SpatialPoint(x:2,y:2,z:0),SpatialPoint(x:0,y:2,z:0)])
        let floor = SpatialSurface(id: "floor", kind: "horizontal", vertices: [SpatialPoint(x:0,y:0,z:0),SpatialPoint(x:0,y:0,z:2),SpatialPoint(x:2,y:0,z:2),SpatialPoint(x:2,y:0,z:0)])
        XCTAssertFalse(SpatialMapGeometry.matches([wall], [wall,wall]).sufficient, "Duplicate evidence cannot establish room alignment")
        XCTAssertTrue(SpatialMapGeometry.matches([wall,floor], [wall,floor]).sufficient)
        var offset = wall
        offset.vertices = wall.vertices.map { SpatialPoint(x:$0.x,y:$0.y,z:$0.z+0.2) }
        XCTAssertFalse(SpatialMapGeometry.matches([wall,floor], [offset,floor]).sufficient)
        var remote = wall
        remote.vertices = wall.vertices.map { SpatialPoint(x:$0.x+10,y:$0.y,z:$0.z) }
        XCTAssertFalse(SpatialMapGeometry.matches([wall,floor], [remote,floor]).sufficient)
    }

    func testWorldMapArchiveChecksumRejectsCorruption() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        var document = SpatialScanDocument()
        let bytes = Data("secure archive test payload".utf8)
        let hash = try await store.saveWorldMap(bytes, scanID: document.id)
        document.worldMap = SpatialWorldMapReference(sha256:hash,referenceEpoch:0,capturedAt:1,mappingStatus:"mapped",referenceSurfaces:[])
        let restored = try await store.loadWorldMap(document)
        XCTAssertEqual(restored,bytes)
        try Data("corruption".utf8).write(to:directory.appendingPathComponent(document.id.uuidString).appendingPathComponent("world-map.archive"))
        do { _ = try await store.loadWorldMap(document); XCTFail("Corrupt map must not be restored") } catch { }
    }

    private func pose(_ time: Double, _ x: Float, epoch: Int = 0,
                      forward: SpatialPoint = SpatialPoint(x: 0, y: 0, z: -1)) -> SpatialPose {
        SpatialPose(time: time, position: SpatialPoint(x: x, y: 0, z: 0), forward: forward, epoch: epoch)
    }

    func testCaptureGuidanceHoldsTallWallAndDoesNotRewritePose() {
        var advice = SpatialCaptureGuidance()
        let origin = pose(0, 0)
        XCTAssertEqual(advice.update(observation(time: 0, pose: origin)), .start)
        XCTAssertEqual(advice.update(observation(time: 0.2, pose: origin, planes: [wall(extent: 1.0)])), .holdWall)
        XCTAssertEqual(advice.update(observation(time: 0.6, pose: origin, planes: [wall(extent: 1.0)])), .holdWall)
        XCTAssertEqual(advice.update(observation(time: 2.7, pose: origin, planes: [wall(extent: 1.0)])), .turnLeft)
        XCTAssertEqual(origin.position.x, 0, "Advice must not modify capture evidence")
        XCTAssertEqual(SpatialCaptureGuidance.State.holdWall.message, "停一下")
        XCTAssertFalse(SpatialCaptureGuidance.State.holdWall.message.contains("侧移"))
    }

    func testCaptureGuidanceLocksLeftSweepAndIgnoresOppositeGap() {
        var advice = SpatialCaptureGuidance()
        let origin = looking(.pi, time: 0)
        _ = advice.update(observation(time: 0, pose: origin))
        _ = advice.update(observation(time: 0.2, pose: origin, planes: [wall(extent: 1.0)]))
        _ = advice.update(observation(time: 0.6, pose: origin, planes: [wall(extent: 1.0)]))
        XCTAssertEqual(advice.update(observation(time: 2.7, pose: origin, planes: [wall(extent: 1.0)])), .turnLeft)
        let towardRight = looking(.pi + 0.4, time: 3.2)
        XCTAssertEqual(advice.update(observation(time: 3.2, pose: towardRight, planes: [wall(extent: 1.0)])), .turnLeft,
                       "Nearest-gap chatter must not reverse a locked left sweep")
        XCTAssertEqual(advice.update(observation(time: 3.6, pose: towardRight, tracking: .excessiveMotion)), .trackingLimited(.excessiveMotion))
        XCTAssertEqual(advice.update(observation(time: 4.0, pose: towardRight, planes: [wall(extent: 1.0)])), .turnLeft)
        XCTAssertEqual(advice.update(observation(time: 4.4, pose: towardRight, planes: [wall(id: "sofa", height: 0.6)])), .avoidFurniture)
        XCTAssertEqual(advice.update(observation(time: 4.8, pose: towardRight)), .turnLeft)
        XCTAssertTrue(advice.completionMessage().contains("继续沿墙转完一圈"))
        XCTAssertFalse(advice.completionMessage().contains("房间已经闭合"))
    }

    func testCaptureGuidanceCompletesCircuitWithoutReversing() {
        var advice = SpatialCaptureGuidance()
        var time = 0.0
        func feed(_ yaw: Float, planes: [SpatialCaptureGuidance.Plane] = []) -> SpatialCaptureGuidance.State {
            time += 0.4
            return advice.update(observation(time: time, pose: looking(yaw, time: time), planes: planes))
        }
        func settle(_ yaw: Float, id: String) {
            let plane = wall(id: id, extent: 1.0)
            XCTAssertEqual(feed(yaw, planes: [plane]), .holdWall)
            XCTAssertEqual(feed(yaw, planes: [plane]), .holdWall)
            time += 2.2
            let next = advice.update(observation(time: time, pose: looking(yaw, time: time), planes: [plane]))
            XCTAssertTrue(next == .turnLeft || next == .circuitComplete, "\(next)")
        }
        _ = advice.update(observation(time: 0, pose: looking(.pi, time: 0)))
        settle(.pi, id: "a")
        var yaw = Float.pi
        while yaw > Float.pi / 2 + 0.05 {
            yaw -= 0.3
            XCTAssertEqual(feed(yaw), .turnLeft)
        }
        settle(.pi / 2, id: "b")
        yaw = Float.pi / 2
        var sawComplete = false
        while yaw > -.pi - 0.05 {
            yaw -= 0.3
            let next = feed(yaw)
            XCTAssertNotEqual(next, .turnRight, "Circuit sweep must stay one way")
            if next == .circuitComplete { sawComplete = true }
        }
        XCTAssertTrue(sawComplete, "Returning to the first heading after ~300° must stop the left-turn nag")
        XCTAssertTrue(advice.completionMessage().contains("转过一圈"))
        XCTAssertTrue(advice.completionMessage().contains("还没看全"))
        XCTAssertFalse(advice.completionMessage().contains("可以结束"))
        XCTAssertEqual(SpatialCaptureGuidance.State.circuitComplete.message.contains("还没看全"), true)
        XCTAssertFalse(SpatialCaptureGuidance.State.circuitComplete.message.contains("可以结束"))
    }

    func testCaptureGuidanceSpeedsTrackingAndFurnitureBeforeSweep() {
        var advice = SpatialCaptureGuidance()
        let origin = pose(0, 0)
        XCTAssertEqual(advice.update(observation(time: 0, pose: origin)), .start)
        XCTAssertEqual(advice.update(observation(time: 0.1, pose: pose(0.1, 1))), .slowDown)
        advice.reset()
        _ = advice.update(observation(time: 0, pose: origin))
        XCTAssertEqual(advice.update(observation(time: 0.1, pose: pose(0.1, 0, forward: SpatialPoint(x: 1, y: 0, z: 0)))), .slowDown)
        advice.reset()
        _ = advice.update(observation(time: 0, pose: origin))
        XCTAssertEqual(advice.update(observation(time: 0.2, pose: pose(0.2, 0, epoch: 1))), .start)
        XCTAssertEqual(advice.update(observation(time: 0.3, pose: pose(0.3, .nan))), .trackingUnavailable)
        advice.reset()
        _ = advice.update(observation(time: 0, pose: origin))
        XCTAssertEqual(advice.update(observation(time: 0.2, pose: origin, planes: [wall(id: "sofa", height: 0.6, extent: 1.2)])), .start,
                       "Furniture before any settled wall should not start a chase")
    }

    @MainActor
    func testCaptureGuidanceMuteKeyDefaultsOff() {
        XCTAssertEqual(SpatialScanCoachSpeech.mutedKey, "spatial.scanCoach.muted")
        XCTAssertEqual(SpatialCaptureGuidance.State.avoidFurniture.eventName, "guidance:avoidFurniture")
        XCTAssertEqual(SpatialCaptureGuidance.State.trackingLimited(.insufficientFeatures).eventName,
                       "guidance:trackingLimited:insufficientFeatures")
    }

    private func observation(time: Double, pose: SpatialPose? = nil, tracking: SpatialCaptureGuidance.Tracking = .normal,
                             planes: [SpatialCaptureGuidance.Plane] = []) -> SpatialCaptureGuidance.Observation {
        SpatialCaptureGuidance.Observation(time: time, pose: pose ?? self.pose(time, 0), tracking: tracking, planes: planes)
    }

    private func wall(id: String = "wall", height: Float = 2.2, extent: Float = 1.0,
                      kind: String = "vertical", x: Float = 0, z: Float = -2,
                      inView: Bool = true) -> SpatialCaptureGuidance.Plane {
        SpatialCaptureGuidance.Plane(id: id, kind: kind, height: height, centroidX: x, centroidZ: z, extent: extent, inView: inView)
    }

    private func looking(_ yaw: Float, time: Double) -> SpatialPose {
        SpatialPose(time: time, position: SpatialPoint(x: 0, y: 0, z: 0),
                    forward: SpatialPoint(x: sin(yaw), y: 0, z: cos(yaw)), epoch: 0)
    }

    func testDistanceUsesAllThreeAxes() {
        XCTAssertEqual(SpatialScanGeometry.distance(SpatialPoint(x: 0, y: 0, z: 0),
                                                   SpatialPoint(x: 2, y: 3, z: 6)), 7, accuracy: 0.00001)
    }

    func testSamplingTranslationRotationTimeAndTrackingReset() {
        let previous = pose(0, 0)
        XCTAssertFalse(SpatialScanGeometry.shouldSample(previous: previous, candidate: pose(0.1, 0.02)))
        XCTAssertTrue(SpatialScanGeometry.shouldSample(previous: previous, candidate: pose(0.1, 0.2)))
        XCTAssertTrue(SpatialScanGeometry.shouldSample(previous: previous, candidate: pose(0.1, 0,
            forward: SpatialPoint(x: 1, y: 0, z: 0))))
        XCTAssertTrue(SpatialScanGeometry.shouldSample(previous: previous, candidate: pose(1, 0)))
        XCTAssertTrue(SpatialScanGeometry.shouldSample(previous: previous, candidate: pose(0.1, 0, epoch: 1)))
        XCTAssertFalse(SpatialScanGeometry.shouldSample(previous: previous, candidate: pose(-1, 1)))
        XCTAssertFalse(SpatialScanGeometry.shouldSample(previous: previous, candidate: pose(1, .nan)))
    }

    func testPolylineNeverBridgesResetGapJumpOrNonfinitePose() {
        let poses = [pose(0, 0), pose(0.5, 0.5), pose(1, 1, epoch: 1),
                     pose(4, 1, epoch: 1), pose(4.1, 10, epoch: 1),
                     pose(4.2, .nan, epoch: 1), pose(4.3, 10, epoch: 1)]
        XCTAssertEqual(SpatialScanGeometry.trajectorySegments(poses).map(\.count), [2, 1, 1, 1, 1])
    }

    func testObservedSpanDoesNotCloseOrInventBoundary() {
        let vertices = [SpatialPoint(x: 0, y: 0, z: 0), SpatialPoint(x: 3, y: 0, z: 0),
                        SpatialPoint(x: 3, y: 0, z: 4)]
        let surface = SpatialSurface(id: "partial", kind: "wall", vertices: vertices)
        XCTAssertEqual(SpatialScanGeometry.observedBoundarySpan(surface)!, 5, accuracy: 0.00001)
        XCTAssertEqual(surface.vertices, vertices)
        XCTAssertNil(SpatialScanGeometry.observedBoundarySpan(SpatialSurface(id: "unknown", kind: "wall", vertices: vertices, observed: false)))
    }

    func testProtectedRoundTripDeletionAndCorruption() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        let fresh = try await store.loadAll()
        XCTAssertTrue(fresh.isEmpty)
        let document = SpatialScanDocument(id: UUID(), createdAt: Date(), poses: [pose(0, 0)],
            surfaces: [SpatialSurface(id: "open", kind: "wall", vertices: [SpatialPoint(x: 0, y: 0, z: 0), SpatialPoint(x: 1, y: 0, z: 0)])], events: ["tracking_reset"])
        try await store.save(document)
        let loaded = try await store.loadAll()
        let expected = try await document.withPersistedStructure()
        XCTAssertEqual(loaded, [expected])
        XCTAssertEqual(try directory.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup, true)
        let archive = directory.appendingPathComponent(document.id.uuidString).appendingPathComponent("document.json")
#if !targetEnvironment(simulator)
        XCTAssertEqual(try FileManager.default.attributesOfItem(atPath: archive.path)[.protectionKey] as? FileProtectionType, .complete)
#endif
        var invalid = document
        invalid.schemaVersion = 2
        do { try await store.save(invalid); XCTFail("Invalid replacement must fail") }
        catch SpatialScanStore.StoreError.unsupportedVersion(2) { }
        let preserved = try await store.loadAll()
        XCTAssertEqual(preserved, [expected])
        do { try await store.saveKeyframe(data: Data([1]), scanID: document.id, filename: "../escape.jpg"); XCTFail("Must reject traversal") }
        catch SpatialScanStore.StoreError.invalidFilename { }
        try Data("broken".utf8).write(to: archive)
        do { _ = try await store.loadAll(); XCTFail("Corruption must remain visible") }
        catch SpatialScanStore.StoreError.corruptArchive { }
        try await store.remove(document)
        let removed = try await store.loadAll()
        XCTAssertTrue(removed.isEmpty)
    }
    func testKeyframeValidationExportAndOrphanRecovery() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        let camera = pose(12, 0)
        let frame = SpatialKeyframe(id: "frame", time: 12, pose: camera,
            intrinsics: [100, 0, 0, 0, 100, 0, 10, 10, 1], width: 20, height: 20,
            imageFile: "frame.jpg", cameraToWorld: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])
        var document = SpatialScanDocument(poses: [camera], keyframes: [frame])
        do { try await store.save(document); XCTFail("Missing photo must fail") }
        catch SpatialScanStore.StoreError.missingKeyframe("frame.jpg") { }
        // Persistence preserves opaque image bytes. Camera image encoding is an integration concern.
        let bytes = Data([0xff, 0xd8, 0xff, 0xd9])
        try await store.saveKeyframe(data: bytes, scanID: document.id, filename: "frame.jpg")
        try await store.save(document)
        let archive = try await store.export(document)
        defer { try? FileManager.default.removeItem(at: archive) }
        let zip = try Data(contentsOf: archive)
        XCTAssertEqual(Array(zip.prefix(4)), [0x50,0x4b,0x03,0x04])
        XCTAssertNotNil(zip.range(of: Data("manifest.json".utf8)))
        XCTAssertNotNil(zip.range(of: Data("document.json".utf8)))
        XCTAssertNotNil(zip.range(of: Data("frame.jpg".utf8)))
        XCTAssertNotNil(zip.range(of: bytes))
        let protectedResources = try archive.resourceValues(forKeys: [.isExcludedFromBackupKey])
        XCTAssertEqual(protectedResources.isExcludedFromBackup, true)
        document.keyframes[0].cameraToWorld = [1, 2]
        do { try await store.save(document); XCTFail("Incomplete camera matrix must fail") }
        catch SpatialScanStore.StoreError.invalidDocument { }
        document.keyframes[0] = frame
        document.keyframes[0].time = 13
        do { try await store.save(document); XCTFail("Mismatched camera time must fail") }
        catch SpatialScanStore.StoreError.invalidDocument { }
        let orphan = UUID(), active = UUID()
        try await store.saveKeyframe(data: bytes, scanID: orphan, filename: "orphan.jpg")
        try await store.saveKeyframe(data: bytes, scanID: active, filename: "active.jpg")
        try await store.discardUncommittedDirectories(keeping: [active])
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.appendingPathComponent(orphan.uuidString).path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: directory.appendingPathComponent(active.uuidString).path))
        let retained = try await store.loadAll()
        XCTAssertEqual(retained.first?.keyframes, [frame])
    }

    func testExposureMetadataRoundTripsAndLegacyFramesStayUnknown() throws {
        // A scan saved before exposure recording has no such keys at all.
        let legacyJSON = #"{"id":"f","time":3,"pose":{"time":3,"position":{"x":0,"y":0,"z":0},"forward":{"x":0,"y":0,"z":-1},"epoch":1},"intrinsics":[100,0,0,0,100,0,10,10,1],"width":20,"height":20,"imageFile":"f.jpg","cameraToWorld":[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]}"#
        let legacy = try JSONDecoder().decode(SpatialKeyframe.self, from: Data(legacyJSON.utf8))
        XCTAssertNil(legacy.photometricLocked)
        XCTAssertNil(legacy.exposureDuration)
        XCTAssertNil(legacy.exposureOffset)
        var frame = legacy
        frame.exposureDuration = 1.0 / 60.0
        frame.exposureOffset = -0.3
        frame.photometricLocked = true
        let restored = try JSONDecoder().decode(SpatialKeyframe.self, from: JSONEncoder().encode(frame))
        XCTAssertEqual(restored, frame)
        XCTAssertEqual(restored.photometricLocked, true)
    }

    func testOptionalDisplayMetadataPreservesNativeImageGeometryAndLegacyUnknown() throws {
        let camera = pose(12, 0)
        let nativeK: [Float] = [100,0,0,0,100,0,10,10,1]
        let nativeT: [Float] = [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]
        var frame = SpatialKeyframe(id: "frame", time: 12, pose: camera, intrinsics: nativeK,
            width: 1920, height: 1440, imageFile: "frame.jpg", cameraToWorld: nativeT)
        let legacyData = try JSONEncoder().encode(frame)
        let legacy = try JSONDecoder().decode(SpatialKeyframe.self, from: legacyData)
        XCTAssertFalse(legacy.hasKnownDisplayOrientation)
        XCTAssertTrue(legacy.hasValidDisplayMetadata)
        XCTAssertNil(legacy.displayOrientation)
        frame.displayOrientation = "portrait"
        frame.displayTransform = [0,1,-1,0,1,0]
        frame.displayViewport = [390,844]
        let restored = try JSONDecoder().decode(SpatialKeyframe.self, from: JSONEncoder().encode(frame))
        XCTAssertEqual(restored, frame)
        XCTAssertTrue(restored.hasKnownDisplayOrientation)
        XCTAssertTrue(restored.hasValidDisplayMetadata)
        XCTAssertEqual(restored.intrinsics, nativeK)
        XCTAssertEqual(restored.cameraToWorld, nativeT)
        XCTAssertEqual(restored.width, 1920)
        XCTAssertEqual(restored.height, 1440)
        // The affine convention x'=a*x+c*y+tx, y'=b*x+d*y+ty is presentation only.
        let m = try XCTUnwrap(restored.displayTransform)
        XCTAssertEqual(m[0]*0 + m[2]*0 + m[4], 1)
        XCTAssertEqual(m[1]*0 + m[3]*0 + m[5], 0)
        frame.displayTransform = nil
        XCTAssertFalse(frame.hasValidDisplayMetadata)
        frame.displayTransform = [0,0,0,0,0,0]
        XCTAssertFalse(frame.hasValidDisplayMetadata)
        frame.displayTransform = [0,1,-1,0,1,0]
        frame.displayViewport = [0,844]
        XCTAssertFalse(frame.hasValidDisplayMetadata)
        frame.displayViewport = [390,844]
        frame.displayOrientation = "inferredPortrait"
        XCTAssertFalse(frame.hasValidDisplayMetadata)
    }

    func testTimestampedTrackingEvidenceRoundTripAndLegacyUnknown() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        var document = SpatialScanDocument(events: ["tracking_gap:定位不可用", "tracking_recovered_new_segment"])
        let legacy = try JSONDecoder().decode(SpatialScanDocument.self, from: JSONEncoder().encode(document))
        XCTAssertNil(legacy.trackingEvents, "Old strings have no trustworthy event capture times")
        XCTAssertEqual(legacy.events, document.events)
        let evidence = [
            SpatialTrackingEvent(kind: .trackingGap, time: 10, timeSource: .frame, epoch: 1,
                trackingState: .limited, trackingReason: .insufficientFeatures, mappingStatus: .limited),
            SpatialTrackingEvent(kind: .trackingChanged, time: 11, timeSource: .frame, epoch: 2,
                trackingState: .normal, mappingStatus: .extending),
            SpatialTrackingEvent(kind: .trackingRecovered, time: 11, timeSource: .frame, epoch: 2,
                trackingState: .normal, mappingStatus: .extending),
            SpatialTrackingEvent(kind: .anchorsRemoved, time: 11.3, timeSource: .systemUptime, epoch: 2,
                trackingState: .normal, mappingStatus: .extending, removedAnchorIDs: ["removed-plane"]),
            SpatialTrackingEvent(kind: .stopped, time: 12.2, timeSource: .systemUptime, epoch: 2,
                trackingState: .normal, mappingStatus: .extending)
        ]
        document.trackingEvents = evidence
        try await store.save(document)
        let loaded = try await store.loadAll()
        XCTAssertEqual(loaded.first?.trackingEvents, evidence)
        XCTAssertEqual(loaded.first?.events, legacy.events)
        XCTAssertEqual(loaded.first?.trackingEvents?[3].removedAnchorIDs, ["removed-plane"])
        document.trackingEvents?[0].time = -1
        do { try await store.save(document); XCTFail("Invalid timestamps must not overwrite evidence") }
        catch SpatialScanStore.StoreError.invalidDocument { }
        let preserved = try await store.loadAll()
        XCTAssertEqual(preserved.first?.trackingEvents, evidence)
    }

}

final class SpatialRestorationUpdateEvidenceTests: XCTestCase {
    private func updatePair(_ evidence: inout SpatialRestorationUpdateEvidence, time: Double,
                            anchors: [UUID], generation: UUID) {
        for anchor in anchors { evidence.recordUpdate(timestamp: time, anchorID: anchor, generation: generation) }
    }
    func testCachedPlaneChecksAndSameTimestampCannotAdvanceStability() {
        let generation = UUID(), anchors = [UUID(), UUID()]
        var evidence = SpatialRestorationUpdateEvidence(generation: generation)
        updatePair(&evidence, time: 1, anchors: anchors, generation: generation)
        XCTAssertTrue(evidence.consume(sufficient: true, generation: generation))
        for _ in 0..<20 {
            updatePair(&evidence, time: 1, anchors: anchors, generation: generation)
            XCTAssertFalse(evidence.consume(sufficient: true, generation: generation))
        }
        XCTAssertEqual(evidence.stableCheckCount, 1)
        XCTAssertEqual(evidence.updateBatchCount, 1)
    }
    func testTenFreshUpdatesRequiredAndFailureResetsConsecutiveCount() {
        let generation = UUID(), anchors = [UUID(), UUID()]
        var evidence = SpatialRestorationUpdateEvidence(generation: generation)
        for time in 1...9 {
            updatePair(&evidence, time: Double(time), anchors: anchors, generation: generation)
            XCTAssertTrue(evidence.consume(sufficient: true, generation: generation))
        }
        XCTAssertEqual(evidence.stableCheckCount, 9)
        updatePair(&evidence, time: 10, anchors: anchors, generation: generation)
        XCTAssertTrue(evidence.consume(sufficient: false, generation: generation))
        XCTAssertEqual(evidence.stableCheckCount, 0)
        for time in 11...20 {
            updatePair(&evidence, time: Double(time), anchors: anchors, generation: generation)
            _ = evidence.consume(sufficient: true, generation: generation)
        }
        XCTAssertEqual(evidence.stableCheckCount, 10)
    }
    func testNewGenerationDropsCountsAndRejectsOldGenerationEvidence() {
        let old = UUID(), new = UUID(), anchors = [UUID(), UUID()]
        var evidence = SpatialRestorationUpdateEvidence(generation: old)
        updatePair(&evidence, time: 100, anchors: anchors, generation: old)
        _ = evidence.consume(sufficient: true, generation: old)
        evidence.reset(generation: new)
        XCTAssertEqual(evidence.updateBatchCount, 0)
        XCTAssertEqual(evidence.consumedBatchCount, 0)
        XCTAssertEqual(evidence.stableCheckCount, 0)
        XCTAssertTrue(evidence.freshAnchorIDs.isEmpty)
        XCTAssertNil(evidence.lastUpdateTimestamp)
        updatePair(&evidence, time: 101, anchors: anchors, generation: old)
        XCTAssertFalse(evidence.consume(sufficient: true, generation: old))
        updatePair(&evidence, time: 1, anchors: anchors, generation: new)
        XCTAssertTrue(evidence.consume(sufficient: true, generation: new))
    }
    func testSingleNewPlaneCannotReuseTheOtherCachedPlane() {
        let generation = UUID(), wall = UUID(), floor = UUID()
        var evidence = SpatialRestorationUpdateEvidence(generation: generation)
        updatePair(&evidence, time: 1, anchors: [wall, floor], generation: generation)
        XCTAssertTrue(evidence.consume(sufficient: true, generation: generation))
        evidence.recordUpdate(timestamp: 2, anchorID: wall, generation: generation)
        XCTAssertEqual(evidence.freshAnchorIDs, [wall])
        XCTAssertFalse(evidence.consume(sufficient: true, generation: generation))
        XCTAssertEqual(evidence.stableCheckCount, 1)
        // The other accepted callback may arrive in the same frame or a later one.
        evidence.recordUpdate(timestamp: 2, anchorID: floor, generation: generation)
        XCTAssertTrue(evidence.consume(sufficient: true, generation: generation))
        XCTAssertEqual(evidence.stableCheckCount, 2)
        XCTAssertTrue(evidence.freshAnchorIDs.isEmpty)
    }
}

final class SpatialFinishAndSaveTests: XCTestCase {
    @MainActor
    func testFinishCommitsRestorationEventsAndNewEpochWithoutSecondSaveTap() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        let controller = SpatialScanController(store: store)
        controller.document.poses = [SpatialPose(time: 1, position: SpatialPoint(x: 0, y: 0, z: 0),
            forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 2)]
        controller.stageWorldMapArchive(Data("storage-only-map-fixture".utf8), referenceEpoch: 2, references: [])
        controller.document.poses.append(SpatialPose(time: 2, position: SpatialPoint(x: 0.1, y: 0, z: 0),
            forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 3))
        controller.document.relocalizationEvents = [SpatialRelocalizationEvent(time: 1.5,
            state: "geometryConsistent", sourceEpoch: 2, newEpoch: 3,
            detail: "Storage regression only, not proof of physical alignment")]
        controller.stopAndSave()
        let deadline = Date().addingTimeInterval(10)
        while (controller.isFinishingAndSaving || controller.isSaving) && Date() < deadline {
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertFalse(controller.isFinishingAndSaving)
        XCTAssertFalse(controller.isSaving)
        XCTAssertNil(controller.error)
        let committed = try await store.loadAll()
        XCTAssertEqual(committed.count, 1)
        XCTAssertEqual(committed.first?.poses.map(\.epoch), [2, 3])
        XCTAssertEqual(committed.first?.relocalizationEvents?.last?.newEpoch, 3)
        XCTAssertEqual(committed.first?.worldMap?.referenceEpoch, 2)
        XCTAssertNotNil(controller.exportURL)
    }

    @MainActor
    func testFinishWithoutMapSavesValidPoseAndExposesMissingRelocalizationMap() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        let controller = SpatialScanController(store: store)
        controller.document.poses = [SpatialPose(time: 1, position: SpatialPoint(x: 0, y: 0, z: 0),
            forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 0)]
        controller.stopAndSave()
        let deadline = Date().addingTimeInterval(10)
        while (controller.isFinishingAndSaving || controller.isSaving) && Date() < deadline {
            try await Task.sleep(for: .milliseconds(20))
        }
        let committed = try await store.loadAll()
        XCTAssertEqual(committed.count, 1)
        XCTAssertNil(committed.first?.worldMap)
        XCTAssertTrue(committed.first?.events.contains("saved_without_relocalization_map") == true)
    }

    @MainActor
    func testChangingDocumentCancelsOldFinishIntentRatherThanSavingReplacement() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        let controller = SpatialScanController(store: store)
        controller.document.poses = [SpatialPose(time: 1, position: SpatialPoint(x: 0, y: 0, z: 0),
            forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 0)]
        controller.stopAndSave()
        controller.document = SpatialScanDocument()
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertFalse(controller.isFinishingAndSaving)
        let committed = try await store.loadAll()
        XCTAssertTrue(committed.isEmpty)
    }
}

final class SpatialUnsavedHistoryTests: XCTestCase {
    @MainActor
    func testFailedStartupWithoutCapturedDataCanBeClearedForRetry() {
        let controller = SpatialScanController(store: SpatialScanStore(directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)))
        controller.document.events.append("tracking_unavailable")
        let failedID = controller.document.id
        XCTAssertFalse(controller.hasUnsavedChanges)
        controller.clear()
        XCTAssertNotEqual(controller.document.id, failedID)
        XCTAssertTrue(controller.document.events.isEmpty)
        XCTAssertNil(controller.error)
    }

    @MainActor
    func testSameIDHistoryCannotOverwriteRestoredEpochAndOtherReplacementEntrancesAreBlocked() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        var original = SpatialScanDocument()
        original.poses = [SpatialPose(time: 1, position: SpatialPoint(x: 0, y: 0, z: 0), forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 2)]
        try await store.save(original)
        let controller = SpatialScanController(store: store)
        controller.load(original)
        XCTAssertFalse(controller.hasUnsavedChanges)
        controller.document.poses.append(SpatialPose(time: 2, position: SpatialPoint(x: 0.1, y: 0, z: 0), forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 3))
        controller.document.relocalizationEvents = [SpatialRelocalizationEvent(time: 1.5, state: "geometryConsistent", sourceEpoch: 2, newEpoch: 3, detail: "Storage regression, not physical alignment truth")]
        let appended = controller.document
        XCTAssertTrue(controller.hasUnsavedChanges)
        controller.load(original)
        XCTAssertEqual(controller.document, appended)
        controller.load(SpatialScanDocument())
        controller.start()
        XCTAssertEqual(controller.document, appended)
        XCTAssertEqual(controller.error, "本次新增内容尚未保存，请先结束并保存")
        controller.remove(original)
        XCTAssertEqual(controller.document, appended)
        try await Task.sleep(for: .milliseconds(100))
        let beforeSave = try await store.loadAll()
        let expectedOriginal = try await original.withPersistedStructure()
        XCTAssertEqual(beforeSave.first, expectedOriginal)
        controller.save()
        let deadline = Date().addingTimeInterval(10)
        while controller.isSaving && Date() < deadline { try await Task.sleep(for: .milliseconds(20)) }
        let committed = try await store.loadAll()
        XCTAssertEqual(committed.first?.poses.map(\.epoch), [2, 3])
        XCTAssertEqual(committed.first?.relocalizationEvents?.last?.newEpoch, 3)
        XCTAssertFalse(controller.hasUnsavedChanges)
    }

    @MainActor
    func testConfirmedClearDiscardsUnsavedCurrentAndKeepsSavedHistory() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        var original = SpatialScanDocument()
        original.poses = [SpatialPose(time: 1, position: SpatialPoint(x: 0, y: 0, z: 0), forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 2)]
        try await store.save(original)
        let controller = SpatialScanController(store: store)
        controller.load(original)
        controller.document.poses.append(SpatialPose(time: 2, position: SpatialPoint(x: 0.1, y: 0, z: 0), forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 3))
        XCTAssertTrue(controller.hasUnsavedChanges)
        let unsavedID = controller.document.id
        controller.clear()
        XCTAssertNotEqual(controller.document.id, unsavedID)
        XCTAssertTrue(controller.document.poses.isEmpty)
        XCTAssertTrue(controller.document.surfaces.isEmpty)
        XCTAssertFalse(controller.hasUnsavedChanges)
        XCTAssertNil(controller.error)
        XCTAssertEqual(controller.status, "点击开始，建立新的空间")
        try await Task.sleep(for: .milliseconds(100))
        let kept = try await store.loadAll()
        XCTAssertEqual(kept.first?.id, original.id)
        XCTAssertEqual(kept.first?.poses.map(\.epoch), [2])
    }

    @MainActor
    func testConfirmedStartReplacesUnsavedSessionInsteadOfNoOp() {
        let controller = SpatialScanController(store: SpatialScanStore(directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)))
        controller.document.poses = [SpatialPose(time: 1, position: SpatialPoint(x: 0, y: 0, z: 0), forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 0)]
        XCTAssertTrue(controller.hasUnsavedChanges)
        controller.start()
        XCTAssertEqual(controller.document.poses.count, 1)
        XCTAssertEqual(controller.error, "本次新增内容尚未保存，请先结束并保存")
        controller.error = nil
        controller.start(discardUnsaved: true)
        XCTAssertNotEqual(controller.error, "本次新增内容尚未保存，请先结束并保存")
        XCTAssertTrue(["准备相机", "相机空间定位不可用"].contains(controller.status), controller.status)
    }

    @MainActor
    func testSaveSnapshotDoesNotOverwriteSameIDMutationDuringAwait() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        let controller = SpatialScanController(store: store)
        controller.document.poses = [SpatialPose(time: 1, position: SpatialPoint(x: 0, y: 0, z: 0), forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 2)]
        controller.save()
        controller.document.events.append("new_evidence_after_save_requested")
        let changed = controller.document
        let deadline = Date().addingTimeInterval(10)
        while controller.isSaving && Date() < deadline { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertEqual(controller.document, changed)
        XCTAssertTrue(controller.hasUnsavedChanges)
        XCTAssertTrue(controller.error?.contains("再次保存") == true)
        let persisted = try await store.loadAll()
        XCTAssertFalse(persisted.first?.events.contains("new_evidence_after_save_requested") == true)
    }
}

final class SpatialRestorationExportTests: XCTestCase {
    private func storedEntries(_ zip: Data) throws -> [String: Data] {
        let b = [UInt8](zip)
        func u16(_ i: Int) -> Int { Int(b[i]) | Int(b[i+1]) << 8 }
        func u32(_ i: Int) -> Int { u16(i) | u16(i+2) << 16 }
        var offset = 0, result: [String: Data] = [:]
        while offset + 30 <= b.count && u32(offset) == 0x04034b50 {
            XCTAssertEqual(u16(offset+8), 0, "Real export uses stored ZIP entries")
            let size = u32(offset+18), nameLength = u16(offset+26), extraLength = u16(offset+28)
            let dataStart = offset+30+nameLength+extraLength
            guard dataStart+size <= b.count, let name = String(bytes: b[(offset+30)..<(offset+30+nameLength)], encoding: .utf8) else { throw SpatialScanStore.StoreError.corruptArchive }
            result[name] = Data(b[dataStart..<(dataStart+size)])
            offset = dataStart+size
        }
        return result
    }

    func testOptionalDiagnosticIsChecksummedButNeverMergedIntoCommittedEpochOrEvents() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        var document = SpatialScanDocument()
        document.poses = [SpatialPose(time: 1, position: SpatialPoint(x: 0, y: 0, z: 0), forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 2)]
        try await store.save(document)
        let legacyURL = try await store.export(document)
        let legacy = try storedEntries(Data(contentsOf: legacyURL))
        XCTAssertNil(legacy["restoration-diagnostic.json"])
        let diagnostic = Data(#"{"schemaVersion":2,"time":2.0,"state":"geometryConsistent","status":"内部几何一致","tracking":"normal","candidatePlaneCount":4,"restoredAnchorCount":22,"updatedSavedAnchorCount":4,"stableCheckCount":10,"planeUpdateBatchCount":30,"consumedPlaneUpdateBatchCount":30,"lastPlaneUpdateTimestamp":1.9,"freshPlaneUpdateCount":0,"lastConsumedPlaneUpdateTimestamp":1.9,"events":[{"time":2.0,"state":"geometryConsistent","sourceEpoch":2,"newEpoch":3,"detail":"Storage evidence, not independent accuracy"}]}"#.utf8)
        try await store.saveRestorationDiagnostic(diagnostic, scanID: document.id)
        let url = try await store.export(document)
        let entries = try storedEntries(Data(contentsOf: url))
        XCTAssertEqual(entries["restoration-diagnostic.json"], diagnostic)
        XCTAssertEqual(entries["document.json"], legacy["document.json"])
        let persisted = try await store.loadAll()
        XCTAssertEqual(persisted.first?.poses.map(\.epoch), [2])
        XCTAssertTrue((persisted.first?.relocalizationEvents ?? []).isEmpty)
        let manifest = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(entries["manifest.json"])) as? [String: Any])
        let checksums = try XCTUnwrap(manifest["checksumsSHA256"] as? [String: String])
        XCTAssertEqual(Set(checksums.keys), Set(entries.keys).subtracting(["manifest.json"]))
        XCTAssertEqual(checksums["restoration-diagnostic.json"], SHA256.hash(data: diagnostic).map { String(format: "%02x", $0) }.joined())
    }

    func testCorruptOrOversizedDiagnosticExportFailsVisiblyWithoutChangingDocument() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        let document = SpatialScanDocument()
        try await store.save(document)
        try await store.saveRestorationDiagnostic(Data("invalid JSON".utf8), scanID: document.id)
        do { _ = try await store.export(document); XCTFail("Corrupt diagnostic must not be omitted") } catch { }
        let path = directory.appendingPathComponent(document.id.uuidString).appendingPathComponent("restoration-diagnostic.json")
        try Data(repeating: 32, count: 64*1_024+1).write(to: path)
        do { _ = try await store.export(document); XCTFail("Oversized diagnostic must not be omitted") } catch { }
        let persisted = try await store.loadAll()
        let expected = try await document.withPersistedStructure()
        XCTAssertEqual(persisted.first, expected)
    }
}

final class SpatialArchiveBudgetTests: XCTestCase {
    func testCompleteStoredZIPBudgetCountsBothUTF8NamesAndCentralDirectory() throws {
        let name = "照片.jpg", overhead = 22 + 76 + 2 * name.utf8.count
        XCTAssertEqual(try SpatialScanStore.archiveByteCount([(name: name, size: 165_218_657)]), 165_218_657 + overhead)
        let exactSize = SpatialScanStore.maximumArchiveBytes - overhead
        XCTAssertEqual(try SpatialScanStore.archiveByteCount([(name: name, size: exactSize)]), SpatialScanStore.maximumArchiveBytes)
        XCTAssertThrowsError(try SpatialScanStore.archiveByteCount([(name: name, size: exactSize + 1)]))
        XCTAssertThrowsError(try SpatialScanStore.archiveByteCount([(name: "x", size: -1)]))
        XCTAssertEqual(try SpatialScanStore.archiveByteCount([]), 22)
    }

    @MainActor
    func testHistoryLoadCountsExistingReferencedImageBytesInsteadOfResettingBudget() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        let pose = SpatialPose(time: 1, position: SpatialPoint(x: 0, y: 0, z: 0), forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 2)
        let frame = SpatialKeyframe(id: "frame", time: 1, pose: pose, intrinsics: [100,0,0,0,100,0,10,10,1], width: 20, height: 20, imageFile: "frame.jpg", cameraToWorld: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])
        let document = SpatialScanDocument(poses: [pose], keyframes: [frame])
        // Real file accounting only; these opaque bytes are not a visual model fixture.
        let bytes = Data([0xff,0xd8,0xff,0xd9])
        try await store.saveKeyframe(data: bytes, scanID: document.id, filename: "frame.jpg")
        try await store.saveKeyframe(data: Data(repeating: 0, count: 100), scanID: document.id, filename: "orphan.jpg")
        try await store.save(document)
        let actualBytes = try await store.keyframeStorageBytes(document)
        XCTAssertEqual(actualBytes, bytes.count)
        let controller = SpatialScanController(store: store)
        controller.load(document)
        let deadline = Date().addingTimeInterval(10)
        while controller.exportURL == nil && Date() < deadline { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertNotNil(controller.exportURL)
        XCTAssertEqual(controller.keyframeBytes, bytes.count)
        XCTAssertEqual(controller.document, document)
    }
}

final class SpatialProjectiveTexturingTests: XCTestCase {
    func testProjectiveTexturePutsFrontalPlaneAtImageCenter() {
        let wall = SpatialSurface(id: "wall", kind: "vertical", vertices: [
            SpatialPoint(x: -0.2, y: -0.2, z: -1), SpatialPoint(x: 0.2, y: -0.2, z: -1),
            SpatialPoint(x: 0.2, y: 0.2, z: -1), SpatialPoint(x: -0.2, y: 0.2, z: -1)
        ], epoch: 1)
        let mapping = SpatialProjectiveTexturing.mapping(surface: wall, keyframe: frontalKeyframe())
        XCTAssertNotNil(mapping)
        XCTAssertGreaterThan(mapping!.visibleFraction, 0.99)
        XCTAssertGreaterThan(mapping!.meanIncidence, 0.99)
        let center = mapping!.uvs.reduce(SIMD2<Float>.zero, +) / 4
        XCTAssertEqual(center.x, 0.5, accuracy: 0.02)
        XCTAssertEqual(center.y, 0.5, accuracy: 0.02)
    }

    func testProjectiveTextureRejectsPlaneBehindCamera() {
        var wall = SpatialSurface(id: "back", kind: "vertical", vertices: [
            SpatialPoint(x: -0.2, y: -0.2, z: 1), SpatialPoint(x: 0.2, y: -0.2, z: 1),
            SpatialPoint(x: 0.2, y: 0.2, z: 1), SpatialPoint(x: -0.2, y: 0.2, z: 1)
        ], epoch: 1)
        XCTAssertNil(SpatialProjectiveTexturing.mapping(surface: wall, keyframe: frontalKeyframe()))
        wall.epoch = 2
        XCTAssertNil(SpatialProjectiveTexturing.mapping(surface: wall, keyframes: [frontalKeyframe()]),
                     "A later epoch cannot reuse an earlier photo")
    }

    func testProjectiveTexturePrefersFrontalKeyframeOverGrazing() {
        let wall = SpatialSurface(id: "wall", kind: "vertical", vertices: [
            SpatialPoint(x: -0.2, y: -0.2, z: -1), SpatialPoint(x: 0.2, y: -0.2, z: -1),
            SpatialPoint(x: 0.2, y: 0.2, z: -1), SpatialPoint(x: -0.2, y: 0.2, z: -1)
        ], epoch: 1)
        var grazing = frontalKeyframe()
        grazing.id = "grazing"; grazing.imageFile = "grazing.jpg"
        grazing.pose.position = SpatialPoint(x: 1.2, y: 0, z: 0)
        grazing.pose.forward = SpatialPoint(x: -0.76, y: 0, z: -0.65)
        grazing.cameraToWorld = [0, 0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1.2, 0, 0, 1]
        let chosen = SpatialProjectiveTexturing.mapping(surface: wall, keyframes: [grazing, frontalKeyframe()])
        XCTAssertEqual(chosen?.imageFile, "front.jpg")
        var hidden = wall
        hidden.observed = false
        XCTAssertNil(SpatialProjectiveTexturing.mapping(surface: hidden, keyframes: [frontalKeyframe()]),
                     "Unobserved surfaces stay untextured")
    }

    private func frontalKeyframe() -> SpatialKeyframe {
        SpatialKeyframe(id: "front", time: 1,
            pose: SpatialPose(time: 1, position: SpatialPoint(x: 0, y: 0, z: 0),
                              forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 1),
            intrinsics: [500, 0, 0, 0, 500, 0, 320, 240, 1], width: 640, height: 480,
            imageFile: "front.jpg", cameraToWorld: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
    }
}

final class SpatialPlaneGroupingTests: XCTestCase {
    func testTouchingCoplanarFragmentsBecomeOneTallCandidate() {
        let left = quad("left", x0: 0, x1: 0.5, y0: 0, y1: 1.4, z: 0)
        let right = quad("right", x0: 0.6, x1: 1.2, y0: 0, y1: 1.4, z: 0)
        let groups = SpatialPlaneGrouping.candidates([left, right])
        XCTAssertEqual(groups.count, 1)
        XCTAssertEqual(groups[0].surfaceIDs, ["left", "right"])
        XCTAssertTrue(groups[0].tall)
    }

    func testParallelWallAndDistantGapStaySeparate() {
        let near = quad("near", x0: 0, x1: 1, y0: 0, y1: 1.4, z: 0)
        let far = quad("far", x0: 0, x1: 1, y0: 0, y1: 1.4, z: 2)
        let gapped = quad("gap", x0: 2, x1: 3, y0: 0, y1: 1.4, z: 0)
        let groups = SpatialPlaneGrouping.candidates([near, far, gapped])
        XCTAssertEqual(groups.count, 3)
    }

    func testShortVerticalPatchIsNotATallWall() {
        let easel = quad("easel", x0: 0, x1: 0.6, y0: 0.8, y1: 1.3, z: 0)
        let groups = SpatialPlaneGrouping.candidates([easel])
        XCTAssertEqual(groups.count, 1)
        XCTAssertFalse(groups[0].tall)
    }

    func testLaterEpochDoesNotJoin() {
        var later = quad("later", x0: 0.5, x1: 1, y0: 0, y1: 1.4, z: 0)
        later.epoch = 2
        let groups = SpatialPlaneGrouping.candidates([quad("now", x0: 0, x1: 0.4, y0: 0, y1: 1.4, z: 0), later])
        XCTAssertEqual(groups.count, 2)
    }

    func testVectorLayerKeepsTallWallsAndShortObjectsApart() {
        let wall = quad("wall", x0: 0, x1: 1, y0: 0, y1: 1.4, z: 0)
        let easel = quad("easel", x0: 0, x1: 0.4, y0: 0.8, y1: 1.2, z: 1)
        let pieces = SpatialPlaneGrouping.vectorPieces(surfaces: [wall, easel], poses: [])
        XCTAssertEqual(pieces.map(\.role).sorted(), ["object", "wall"])
        XCTAssertEqual(pieces.first { $0.role == "wall" }?.chains.first?.count, 4)
        XCTAssertFalse(pieces.contains { $0.role == "object" && $0.surfaceID == "wall" })
    }

    func testVectorLayerDropsHorizontalVerticesFarFromThePath() {
        let floor = SpatialSurface(id: "floor", kind: "horizontal", vertices: [
            SpatialPoint(x: 0, y: 0, z: 0), SpatialPoint(x: 0.4, y: 0, z: 0),
            SpatialPoint(x: 5, y: 0, z: 0), SpatialPoint(x: 5, y: 0, z: 4)
        ], epoch: 1)
        let pose = SpatialPose(time: 1, position: SpatialPoint(x: 0.1, y: 1.2, z: 0.1),
                               forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 1)
        let pieces = SpatialPlaneGrouping.vectorPieces(surfaces: [floor], poses: [pose])
        let drawn = pieces.flatMap { $0.chains.flatMap { $0 } }
        XCTAssertTrue(drawn.contains { $0.x == 0 && $0.z == 0 })
        XCTAssertFalse(drawn.contains { $0.x == 5 })
        XCTAssertFalse(pieces.contains { $0.closed })
        XCTAssertTrue(SpatialPlaneGrouping.vectorPieces(surfaces: [floor], poses: []).isEmpty)
    }

    func testCollinearTallFragmentsBecomeOneWallLine() {
        let left = quad("left", x0: 0, x1: 1, y0: 0, y1: 2, z: 0)
        let right = quad("right", x0: 1.1, x1: 2, y0: 0, y1: 2, z: 0.02)
        let lines = SpatialPlaneGrouping.wallLines(surfaces: [left, right])
        XCTAssertEqual(lines.filter { $0.role == "wall" }.count, 1)
        let wall = lines[0]
        let xs = [wall.start.x, wall.end.x].sorted()
        XCTAssertEqual(xs[0], 0, accuracy: 0.05)
        XCTAssertEqual(xs[1], 2, accuracy: 0.05)
    }

    func testNearCornerJoinsAndADoorwayStaysOpen() {
        let north = quad("north", x0: 0, x1: 2, y0: 0, y1: 2, z: 0)
        let east = upright("east", z0: 0.4, z1: 2.2, x: 2.4)
        let joined = SpatialPlaneGrouping.wallLines(surfaces: [north, east])
        XCTAssertEqual(joined.count, 2)
        XCTAssertTrue(joined.contains { hypot($0.start.x - 2.4, $0.start.z) < 0.05 || hypot($0.end.x - 2.4, $0.end.z) < 0.05 })
        let shortNorth = quad("north", x0: 0, x1: 1, y0: 0, y1: 2, z: 0)
        let open = SpatialPlaneGrouping.wallLines(surfaces: [shortNorth, east])
        let northEnd = open.first { $0.surfaceIDs == ["north"] }
        let reached = [northEnd?.start, northEnd?.end].compactMap { $0 }.contains { $0.x > 1.5 }
        XCTAssertFalse(reached)
    }

    func testFurnitureOffTheWallStaysAndAWallSliverDoesNot() {
        let wall = quad("wall", x0: 0, x1: 3, y0: 0, y1: 2.2, z: 0)
        let cabinet = quad("cabinet", x0: 1, x1: 1.5, y0: 0, y1: 0.8, z: 1)
        let picture = quad("picture", x0: 2, x1: 2.15, y0: 0, y1: 2.1, z: 0.02)
        let lines = SpatialPlaneGrouping.wallLines(surfaces: [wall, cabinet, picture])
        XCTAssertEqual(lines.filter { $0.role == "wall" }.count, 1)
        XCTAssertEqual(lines.filter { $0.role == "object" }.map(\.surfaceIDs), [["cabinet"]])
        XCTAssertTrue(SpatialPlaneGrouping.wallLines(surfaces: [
            SpatialSurface(id: "floor", kind: "horizontal", vertices: wall.vertices, epoch: 1)
        ]).isEmpty)
    }

    private func upright(_ id: String, z0: Float, z1: Float, x: Float) -> SpatialSurface {
        SpatialSurface(id: id, kind: "vertical", vertices: [
            SpatialPoint(x: x, y: 0, z: z0), SpatialPoint(x: x, y: 0, z: z1),
            SpatialPoint(x: x, y: 2, z: z1), SpatialPoint(x: x, y: 2, z: z0)
        ], epoch: 1)
    }

    private func quad(_ id: String, x0: Float, x1: Float, y0: Float, y1: Float, z: Float) -> SpatialSurface {
        SpatialSurface(id: id, kind: "vertical", vertices: [
            SpatialPoint(x: x0, y: y0, z: z), SpatialPoint(x: x1, y: y0, z: z),
            SpatialPoint(x: x1, y: y1, z: z), SpatialPoint(x: x0, y: y1, z: z)
        ], epoch: 1)
    }
}

extension SpatialScanTests {
    func testLegacyScanWithoutObjectObservationsRemainsReadable() throws {
        let original = SpatialScanDocument()
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(original)) as? [String: Any])
        json.removeValue(forKey: "objectObservations")
        let decoded = try JSONDecoder().decode(SpatialScanDocument.self, from: JSONSerialization.data(withJSONObject: json))
        XCTAssertNil(decoded.objectObservations)
        XCTAssertEqual(decoded.schemaVersion, 1)
    }

    func testObjectObservationsRoundTripAndRejectInvalidProvenanceOrCandidates() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        let camera = SpatialPose(time: 12, position: SpatialPoint(x: 0, y: 0, z: 0),
                                 forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 2)
        let frame = SpatialKeyframe(id: "object-frame", time: 12, pose: camera,
            intrinsics: [100,0,0,0,100,0,10,10,1], width: 20, height: 20,
            imageFile: "object-frame.jpg", cameraToWorld: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])
        let detection = SpatialObjectDetection(label: "chair", score: 0.72, boundingBox: [0.1,0.2,0.7,0.9])
        let observation = SpatialObjectObservation(frameID: frame.id, epoch: 2, frameTime: 12,
            status: .processed, elapsedSeconds: 0.08, detections: [detection], modelID: "local-yolo")
        var original = SpatialScanDocument()
        original.keyframes = [frame]
        original.objectObservations = [observation]
        try await store.saveKeyframe(data: Data([0xff, 0xd8, 0xff, 0xd9]), scanID: original.id, filename: frame.imageFile)
        try await store.save(original)
        let restored = try await store.loadAll()
        XCTAssertEqual(restored.first?.objectObservations, [observation])

        var invalid: [SpatialObjectObservation] = []
        var changed = observation; changed.frameID = "other-scan-frame"; invalid.append(changed)
        changed = observation; changed.epoch = 3; invalid.append(changed)
        changed = observation; changed.frameTime = 12.01; invalid.append(changed)
        changed = observation; changed.elapsedSeconds = -.infinity; invalid.append(changed)
        changed = observation; changed.detections[0].score = .nan; invalid.append(changed)
        changed = observation; changed.detections[0].boundingBox = [0,0,1.1,1]; invalid.append(changed)
        changed = observation; changed.detections[0].boundingBox = [0.8,0.2,0.3,0.9]; invalid.append(changed)
        changed = observation; changed.status = .failed; changed.failure = "识别失败"; invalid.append(changed)
        changed = observation; changed.status = .failed; changed.failure = " "; changed.detections = []; invalid.append(changed)
        for candidate in invalid {
            var document = original; document.objectObservations = [candidate]
            do { try await store.save(document); XCTFail("Invalid observation must not overwrite stored evidence") }
            catch SpatialScanStore.StoreError.invalidDocument { }
        }
        var duplicate = original; duplicate.objectObservations = [observation, observation]
        do { try await store.save(duplicate); XCTFail("Duplicate frame outcomes must fail") }
        catch SpatialScanStore.StoreError.invalidDocument { }
        let unchanged = try await store.loadAll()
        XCTAssertEqual(unchanged.first?.objectObservations, [observation])

        var failed = observation; failed.status = .failed; failed.failure = "本地识别失败"; failed.detections = []
        original.objectObservations = [failed]
        try await store.save(original)
        let failureRestored = try await store.loadAll()
        XCTAssertEqual(failureRestored.first?.objectObservations, [failed])
    }
}

extension SpatialScanTests {
    func testObjectRetryPreservesFailureHistoryAndRejectsForgedHistoricalSource() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        let camera = SpatialPose(time: 1, position: SpatialPoint(x: 0, y: 0, z: 0),
                                 forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 0)
        let frame = SpatialKeyframe(id: "retry-frame", time: 1, pose: camera,
            intrinsics: [100,0,0,0,100,0,10,10,1], width: 20, height: 20,
            imageFile: "retry.jpg", cameraToWorld: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])
        let first = SpatialObjectObservation(frameID: frame.id, epoch: 0, frameTime: 1,
            status: .failed, failure: "识别超时", elapsedSeconds: 2, detections: [], modelID: "local-yolo")
        var retry = SpatialObjectObservation(frameID: frame.id, epoch: 0, frameTime: 1,
            status: .processed, elapsedSeconds: 0.1, detections: [], modelID: "local-yolo",
            previousAttempts: [first])
        var document = SpatialScanDocument()
        document.keyframes = [frame]; document.objectObservations = [retry]
        try await store.saveKeyframe(data: Data([0xff, 0xd8, 0xff, 0xd9]), scanID: document.id, filename: frame.imageFile)
        try await store.save(document)
        let restored = try await store.loadAll()
        XCTAssertEqual(restored.first?.objectObservations?.first?.previousAttempts, [first])
        retry.previousAttempts?[0].frameID = "other-frame"
        document.objectObservations = [retry]
        do { try await store.save(document); XCTFail("Historical results must retain the same source frame") }
        catch SpatialScanStore.StoreError.invalidDocument { }
        let unchanged = try await store.loadAll()
        XCTAssertEqual(unchanged.first?.objectObservations?.first?.previousAttempts, [first])
    }
}

extension SpatialScanTests {
    func testRealEdgeDetectionsPreserveRawBoxesAndPartialFailuresWithoutForgedClipping() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = SpatialScanStore(directory: directory)
        let camera = SpatialPose(time: 1, position: SpatialPoint(x: 0, y: 0, z: 0),
                                 forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 0)
        let frame = SpatialKeyframe(id: "edge-frame", time: 1, pose: camera,
            intrinsics: [100,0,0,0,100,0,10,10,1], width: 20, height: 20,
            imageFile: "edge.jpg", cameraToWorld: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])
        let raw: [Float] = [0.5197754, -0.000122, 0.99975, 0.47278]
        let clipped = SpatialObjectDetection(label: "chair", score: 0.72,
            boundingBox: [raw[0],0,raw[2],raw[3]], rawBoundingBox: raw)
        let bottom = SpatialObjectDetection(label: "table", score: 0.6,
            boundingBox: [0.1,0.2,0.8,1], rawBoundingBox: [0.1,0.2,0.8,1.00122])
        XCTAssertTrue(clipped.isClipped); XCTAssertTrue(bottom.isClipped)
        XCTAssertFalse(SpatialObjectDetection(label: "chair", score: 0.7, boundingBox: [0,0,1,1]).isClipped)
        let diagnostic = "candidate 3 invalid raw bounding box: [1.2,0.2,1.5,0.7]"
        var observation = SpatialObjectObservation(frameID: frame.id, epoch: 0, frameTime: 1,
            status: .partial, failure: "部分识别结果无效", elapsedSeconds: 0.1,
            detections: [clipped,bottom], modelID: "local-yolo", detectionFailures: [diagnostic])
        var document = SpatialScanDocument()
        document.keyframes = [frame]; document.objectObservations = [observation]
        try await store.saveKeyframe(data: Data([0xff,0xd8,0xff,0xd9]), scanID: document.id, filename: frame.imageFile)
        try await store.save(document)
        let restored = try await store.loadAll()
        XCTAssertEqual(restored.first?.objectObservations, [observation])
        XCTAssertEqual(restored.first?.objectObservations?.first?.detections.first?.rawBoundingBox, raw)

        var invalid: [SpatialObjectObservation] = []
        var changed = observation; changed.detections[0].boundingBox[1] = 0.01; invalid.append(changed)
        changed = observation; changed.detections[0].rawBoundingBox = [1.2,0.2,1.5,0.7]; invalid.append(changed)
        changed = observation; changed.detections[0].rawBoundingBox = [0.1,0.2,0.1,0.7]; invalid.append(changed)
        changed = observation; changed.detections[0].rawBoundingBox = [0.1,.nan,0.8,0.9]; invalid.append(changed)
        changed = observation; changed.detections = []; invalid.append(changed)
        changed = observation; changed.failure = nil; invalid.append(changed)
        changed = observation; changed.detectionFailures = []; invalid.append(changed)
        changed = observation; changed.detectionFailures = [" "]; invalid.append(changed)
        changed = observation; changed.status = .processed; changed.failure = nil; invalid.append(changed)
        changed = observation; changed.previousAttempts = [invalid[0]]; invalid.append(changed)
        for candidate in invalid {
            document.objectObservations = [candidate]
            do { try await store.save(document); XCTFail("Invalid clipping or partial evidence must fail") }
            catch SpatialScanStore.StoreError.invalidDocument { }
        }
        let unchanged = try await store.loadAll()
        XCTAssertEqual(unchanged.first?.objectObservations, [observation])
        observation.status = .failed; observation.detections = []
        document.objectObservations = [observation]
        try await store.save(document)
        let failed = try await store.loadAll()
        XCTAssertEqual(failed.first?.objectObservations?.first?.detectionFailures, [diagnostic])
    }
}
