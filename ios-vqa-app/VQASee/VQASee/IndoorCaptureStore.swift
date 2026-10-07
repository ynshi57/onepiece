import Foundation
import CryptoKit

nonisolated enum IndoorCaptureEvent: String, Codable, CaseIterable, Identifiable {
    case unchanged = "物品未动", removed = "移走", moved = "移到新位置"
    case occluded = "遮挡", replaced = "换成相似品", viewpoint = "角度或光线变化"
    var id: String { rawValue }
}

nonisolated struct IndoorCaptureFrame: Codable, Identifiable, Sendable {
    var id: Int
    var timestamp: Double
    var tracking: String
    var sensorWidth: Int
    var sensorHeight: Int
    var imageWidth: Int
    var imageHeight: Int
    /// JPEG pixels retain ARFrame sensor orientation. EXIF is up (1).
    var interfaceOrientation: Int
    /// Column-major ARKit camera-to-world, meters, right-handed, camera looks -Z.
    var cameraToWorld: [Float]
    var intrinsics: [Float]
    var encodedIntrinsics: [Float]
    var imageSHA256: String
    var depthWidth: Int?
    var depthHeight: Int?
    var depthSHA256: String?
    var confidenceSHA256: String?
    var depthUnavailableReason: String?
}

nonisolated struct IndoorCaptureManifest: Codable, Identifiable, Sendable {
    var schemaVersion = 1
    var id: UUID
    var arSessionID: UUID
    var objectName: String
    var objectInstanceID: UUID
    var event: IndoorCaptureEvent
    var startedAt: Date
    var startedUptime: Double
    var transitionStarted: Double?
    var transitionCompleted: Double?
    var endReason: String?
    var frames: [IndoorCaptureFrame] = []
    var missedBusy = 0
    var missingFrames = 0
    var duplicateFrames = 0
    var writeFailures = 0
    var bytesWritten = 0
    /// Weak temporal labels only; never assert object presence from an event label.
    func phase(at timestamp: Double) -> String {
        guard let transitionStarted else { return "基线 · 尚未开始变化" }
        if timestamp < transitionStarted { return "基线" }
        guard let transitionCompleted, timestamp >= transitionCompleted else { return "变化过程 · 待标注" }
        return "事件后 · 仍需逐帧确认"
    }
}

nonisolated struct IndoorCapturePacket: Sendable {
    var metadata: IndoorCaptureFrame
    var jpeg: Data
    var depth: Data?
    var confidence: Data?
}

nonisolated struct IndoorCaptureEntry: Identifiable, Sendable {
    let id: UUID
    let manifest: IndoorCaptureManifest?
    let issue: String?
}

nonisolated enum IndoorCaptureError: LocalizedError {
    case invalidData, sizeLimit, noSession, damaged(String)
    var errorDescription: String? {
        switch self {
        case .invalidData: return "样本字段不完整或格式不支持。"
        case .sizeLimit: return "本段样本达到容量上限，已停止。"
        case .noSession: return "采集会话已结束。"
        case .damaged(let file): return "样本文件缺失或损坏：\(file)"
        }
    }
}

/// Files first, manifest last. A crash may leave unreferenced files but never publishes a partial frame.
actor IndoorCaptureStore {
    let root: URL
    private var active: IndoorCaptureManifest?
    static let byteLimit = 80 * 1_024 * 1_024
    init(root: URL? = nil) {
        self.root = root ?? URL.applicationSupportDirectory.appendingPathComponent("IndoorCaptures", isDirectory: true)
    }
    private func folder(_ id: UUID) -> URL { root.appendingPathComponent(id.uuidString, isDirectory: true) }
    private func write(_ data: Data, to url: URL) throws {
        try data.write(to: url, options: [.atomic, .completeFileProtection])
    }
    private func commit(_ manifest: IndoorCaptureManifest) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        try write(encoder.encode(manifest), to: folder(manifest.id).appendingPathComponent("manifest.json"))
    }
    func begin(_ manifest: IndoorCaptureManifest) throws {
        guard active == nil, manifest.frames.isEmpty else { throw IndoorCaptureError.invalidData }
        let path = folder(manifest.id)
        try FileManager.default.createDirectory(at: path, withIntermediateDirectories: true,
                                               attributes: [.protectionKey: FileProtectionType.complete])
        for var url in [root, path] {
            var values = URLResourceValues(); values.isExcludedFromBackup = true
            try url.setResourceValues(values)
        }
        try commit(manifest)
        active = manifest
    }
    func append(_ packet: IndoorCapturePacket, id: UUID) throws -> IndoorCaptureManifest {
        guard var manifest = active, manifest.id == id else { throw IndoorCaptureError.noSession }
        let frame = packet.metadata
        guard frame.id == manifest.frames.count, frame.cameraToWorld.count == 16,
              frame.intrinsics.count == 9, frame.encodedIntrinsics.count == 9,
              frame.cameraToWorld.allSatisfy(\.isFinite), frame.timestamp.isFinite,
              frame.timestamp >= manifest.startedUptime,
              manifest.frames.last.map({ $0.timestamp < frame.timestamp }) ?? true,
              frame.imageWidth > 0, frame.imageHeight > 0,
              Self.hash(packet.jpeg) == frame.imageSHA256 else { throw IndoorCaptureError.invalidData }
        if let depth = packet.depth {
            guard let width = frame.depthWidth, let height = frame.depthHeight,
                  width > 0, height > 0, width <= 2048, height <= 2048,
                  depth.count == width * height * 4, Self.hash(depth) == frame.depthSHA256 else { throw IndoorCaptureError.invalidData }
            if let confidence = packet.confidence {
                guard confidence.count == width * height,
                      Self.hash(confidence) == frame.confidenceSHA256 else { throw IndoorCaptureError.invalidData }
            } else if frame.confidenceSHA256 != nil { throw IndoorCaptureError.invalidData }
        } else if frame.depthSHA256 != nil || packet.confidence != nil { throw IndoorCaptureError.invalidData }
        let bytes = packet.jpeg.count + (packet.depth?.count ?? 0) + (packet.confidence?.count ?? 0)
        guard manifest.frames.count < 30, bytes <= 8 * 1_024 * 1_024,
              manifest.bytesWritten + bytes <= Self.byteLimit else { throw IndoorCaptureError.sizeLimit }
        let dir = folder(id)
        try write(packet.jpeg, to: dir.appendingPathComponent("\(frame.id).jpg"))
        if let depth = packet.depth { try write(depth, to: dir.appendingPathComponent("\(frame.id).depth-f32le")) }
        if let confidence = packet.confidence { try write(confidence, to: dir.appendingPathComponent("\(frame.id).confidence-u8")) }
        manifest.frames.append(frame)
        manifest.bytesWritten += bytes
        try commit(manifest)
        active = manifest
        return manifest
    }
    func mark(id: UUID, time: Double, completed: Bool) throws {
        guard var manifest = active, manifest.id == id else { throw IndoorCaptureError.noSession }
        if completed {
            guard let start = manifest.transitionStarted, time >= start else { throw IndoorCaptureError.invalidData }
            manifest.transitionCompleted = time
        } else { manifest.transitionStarted = time }
        try commit(manifest); active = manifest
    }
    func finish(id: UUID, reason: String, busy: Int, missing: Int, duplicates: Int, failures: Int) throws {
        guard var manifest = active, manifest.id == id else { throw IndoorCaptureError.noSession }
        manifest.endReason = reason
        manifest.missedBusy = busy; manifest.missingFrames = missing
        manifest.duplicateFrames = duplicates; manifest.writeFailures = failures
        defer { active = nil }
        try commit(manifest)
    }
    func list() throws -> [IndoorCaptureEntry] {
        guard FileManager.default.fileExists(atPath: root.path) else { return [] }
        return try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .compactMap { url -> IndoorCaptureEntry? in
                guard let id = UUID(uuidString: url.lastPathComponent) else { return nil }
                do {
                    let manifest = try load(id)
                    return IndoorCaptureEntry(id: id, manifest: manifest,
                        issue: manifest.endReason == nil && active?.id != id ? "未正常结束，已保存的帧仍可查看" : nil)
                } catch { return IndoorCaptureEntry(id: id, manifest: nil, issue: error.localizedDescription) }
            }.sorted { ($0.manifest?.startedAt ?? .distantPast) > ($1.manifest?.startedAt ?? .distantPast) }
    }
    func load(_ id: UUID) throws -> IndoorCaptureManifest {
        let url = folder(id).appendingPathComponent("manifest.json")
        guard let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize, size <= 1_024 * 1_024 else { throw IndoorCaptureError.invalidData }
        let result = try JSONDecoder().decode(IndoorCaptureManifest.self, from: Data(contentsOf: url))
        guard result.id == id, result.schemaVersion == 1, result.frames.count <= 30,
              result.frames.enumerated().allSatisfy({ $0.offset == $0.element.id }) else { throw IndoorCaptureError.invalidData }
        return result
    }
    func readFrame(id: UUID, index: Int) throws -> IndoorCapturePacket {
        let manifest = try load(id)
        guard manifest.frames.indices.contains(index) else { throw IndoorCaptureError.invalidData }
        let frame = manifest.frames[index]
        func read(_ suffix: String, hash: String, limit: Int) throws -> Data {
            let name = "\(index).\(suffix)", url = folder(id).appendingPathComponent("\(index).\(suffix)")
            do {
                guard let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize, size <= limit else { throw IndoorCaptureError.damaged(name) }
                let bytes = try Data(contentsOf: url)
                guard Self.hash(bytes) == hash else { throw IndoorCaptureError.damaged(name) }
                return bytes
            } catch { throw IndoorCaptureError.damaged(name) }
        }
        return try IndoorCapturePacket(metadata: frame,
            jpeg: read("jpg", hash: frame.imageSHA256, limit: 8 * 1_024 * 1_024),
            depth: frame.depthSHA256.map { try read("depth-f32le", hash: $0, limit: 8 * 1_024 * 1_024) },
            confidence: frame.confidenceSHA256.map { try read("confidence-u8", hash: $0, limit: 4 * 1_024 * 1_024) })
    }
    func delete(_ id: UUID) throws {
        guard active?.id != id else { throw IndoorCaptureError.invalidData }
        try FileManager.default.removeItem(at: folder(id))
    }
    nonisolated static func hash(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    nonisolated static func scaledIntrinsics(_ k: [Float], x: Float, y: Float) -> [Float] {
        guard k.count == 9 else { return [] }
        return k.enumerated().map { i, v in i % 3 == 0 ? v * x : (i % 3 == 1 ? v * y : v) }
    }
}
