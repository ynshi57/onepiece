import Foundation
import Combine

nonisolated struct FieldDiagnosticRecord: Identifiable, Codable, Sendable {
    let id: UUID
    let startedAt: Date
    var sizeBytes: Int64
    var isKept: Bool
    let includesImages: Bool
    let includesLocation: Bool
    var summary: String
}

nonisolated struct FieldDiagnosticFrame: Identifiable, Sendable {
    let id: String
    let capturedAt: Date?
    let recordedAt: Date?
    let metadata: String
    let imageURL: URL?
}

/// Explicitly enabled, local-only field evidence. All filesystem work is serial
/// and off the capture/main queues. No image or coordinate collection by default.
@MainActor
final class FieldDiagnosticStore: ObservableObject {
    @Published private(set) var isRecording = false
    @Published private(set) var status = "现场诊断未开启"
    @Published private(set) var records: [FieldDiagnosticRecord] = []
    @Published private(set) var usedBytes: Int64 = 0
    @Published private(set) var captureImagesEnabled = false
    @Published private(set) var captureLocationEnabled = false
    private let worker = FieldDiagnosticWorker()
    private let queue = DispatchQueue(label: "vqasee.field-diagnostics", qos: .utility)
    private var queuedBytes = 0
    private var timer: Timer?
    private var revision = 0

    init() { refresh() }

    func start(includeImages: Bool, includeLocation: Bool) {
        guard !isRecording else { return }
        revision += 1
        isRecording = true
        captureImagesEnabled = includeImages
        captureLocationEnabled = includeLocation
        status = "正在开启现场诊断"
        submit { try $0.start(images: includeImages, location: includeLocation) }
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, self.isRecording else { return }
                self.submit { try $0.checkDuration() }
            }
        }
    }

    func stop() {
        revision += 1
        isRecording = false
        captureImagesEnabled = false
        captureLocationEnabled = false
        timer?.invalidate()
        timer = nil
        submit { try $0.stop(reason: "录制已停止") }
    }

    func markIssue() {
        guard isRecording else { return }
        submit { try $0.trigger("手动标记", manual: true) }
    }

    func ingest(metadata: Data, jpeg: Data?, issue: String?) {
        guard isRecording else { return }
        let cost = metadata.count + (jpeg?.count ?? 0)
        guard cost <= 3 * 1024 * 1024, queuedBytes + cost <= 12 * 1024 * 1024 else {
            revision += 1
            isRecording = false
            captureImagesEnabled = false
            captureLocationEnabled = false
            timer?.invalidate()
            submit { try $0.stop(reason: "写入跟不上采集，诊断已停止；本地感知继续运行") }
            return
        }
        queuedBytes += cost
        submit(bytes: cost) { try $0.ingest(metadata: metadata, jpeg: jpeg, issue: issue) }
    }

    func refresh() { submit { try $0.refresh() } }
    func delete(ids: Set<UUID>) { submit { try $0.delete(ids: ids) } }
    func setKept(id: UUID, kept: Bool) { submit { try $0.setKept(id: id, kept: kept) } }

    func export(id: UUID) async throws -> URL {
        let worker = worker
        let revision = revision
        return try await withCheckedThrowingContinuation { continuation in
            queue.async { [weak self] in
                do {
                    let url = try worker.export(id: id)
                    let state = worker.snapshot()
                    DispatchQueue.main.async { [weak self] in self?.apply(state, revision: revision) }
                    continuation.resume(returning: url)
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    func cleanupExport(url: URL) { submit { try $0.cleanupExport(url: url) } }

    func readFrames(id: UUID, offset: Int = 0, limit: Int = 100) async throws -> [FieldDiagnosticFrame] {
        let worker = worker
        return try await withCheckedThrowingContinuation { continuation in
            queue.async {
                do { continuation.resume(returning: try worker.readFrames(id: id, offset: offset, limit: limit)) }
                catch { continuation.resume(throwing: error) }
            }
        }
    }

    private func submit(bytes: Int = 0, _ body: @escaping @Sendable (FieldDiagnosticWorker) throws -> Void) {
        let worker = worker
        let revision = revision
        queue.async { [weak self] in
            do { try body(worker) }
            catch { worker.fail(error) }
            let state = worker.snapshot()
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                self.queuedBytes = max(0, self.queuedBytes - bytes)
                self.apply(state, revision: revision)
            }
        }
    }

    private func apply(_ state: FieldDiagnosticWorker.State, revision: Int) {
        records = state.records
        usedBytes = state.bytes
        guard revision == self.revision else { return }
        status = state.status
        isRecording = state.recording
        if !isRecording {
            captureImagesEnabled = false; captureLocationEnabled = false
            timer?.invalidate(); timer = nil
        }
    }
}

nonisolated private final class FieldDiagnosticWorker: @unchecked Sendable {
    struct State: Sendable {
        let records: [FieldDiagnosticRecord]
        let bytes: Int64
        let status: String
        let recording: Bool
    }
    struct BufferedImage {
        let time: TimeInterval
        let name: String
        let data: Data
    }
    enum Failure: LocalizedError {
        case message(String)
        var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
    }
    private let fm = FileManager.default
    private let limit: Int64 = 300 * 1024 * 1024
    private let testDirectory: URL?
    private var root: URL?
    private var initialized = false
    private var rows: [FieldDiagnosticRecord] = []
    private var active: FieldDiagnosticRecord?
    private var activeStart: TimeInterval = 0
    private var activeBytes: Int64 = 0
    private var bytes: Int64 = 0
    private var status = "现场诊断未开启"
    private var ring: [BufferedImage] = []
    private var ringBytes = 0
    private var lastTrigger: TimeInterval = -.infinity
    private var tailUntil: TimeInterval = -.infinity
    private var writtenNames = Set<String>()

    init(directory: URL? = nil) { testDirectory = directory }

    func snapshot() -> State {
        let currentRows = rows.map { row -> FieldDiagnosticRecord in
            guard row.id == active?.id else { return row }
            var current = row
            current.sizeBytes = activeBytes
            return current
        }
        return State(records: currentRows, bytes: bytes, status: status, recording: active != nil)
    }

    private func initialize() throws {
        guard !initialized else { return }
        let directory: URL
        if let testDirectory { directory = testDirectory }
        else {
            let base = try fm.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
            directory = base.appendingPathComponent("VQASeeFieldDiagnostics", isDirectory: true)
        }
        try makeDirectory(directory)
        root = directory
        let exports = directory.appendingPathComponent("Exports", isDirectory: true)
        // This directory is exclusively owned by the diagnostic store.
        if fm.fileExists(atPath: exports.path) { try fm.removeItem(at: exports) }
        try makeDirectory(exports)
        initialized = true
        try refresh()
    }

    func refresh() throws {
        if !initialized { try initialize(); return }
        guard let root else { return }
        var found: [FieldDiagnosticRecord] = []
        for directory in try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: [.isSymbolicLinkKey]) {
            guard UUID(uuidString: directory.lastPathComponent) != nil else { continue }
            guard try directory.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink != true else { continue }
            let manifest = directory.appendingPathComponent("manifest.json")
            guard let data = try? Data(contentsOf: manifest),
                  var record = try? JSONDecoder().decode(FieldDiagnosticRecord.self, from: data),
                  record.id.uuidString == directory.lastPathComponent else {
                // Incomplete creation from a prior crash; never a user's arbitrary directory.
                if directory.lastPathComponent != active?.id.uuidString { try fm.removeItem(at: directory) }
                continue
            }
            if record.id != active?.id, !record.isKept, Date().timeIntervalSince(record.startedAt) > 7 * 86400 {
                try fm.removeItem(at: directory)
                continue
            }
            record.sizeBytes = try size(of: directory)
            if record.id != active?.id, record.summary == "录制中" {
                record.summary = "录制中断，已保留中断前的记录"
                try JSONEncoder().encode(record).write(to: manifest, options: .atomic)
                try protect(manifest)
            }
            found.append(record)
        }
        rows = found.sorted { $0.startedAt > $1.startedAt }
        bytes = try size(of: root)
    }

    func start(images: Bool, location: Bool) throws {
        try initialize()
        guard active == nil else { return }
        try refresh()
        guard bytes + 65536 < limit else { throw Failure.message("诊断空间已满，请删除记录后再录制（上限 300 MB）") }
        let record = FieldDiagnosticRecord(id: UUID(), startedAt: Date(), sizeBytes: 0, isKept: false,
                                          includesImages: images, includesLocation: location, summary: "录制中")
        let directory = try folder(record.id)
        try makeDirectory(directory)
        try makeDirectory(directory.appendingPathComponent("images", isDirectory: true))
        active = record
        activeBytes = 0
        activeStart = ProcessInfo.processInfo.systemUptime
        ring = []; ringBytes = 0; writtenNames = []; lastTrigger = -.infinity; tailUntil = -.infinity
        try write(try JSONEncoder().encode(record), to: directory.appendingPathComponent("manifest.json"))
        let readme = """
        VQASee local field diagnostics — schema v1
        metadata.ndjson: one JSON object per ingested frame; frame_id identifies its image.
        events.ndjson: manual/automatic triggers and recording lifecycle events.
        images/: JPEGs only when image consent was enabled; filename is frame_id encoded as UTF-8 hex.
        Images consent: \(images). Precise location consent: \(location).
        Image capture: at most 15s before / 5s after a trigger; 24MiB prebuffer may shorten the window.
        Automatic triggers merge inside a 20s window; manual marks extend the current window.
        Missing images mean no consent, no camera JPEG, buffer truncation, or no matching trigger.
        Limits: recording 10min, total disk 300MiB including ZIP exports, unkept records expire after 7 days.
        The archive contains evidence, not a claim of road safety or algorithm accuracy.
        """
        try write(Data(readme.utf8), to: directory.appendingPathComponent("README.txt"))
        rows.insert(record, at: 0)
        status = "正在记录现场诊断"
    }

    func checkDuration() throws {
        if active != nil, ProcessInfo.processInfo.systemUptime - activeStart >= 600 {
            try stop(reason: "已达到 10 分钟，诊断已停止")
        }
    }

    func stop(reason: String) throws {
        guard var record = active else { return }
        record.summary = reason
        active = nil
        ring = []; ringBytes = 0; writtenNames = []
        status = reason
        let directory = try folder(record.id)
        record.sizeBytes = try size(of: directory)
        try write(try JSONEncoder().encode(record), to: directory.appendingPathComponent("manifest.json"), reserve: 0)
        try refresh()
    }

    func fail(_ error: Error) {
        let text = "现场诊断已停止：\(error.localizedDescription)"
        try? stop(reason: text)
        active = nil
        ring = []; ringBytes = 0
        status = text
    }

    func ingest(metadata: Data, jpeg: Data?, issue: String?) throws {
        try checkDuration()
        guard let active else { return }
        guard var object = try JSONSerialization.jsonObject(with: metadata) as? [String: Any],
              let frameID = object["frame_id"] as? String, !frameID.isEmpty,
              frameID.utf8.count <= 100 else { throw Failure.message("诊断帧缺少有效 frame_id") }
        if !active.includesLocation { object = redact(object) as? [String: Any] ?? [:] }
        object["diagnostic_schema"] = 1
        object["recorded_at_unix"] = Date().timeIntervalSince1970
        try append(object, name: "metadata.ndjson", id: active.id)
        let now = ProcessInfo.processInfo.systemUptime
        if active.includesImages, let jpeg, !jpeg.isEmpty {
            let name = frameID.utf8.map { String(format: "%02x", $0) }.joined() + ".jpg"
            let frame = BufferedImage(time: now, name: name, data: jpeg)
            ring.append(frame); ringBytes += jpeg.count
            while let first = ring.first, now - first.time > 15 || ringBytes > 24 * 1024 * 1024 {
                ringBytes -= first.data.count; ring.removeFirst()
            }
            if now <= tailUntil { try persist(frame) }
        }
        if let issue, !issue.isEmpty { try trigger(String(issue.prefix(512)), manual: false) }
    }

    func trigger(_ issue: String, manual: Bool) throws {
        try checkDuration()
        guard let active else { return }
        let now = ProcessInfo.processInfo.systemUptime
        let merged = !manual && now - lastTrigger < 20
        try append(["event": merged ? "issue_merged" : "issue", "reason": issue,
                    "manual": manual, "recorded_at_unix": Date().timeIntervalSince1970],
                   name: "events.ndjson", id: active.id)
        guard !merged else { return }
        lastTrigger = now
        tailUntil = now + 5
        if active.includesImages {
            for frame in ring where now - frame.time <= 15 { try persist(frame) }
        }
        status = "已标记问题，继续记录"
    }

    private func persist(_ frame: BufferedImage) throws {
        guard let active, !writtenNames.contains(frame.name) else { return }
        try write(frame.data, to: folder(active.id).appendingPathComponent("images/" + frame.name))
        writtenNames.insert(frame.name)
    }

    func delete(ids: Set<UUID>) throws {
        try initialize()
        for id in ids where id != active?.id {
            // Explicit deletion is the user's retention decision, including records
            // previously marked "keep". The active session remains protected.
            guard rows.contains(where: { $0.id == id }) else { continue }
            try fm.removeItem(at: folder(id))
        }
        try refresh()
    }

    func setKept(id: UUID, kept: Bool) throws {
        try initialize()
        guard var record = rows.first(where: { $0.id == id }) else { return }
        record.isKept = kept
        if active?.id == id { active = record }
        try write(try JSONEncoder().encode(record), to: folder(id).appendingPathComponent("manifest.json"), reserve: 0)
        try refresh()
    }

    func export(id: UUID) throws -> URL {
        try initialize()
        guard active?.id != id else { throw Failure.message("请先停止录制再导出") }
        guard rows.contains(where: { $0.id == id }) else { throw Failure.message("记录不存在或已过期") }
        try refresh()
        let directory = try folder(id)
        let files = try regularFiles(in: directory)
        let estimated = try files.reduce(Int64(22)) { sum, file in
            let name = String(file.path.dropFirst(directory.path.count + 1))
            return sum + (try size(of: file)) + Int64(92 + name.utf8.count * 2)
        }
        guard bytes + estimated <= limit else { throw Failure.message("空间不足以生成 ZIP，请先删除其他未保留记录") }
        guard let root else { throw Failure.message("诊断目录不可用") }
        let output = root.appendingPathComponent("Exports/\(id.uuidString)-\(UUID().uuidString).zip")
        do {
            try FieldDiagnosticZIP.create(files: files, relativeTo: directory, output: output)
            try protect(output)
            bytes += try size(of: output)
            return output
        } catch {
            try? fm.removeItem(at: output)
            throw error
        }
    }

    func cleanupExport(url: URL) throws {
        try initialize()
        guard let root,
              url.standardizedFileURL.deletingLastPathComponent() == root.appendingPathComponent("Exports").standardizedFileURL,
              url.pathExtension == "zip" else { return }
        if fm.fileExists(atPath: url.path) { try fm.removeItem(at: url) }
        bytes = try size(of: root)
    }

    func readFrames(id: UUID, offset: Int, limit: Int) throws -> [FieldDiagnosticFrame] {
        try initialize()
        guard rows.contains(where: { $0.id == id }) else { throw Failure.message("记录不存在或已过期") }
        let directory = try folder(id)
        let file = directory.appendingPathComponent("metadata.ndjson")
        guard fm.fileExists(atPath: file.path) else { return [] }
        let reader = try FileHandle(forReadingFrom: file)
        defer { try? reader.close() }
        var pending = Data()
        var result: [FieldDiagnosticFrame] = []
        var lineNumber = 0
        let count = min(200, max(1, limit))
        while let chunk = try reader.read(upToCount: 65536), !chunk.isEmpty {
            pending.append(chunk)
            while let newline = pending.firstIndex(of: 10) {
                let line = Data(pending[..<newline])
                pending.removeSubrange(...newline)
                defer { lineNumber += 1 }
                guard lineNumber >= max(0, offset),
                      let object = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
                      let frameID = object["frame_id"] as? String else { continue }
                let image = directory.appendingPathComponent("images/" + frameID.utf8.map { String(format: "%02x", $0) }.joined() + ".jpg")
                let preview = String(decoding: line.prefix(32768), as: UTF8.self)
                    + (line.count > 32768 ? "\n（预览已截断，导出 ZIP 包含完整数据）" : "")
                result.append(FieldDiagnosticFrame(id: frameID,
                    capturedAt: (object["captured_at_unix"] as? Double).map { Date(timeIntervalSince1970: $0) },
                    recordedAt: (object["recorded_at_unix"] as? Double).map { Date(timeIntervalSince1970: $0) },
                    metadata: preview, imageURL: fm.fileExists(atPath: image.path) ? image : nil))
                if result.count >= count { return result }
            }
            guard pending.count <= 3 * 1024 * 1024 else { throw Failure.message("诊断文件单行超过上限") }
        }
        return result
    }

    private func folder(_ id: UUID) throws -> URL {
        guard let root else { throw Failure.message("诊断目录不可用") }
        return root.appendingPathComponent(id.uuidString, isDirectory: true)
    }

    private func redact(_ value: Any) -> Any {
        if let dict = value as? [String: Any] {
            return dict.reduce(into: [String: Any]()) { result, item in
                let key = item.key.lowercased().replacingOccurrences(of: "_", with: "")
                guard !["location", "latitude", "longitude", "coordinate", "gps", "placename", "address"].contains(where: { key.contains($0) }) else { return }
                result[item.key] = redact(item.value)
            }
        }
        if let array = value as? [Any] { return array.map(redact) }
        return value
    }

    private func append(_ object: [String: Any], name: String, id: UUID) throws {
        var data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        data.append(0x0a)
        guard bytes + Int64(data.count) + 65536 <= limit else { throw Failure.message("诊断空间已满（300 MB）") }
        let file = try folder(id).appendingPathComponent(name)
        if !fm.fileExists(atPath: file.path) { try write(Data(), to: file) }
        let handle = try FileHandle(forWritingTo: file)
        defer { try? handle.close() }
        try handle.seekToEnd()
        try handle.write(contentsOf: data)
        bytes += Int64(data.count)
        if id == active?.id { activeBytes += Int64(data.count) }
    }

    private func write(_ data: Data, to url: URL, reserve: Int64 = 65536) throws {
        let old = fm.fileExists(atPath: url.path) ? try size(of: url) : 0
        // Atomic writes briefly coexist with the original. Include that overhead.
        guard bytes + Int64(data.count) + reserve <= limit else { throw Failure.message("诊断空间已满（300 MB）") }
        try data.write(to: url, options: .atomic)
        try protect(url)
        bytes += Int64(data.count) - old
        if let active, url.path.hasPrefix(try folder(active.id).path + "/") {
            activeBytes += Int64(data.count) - old
        }
    }

    private func makeDirectory(_ url: URL) throws {
        try fm.createDirectory(at: url, withIntermediateDirectories: true)
        try protect(url)
    }

    private func protect(_ url: URL) throws {
        var target = url
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try target.setResourceValues(values)
        #if os(iOS)
        try fm.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: url.path)
        #endif
    }

    private func regularFiles(in directory: URL) throws -> [URL] {
        guard let enumerator = fm.enumerator(at: directory, includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey]) else { return [] }
        var files: [URL] = []
        for case let file as URL in enumerator {
            let values = try file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
            if values.isSymbolicLink == true { enumerator.skipDescendants(); continue }
            if values.isRegularFile == true { files.append(file) }
        }
        return files.sorted { $0.path < $1.path }
    }

    private func size(of url: URL) throws -> Int64 {
        let values = try url.resourceValues(forKeys: [.isDirectoryKey, .fileSizeKey])
        if values.isDirectory == true {
            return try regularFiles(in: url).reduce(0) { $0 + Int64(try $1.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) }
        }
        return Int64(values.fileSize ?? 0)
    }
}

/// ZIP method 0 (stored), CRC32 and data descriptors; streaming 64KiB chunks.
/// The 300MiB store cap keeps all entries and offsets within classic ZIP limits.
nonisolated private enum FieldDiagnosticZIP {
    private struct Entry { let name: Data; let crc: UInt32; let size: UInt32; let offset: UInt32 }
    static func create(files: [URL], relativeTo root: URL, output: URL) throws {
        guard files.count <= Int(UInt16.max) else { throw CocoaError(.fileWriteFileExists) }
        FileManager.default.createFile(atPath: output.path, contents: nil)
        let writer = try FileHandle(forWritingTo: output)
        defer { try? writer.close() }
        var entries: [Entry] = []
        for file in files {
            let name = Data(file.path.dropFirst(root.path.count + 1).utf8)
            let offset = UInt32(try writer.offset())
            var header = Data()
            header.u32(0x04034b50); header.u16(20); header.u16(0x0808); header.u16(0)
            header.u16(0); header.u16(0x21); header.u32(0); header.u32(0); header.u32(0)
            header.u16(UInt16(name.count)); header.u16(0); header.append(name)
            try writer.write(contentsOf: header)
            let reader = try FileHandle(forReadingFrom: file)
            var crc: UInt32 = 0xffffffff
            var size: UInt32 = 0
            do {
                while let data = try reader.read(upToCount: 65536), !data.isEmpty {
                    for byte in data { crc = table[Int((crc ^ UInt32(byte)) & 255)] ^ (crc >> 8) }
                    size += UInt32(data.count)
                    try writer.write(contentsOf: data)
                }
                try reader.close()
            } catch { try? reader.close(); throw error }
            crc ^= 0xffffffff
            var descriptor = Data()
            descriptor.u32(0x08074b50); descriptor.u32(crc); descriptor.u32(size); descriptor.u32(size)
            try writer.write(contentsOf: descriptor)
            entries.append(Entry(name: name, crc: crc, size: size, offset: offset))
        }
        let centralOffset = UInt32(try writer.offset())
        for entry in entries {
            var central = Data()
            central.u32(0x02014b50); central.u16(20); central.u16(20); central.u16(0x0808); central.u16(0)
            central.u16(0); central.u16(0x21); central.u32(entry.crc); central.u32(entry.size); central.u32(entry.size)
            central.u16(UInt16(entry.name.count)); central.u16(0); central.u16(0); central.u16(0); central.u16(0)
            central.u32(0); central.u32(entry.offset); central.append(entry.name)
            try writer.write(contentsOf: central)
        }
        let centralSize = UInt32(try writer.offset()) - centralOffset
        var end = Data()
        end.u32(0x06054b50); end.u16(0); end.u16(0)
        end.u16(UInt16(entries.count)); end.u16(UInt16(entries.count)); end.u32(centralSize); end.u32(centralOffset); end.u16(0)
        try writer.write(contentsOf: end)
    }
    private static let table: [UInt32] = (0..<256).map { value in
        var crc = UInt32(value)
        for _ in 0..<8 { crc = (crc & 1) == 1 ? 0xedb88320 ^ (crc >> 1) : crc >> 1 }
        return crc
    }
}

nonisolated private extension Data {
    mutating func u16(_ value: UInt16) { append(UInt8(value & 255)); append(UInt8(value >> 8)) }
    mutating func u32(_ value: UInt32) { u16(UInt16(value & 65535)); u16(UInt16(value >> 16)) }
}
