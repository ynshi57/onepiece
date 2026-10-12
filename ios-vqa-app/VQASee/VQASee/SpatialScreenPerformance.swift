import Foundation
import CoreMedia
import CoreVideo
import QuartzCore
import UIKit
import ReplayKit
import Darwin

/// Fixed-size pixel marker shared by the live arrow transaction and ReplayKit.
/// Samples are read synchronously; no image or sample buffer is retained.
nonisolated enum SpatialScreenMarker {
    static let columns = 6, rows = 7
    static func bits(_ id: UInt32) -> [Bool] {
        let checksum = UInt32((id & 255) ^ ((id >> 8) & 255) ^ ((id >> 16) & 255))
        let word = UInt64(0xa5) | UInt64(id & 0xffffff) << 8 | UInt64(checksum) << 32
        return (0..<40).map { word & (1 << $0) != 0 } + [false, true]
    }
    static func decode(_ bits: [Bool]) -> UInt32? {
        guard bits.count == 42, !bits[40], bits[41] else { return nil }
        var word: UInt64 = 0
        for index in 0..<40 where bits[index] { word |= 1 << index }
        guard word & 255 == 0xa5 else { return nil }
        let id = UInt32((word >> 8) & 0xffffff)
        let checksum = (id & 255) ^ ((id >> 8) & 255) ^ ((id >> 16) & 255)
        return UInt32((word >> 32) & 255) == checksum && id > 0 ? id : nil
    }
    /// Inverse EXIF mapping: upright screen coordinates -> native pixel buffer.
    static func nativePoint(u: Double, v: Double, orientation: Int) -> (Double, Double)? {
        switch orientation {
        case 1: return (u,v)
        case 2: return (1-u,v)
        case 3: return (1-u,1-v)
        case 4: return (u,1-v)
        case 5: return (v,u)
        case 6: return (v,1-u)
        case 7: return (1-v,1-u)
        case 8: return (1-v,u)
        default: return nil
        }
    }
}

/// Thread-safe bounded statistics. CPU submission is never labelled display.
nonisolated final class SpatialScreenPerformanceProbe: @unchecked Sendable {
    static let shared = SpatialScreenPerformanceProbe()
    private let lock = NSLock()
    private struct Receipt { let source: Double; let received: Double; let applied: Double }
    private var active = false, sharing = false
    private var sourceID = "", started = 0.0, ended = 0.0
    private var nextID: UInt32 = 0, lastDecoded: UInt32 = 0
    private var receipts: [UInt32: Receipt] = [:]
    private var receiptOrder: [UInt32] = []
    private var markerRect = CGRect.zero, windowSize = CGSize.zero
    private var counts: [String: Int] = [:]
    private var captureHistogram = Array(repeating: 0, count: 5001)
    private var callbackHistogram = Array(repeating: 0, count: 5001)
    private var applyHistogram = Array(repeating: 0, count: 5001)
    private var memory: [[String: Double]] = []
    private var memoryCount = 0, firstFootprint: Double?, maximumFootprint = 0.0
    private var renderTimes: [Double] = []
    var isSharing: Bool { lock.lock(); defer { lock.unlock() }; return sharing }
    func setSharing(_ value: Bool) { lock.lock(); sharing = value; lock.unlock() }
    func begin(scanID: String) {
        lock.lock(); defer { lock.unlock() }
        active = true; sourceID = scanID; started = ProcessInfo.processInfo.systemUptime; ended = 0
        receipts.removeAll(); receiptOrder.removeAll(); lastDecoded = nextID
        counts.removeAll(); memory.removeAll(); memoryCount = 0; firstFootprint = nil; maximumFootprint = 0
        renderTimes.removeAll(); markerRect = .zero; windowSize = .zero
        captureHistogram = Array(repeating: 0, count: 5001)
        callbackHistogram = captureHistogram; applyHistogram = captureHistogram
    }
    func finish() { lock.lock(); active = false; ended = ProcessInfo.processInfo.systemUptime; markerRect = .zero; lock.unlock() }
    private func add(_ key: String, _ amount: Int = 1) { counts[key, default: 0] += amount }
    private func bucket(_ milliseconds: Double) -> Int? {
        guard milliseconds.isFinite, milliseconds >= 0, milliseconds <= 5000 else { return nil }
        return min(5000, Int(ceil(milliseconds)))
    }
    func recordCallback(source: Double, received: Double) {
        lock.lock(); defer { lock.unlock() }; guard active else { return }
        add("arCallbacks")
        if let b = bucket((received-source)*1000) { callbackHistogram[b] += 1 } else { add("callbackClockOrLatencyInvalid") }
    }
    func registerApplied(pose: SpatialPose, received: Double, rect: CGRect, window: CGSize) -> UInt32? {
        let now = ProcessInfo.processInfo.systemUptime
        lock.lock(); defer { lock.unlock() }; guard active else { return nil }
        add("liveCPUApplications")
        if let b = bucket((now-received)*1000) { applyHistogram[b] += 1 } else { add("applyClockOrLatencyInvalid") }
        guard sharing, rect.width > 0, rect.height > 0, window.width > 0, window.height > 0,
              CGRect(origin: .zero, size: window).contains(rect) else { markerRect = .zero; return nil }
        guard nextID < 0xffffff else { add("markerIDCapacityExceeded"); markerRect = .zero; return nil }
        nextID += 1
        receipts[nextID] = Receipt(source: pose.time, received: received, applied: now)
        receiptOrder.append(nextID)
        if receiptOrder.count > 512 { receipts.removeValue(forKey: receiptOrder.removeFirst()); add("receiptWindowEvictions") }
        markerRect = rect; windowSize = window
        return nextID
    }
    func hideMarker() {
        lock.lock(); defer { lock.unlock() }
        if markerRect != .zero {
            add("markerInvalidatedOnUnavailablePose")
            add("pendingMarkerIDsInvalidated", receiptOrder.filter { $0 > lastDecoded }.count)
            receipts.removeAll(); receiptOrder.removeAll()
        }
        markerRect = .zero
    }
    func recordRenderCallback(at time: Double) {
        lock.lock(); defer { lock.unlock() }; guard active else { return }
        add("sceneKitDidRenderCallbacksNotDisplayFrames")
        renderTimes.append(time)
        if renderTimes.count > 600 { renderTimes.removeFirst() }
    }
    func sampleMemory(poseCount: Int, surfaceCount: Int, keyframeCount: Int) {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
        let result = withUnsafeMutablePointer(to: &info) { pointer in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        let now = ProcessInfo.processInfo.systemUptime, thermal = ProcessInfo.processInfo.thermalState.rawValue
        lock.lock(); defer { lock.unlock() }; guard active else { return }
        guard result == KERN_SUCCESS else { add("memoryReadFailures"); return }
        let footprint = Double(info.phys_footprint)
        if firstFootprint == nil { firstFootprint = footprint }
        maximumFootprint = max(maximumFootprint, footprint); memoryCount += 1
        memory.append(["seconds": now-started, "physFootprintBytes": footprint, "thermalState": Double(thermal), "poses": Double(poseCount), "surfaces": Double(surfaceCount), "keyframes": Double(keyframeCount)])
        if memory.count > 600 { memory.removeFirst(); add("memoryWindowEvictions") }
    }
    func consume(_ sample: CMSampleBuffer) {
        let host = CMTimeGetSeconds(CMClockGetTime(CMClockGetHostTimeClock()))
        let uptime = ProcessInfo.processInfo.systemUptime
        let pts = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample))
        lock.lock(); guard active && sharing else { lock.unlock(); return }
        add("rawReplayKitVideoSamples")
        let rect = markerRect, size = windowSize
        lock.unlock()
        guard host.isFinite, pts.isFinite, abs(host-uptime) <= 0.01, pts <= host+0.005, host-pts <= 5 else { failure("captureHostClockUnknown"); return }
        guard rect.width > 0, size.width > 0, let pixel = CMSampleBufferGetImageBuffer(sample) else { failure("captureMarkerOrPixelUnavailable"); return }
        let orientation = (CMGetAttachment(sample, key: RPVideoSampleOrientationKey as CFString, attachmentModeOut: nil) as? NSNumber)?.intValue ?? 1
        let format = CVPixelBufferGetPixelFormatType(pixel)
        let bgra = format == kCVPixelFormatType_32BGRA
        let yPlane = format == kCVPixelFormatType_420YpCbCr8BiPlanarFullRange || format == kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange
        guard bgra || yPlane else { failure("capturePixelFormatUnsupported"); return }
        guard CVPixelBufferLockBaseAddress(pixel, .readOnly) == kCVReturnSuccess else { failure("capturePixelLockFailed"); return }
        defer { CVPixelBufferUnlockBaseAddress(pixel, .readOnly) }
        guard let raw = bgra ? CVPixelBufferGetBaseAddress(pixel) : CVPixelBufferGetBaseAddressOfPlane(pixel,0) else { failure("capturePixelBaseUnavailable"); return }
        let width = CVPixelBufferGetWidth(pixel), height = CVPixelBufferGetHeight(pixel)
        let stride = bgra ? CVPixelBufferGetBytesPerRow(pixel) : CVPixelBufferGetBytesPerRowOfPlane(pixel,0)
        let bytes = raw.assumingMemoryBound(to: UInt8.self)
        var bits: [Bool] = []
        for index in 0..<42 {
            let u = (rect.minX + rect.width * CGFloat(Double(index % 6)+0.5)/6) / size.width
            let v = (rect.minY + rect.height * CGFloat(Double(index / 6)+0.5)/7) / size.height
            guard let (x,y) = SpatialScreenMarker.nativePoint(u: Double(u), v: Double(v), orientation: orientation), x >= 0, y >= 0, x < 1, y < 1 else { failure("captureOrientationOrRegionUnknown"); return }
            let px = Int(x * Double(width)), py = Int(y * Double(height))
            let offset = py*stride + px*(bgra ? 4 : 1)
            let value = bgra ? (Int(bytes[offset])+Int(bytes[offset+1])+Int(bytes[offset+2]))/3 : Int(bytes[offset])
            bits.append(value > 127)
        }
        guard let id = SpatialScreenMarker.decode(bits) else { failure("captureMarkerDecodeFailed"); return }
        lock.lock(); defer { lock.unlock() }; guard active else { return }
        guard let receipt = receipts[id] else { add("captureUnknownOrEvictedID"); return }
        guard id > lastDecoded else { add("captureRepeatedOrOldID"); return }
        guard pts >= receipt.applied, let b = bucket((pts-receipt.source)*1000) else { add("captureClockOrLatencyInvalid"); return }
        if id > lastDecoded+1 { add("captureSkippedMarkerIDs", Int(id-lastDecoded-1)) }
        lastDecoded = id; captureHistogram[b] += 1; add("uniqueComposedScreenSamples")
    }
    private func failure(_ key: String) { lock.lock(); defer { lock.unlock() }; if active { add(key) } }
    private func p95(_ histogram: [Int]) -> Any {
        let total = histogram.reduce(0,+); guard total > 0 else { return NSNull() }
        let target = Int(ceil(Double(total)*0.95)); var count = 0
        for (index,n) in histogram.enumerated() { count += n; if count >= target { return index } }
        return NSNull()
    }
    func encodedSnapshot() throws -> Data {
        lock.lock(); defer { lock.unlock() }
        let duration = max(0,(ended > 0 ? ended : ProcessInfo.processInfo.systemUptime)-started)
        let value: [String: Any] = ["schemaVersion":1,"sourceScanID":sourceID,"durationSeconds":duration,
            "actualDisplayFPSVerified":false,"performanceAccepted":false,
            "latencyMeaning":"source-to-composed-screen-capture; includes ReplayKit overhead, not CPU submission or direct panel presentation",
            "noClockOffsetFitting":true,"captureLatencyP95MillisecondsUpperBucket":p95(captureHistogram),
            "sourceToCallbackP95MillisecondsUpperBucket":p95(callbackHistogram),"callbackToLiveCPUApplyP95MillisecondsUpperBucket":p95(applyHistogram),
            "counts":counts,"memorySampleCount":memoryCount,"memoryFirstFootprintBytes":firstFootprint as Any? ?? NSNull(),
            "memoryMaximumFootprintBytes":maximumFootprint,"memoryLatestTenMinuteWindow":memory,
            "sceneKitRenderCallbackCadenceIsProxy":true,"recentRenderCallbackTimes":renderTimes]
        return try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    }
}
