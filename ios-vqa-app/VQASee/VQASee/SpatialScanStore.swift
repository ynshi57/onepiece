import Foundation
import CryptoKit

/// Each scan is an atomic JSON document plus explicitly consented local keyframes.
actor SpatialScanStore {
    nonisolated let baseURL: URL
    nonisolated enum StoreError: LocalizedError {
        case invalidDocument, unsupportedVersion(Int), invalidFilename, corruptArchive, oversizedArchive, missingKeyframe(String)
        var errorDescription: String? {
            switch self {
            case .invalidDocument: return "空间记录不完整，未保存。"
            case .unsupportedVersion: return "空间记录版本不兼容。"
            case .invalidFilename: return "空间照片文件名无效。"
            case .corruptArchive: return "空间记录已损坏，无法读取。"
            case .missingKeyframe: return "空间照片缺失，无法完整读取或导出。"
            case .oversizedArchive: return "空间记录过大，无法保存或读取。"
            }
        }
    }
    nonisolated static let maximumArchiveBytes = 256 * 1_024 * 1_024
    nonisolated static let maximumKeyframeBytes = 110 * 1_024 * 1_024
    private let maximumBytes = 64 * 1_024 * 1_024

    /// Stored ZIP: local header 30 + central header 46 + two UTF8 names,
    /// plus 22 bytes for the end-of-central-directory record.
    nonisolated static func archiveByteCount(_ entries: [(name: String, size: Int)]) throws -> Int {
        guard entries.count <= Int(UInt16.max) else { throw StoreError.oversizedArchive }
        var total = 22
        for entry in entries {
            let nameBytes = entry.name.utf8.count
            guard entry.size >= 0, entry.size <= Int(UInt32.max), nameBytes <= Int(UInt16.max),
                  total <= maximumArchiveBytes - 76 - 2 * nameBytes,
                  entry.size <= maximumArchiveBytes - total - 76 - 2 * nameBytes else { throw StoreError.oversizedArchive }
            total += entry.size + 76 + 2 * nameBytes
        }
        return total
    }

    func keyframeStorageBytes(_ document: SpatialScanDocument) throws -> Int {
        try validateImages(document)
        return try document.keyframes.reduce(0) { sum, keyframe in
            let attributes = try FileManager.default.attributesOfItem(atPath: scanDirectory(document.id).appendingPathComponent(keyframe.imageFile).path)
            guard let size = attributes[.size] as? NSNumber else { throw StoreError.invalidDocument }
            return sum + size.intValue
        }
    }

    init(directory: URL? = nil) {
        baseURL = directory ?? URL.applicationSupportDirectory.appendingPathComponent("SpatialScans", isDirectory: true)
    }

    func save(_ document: SpatialScanDocument) throws {
        try validate(document)
        try validateImages(document)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(document)
        guard data.count <= maximumBytes else { throw StoreError.oversizedArchive }
        let directory = scanDirectory(document.id)
        try prepare(directory)
        try data.write(to: directory.appendingPathComponent("document.json"), options: [.atomic, .completeFileProtection])
    }

    func saveKeyframe(data: Data, scanID: UUID, filename: String) throws {
        guard validFilename(filename) else { throw StoreError.invalidFilename }
        guard !data.isEmpty, data.count <= 10 * 1_024 * 1_024 else { throw StoreError.oversizedArchive }
        let directory = scanDirectory(scanID)
        try prepare(directory)
        try data.write(to: directory.appendingPathComponent(filename), options: [.atomic, .completeFileProtection])
    }

    func saveWorldMap(_ data: Data, scanID: UUID) throws -> String {
        guard !data.isEmpty, data.count <= maximumBytes else { throw StoreError.oversizedArchive }
        let directory = scanDirectory(scanID)
        try prepare(directory)
        try data.write(to: directory.appendingPathComponent("world-map.archive"), options: [.atomic, .completeFileProtection])
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// Local recovery telemetry is independent of the committed scan/map.
    /// It must survive a process exit without overwriting the user's geometry.
    func saveRestorationDiagnostic(_ data: Data, scanID: UUID) throws {
        guard !data.isEmpty, data.count <= 64 * 1_024 else { throw StoreError.oversizedArchive }
        let directory = scanDirectory(scanID)
        try prepare(directory)
        try data.write(to: directory.appendingPathComponent("restoration-diagnostic.json"),
                       options: [.atomic, .completeFileProtection])
    }

    func savePerformanceDiagnostic(_ data: Data, scanID: UUID) throws {
        guard !data.isEmpty, data.count <= 1_024 * 1_024 else { throw StoreError.oversizedArchive }
        let directory = scanDirectory(scanID)
        try prepare(directory)
        try data.write(to: directory.appendingPathComponent("performance-diagnostic.json"), options: [.atomic, .completeFileProtection])
    }

    func loadWorldMap(_ document: SpatialScanDocument) throws -> Data {
        guard let reference = document.worldMap, reference.filename == "world-map.archive" else { throw StoreError.invalidDocument }
        let url = scanDirectory(document.id).appendingPathComponent(reference.filename)
        let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
        guard attributes[.type] as? FileAttributeType == .typeRegular,
              let size = attributes[.size] as? NSNumber, size.intValue > 0, size.intValue <= maximumBytes else { throw StoreError.oversizedArchive }
        let data = try Data(contentsOf: url)
        guard SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == reference.sha256 else { throw StoreError.corruptArchive }
        return data
    }

    func loadAll() throws -> [SpatialScanDocument] {
        let entries: [URL]
        do {
            entries = try FileManager.default.contentsOfDirectory(at: baseURL, includingPropertiesForKeys: [.isDirectoryKey])
        } catch let error as NSError where error.domain == NSCocoaErrorDomain && (error.code == NSFileNoSuchFileError || error.code == NSFileReadNoSuchFileError) {
            return []
        }
        var documents: [SpatialScanDocument] = []
        for entry in entries {
            guard let id = UUID(uuidString: entry.lastPathComponent),
                  try entry.resourceValues(forKeys: [.isDirectoryKey]).isDirectory == true else { continue }
            let url = entry.appendingPathComponent("document.json")
            // Keyframes can precede the first committed document; only committed scans are listed.
            guard FileManager.default.fileExists(atPath: url.path) else { continue }
            let attrs = try FileManager.default.attributesOfItem(atPath: url.path)
            if let size = attrs[.size] as? NSNumber, size.intValue > maximumBytes { throw StoreError.oversizedArchive }
            let data = try Data(contentsOf: url)
            let document: SpatialScanDocument
            do { document = try JSONDecoder().decode(SpatialScanDocument.self, from: data) }
            catch { throw StoreError.corruptArchive }
            guard document.id == id else { throw StoreError.corruptArchive }
            try validate(document)
            try validateImages(document)
            documents.append(document)
        }
        return documents.sorted { $0.createdAt > $1.createdAt }
    }

    func remove(_ document: SpatialScanDocument) throws { try delete(id: document.id) }
    func delete(id: UUID) throws {
        do { try FileManager.default.removeItem(at: scanDirectory(id)) }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && (error.code == NSFileNoSuchFileError || error.code == NSFileReadNoSuchFileError) { return }
    }

    /// Explicit crash-recovery cleanup. Committed documents and active scan IDs are retained.
    func discardUncommittedDirectories(keeping: Set<UUID> = []) throws {
        guard FileManager.default.fileExists(atPath: baseURL.path) else { return }
        for url in try FileManager.default.contentsOfDirectory(at: baseURL, includingPropertiesForKeys: [.isDirectoryKey]) {
            guard let id = UUID(uuidString: url.lastPathComponent), !keeping.contains(id),
                  try url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory == true,
                  !FileManager.default.fileExists(atPath: url.appendingPathComponent("document.json").path) else { continue }
            try FileManager.default.removeItem(at: url)
        }
    }

    func export(_ document: SpatialScanDocument) throws -> URL {
        try validate(document)
        try validateImages(document)
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("SpatialScanExports", isDirectory: true)
        try prepare(directory)
        let destination = directory.appendingPathComponent(document.id.uuidString + ".zip")
        let staging = directory.appendingPathComponent(UUID().uuidString + ".zip")
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let documentData = try encoder.encode(document)
        guard documentData.count <= maximumBytes else { throw StoreError.oversizedArchive }
        var entries: [(String, Data)] = [("document.json", documentData)]
        if let reference = document.worldMap { entries.append((reference.filename, try loadWorldMap(document))) }
        var checksums: [String: String] = ["document.json": SHA256.hash(data: documentData).map { String(format: "%02x", $0) }.joined()]
        if let reference = document.worldMap { checksums[reference.filename] = reference.sha256 }
        // Telemetry is an independent attachment, never merged into committed
        // poses/events. It may expose a restoration that was not persisted.
        let diagnosticURL = scanDirectory(document.id).appendingPathComponent("restoration-diagnostic.json")
        if FileManager.default.fileExists(atPath: diagnosticURL.path) {
            let attributes = try FileManager.default.attributesOfItem(atPath: diagnosticURL.path)
            guard attributes[.type] as? FileAttributeType == .typeRegular,
                  let size = attributes[.size] as? NSNumber, size.intValue > 0, size.intValue <= 64 * 1_024 else { throw StoreError.oversizedArchive }
            let diagnostic = try Data(contentsOf: diagnosticURL)
            guard diagnostic.count <= 64 * 1_024,
                  (try JSONSerialization.jsonObject(with: diagnostic)) is [String: Any] else { throw StoreError.invalidDocument }
            entries.append(("restoration-diagnostic.json", diagnostic))
            checksums["restoration-diagnostic.json"] = SHA256.hash(data: diagnostic).map { String(format: "%02x", $0) }.joined()
        }
        // Each image is bounded and read independently; the complete image set is never retained.
        for keyframe in document.keyframes {
            let data = try Data(contentsOf: scanDirectory(document.id).appendingPathComponent(keyframe.imageFile))
            checksums[keyframe.imageFile] = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        }
        let manifest = ExportManifest(schemaVersion: document.schemaVersion, coordinateSystem: "ARKit right-handed world, metres, +Y up; camera forward -Z; matrices column-major; intrinsics 3x3 column-major; poses are not joined across epochs; optional displayTransform is [a,b,c,d,tx,ty], normalized native image to normalized displayViewport, presentation only; absent displayOrientation means unknown, no assumed rotation", boundaryMeaning: "Observed open boundaries only; dimensions are observed vertex spans, not complete room or wall dimensions", checksumsSHA256: checksums)
        entries.append(("manifest.json", try encoder.encode(manifest)))
        var sizes = entries.map { (name: $0.0, size: $0.1.count) }
        for keyframe in document.keyframes {
            let attrs = try FileManager.default.attributesOfItem(atPath: scanDirectory(document.id).appendingPathComponent(keyframe.imageFile).path)
            guard let size = attrs[.size] as? NSNumber else { throw StoreError.invalidDocument }
            sizes.append((name: keyframe.imageFile, size: size.intValue))
        }
        let plannedArchiveBytes = try Self.archiveByteCount(sizes)
        try Data().write(to: staging, options: [.atomic, .completeFileProtection])
        do {
            let output = try FileHandle(forWritingTo: staging)
            defer { try? output.close() }
            var central = Data()
            var offset: UInt32 = 0
            var count: UInt16 = 0
            func append(_ name: String, _ data: Data) throws {
                let nameBytes = Data(name.utf8)
                let size = UInt32(data.count)
                let crc = Self.crc32(data)
                var local = Data()
                local.le(UInt32(0x04034b50)); local.le(UInt16(20)); local.le(UInt16(0x0800))
                local.le(UInt16(0)); local.le(UInt16(0)); local.le(UInt16(0x21))
                local.le(crc); local.le(size); local.le(size)
                local.le(UInt16(nameBytes.count)); local.le(UInt16(0)); local.append(nameBytes)
                guard UInt64(offset) + UInt64(local.count) + UInt64(data.count) < UInt64(UInt32.max) else { throw StoreError.oversizedArchive }
                guard UInt64(offset) + UInt64(local.count) + UInt64(data.count) + UInt64(central.count) + UInt64(46 + nameBytes.count + 22) <= UInt64(Self.maximumArchiveBytes) else { throw StoreError.oversizedArchive }
                try output.write(contentsOf: local); try output.write(contentsOf: data)
                central.le(UInt32(0x02014b50)); central.le(UInt16(20)); central.le(UInt16(20)); central.le(UInt16(0x0800))
                central.le(UInt16(0)); central.le(UInt16(0)); central.le(UInt16(0x21))
                central.le(crc); central.le(size); central.le(size); central.le(UInt16(nameBytes.count))
                central.le(UInt16(0)); central.le(UInt16(0)); central.le(UInt16(0)); central.le(UInt16(0)); central.le(UInt32(0)); central.le(offset); central.append(nameBytes)
                offset += UInt32(local.count + data.count); count += 1
            }
            for (name, data) in entries { try append(name, data) }
            for keyframe in document.keyframes {
                let data = try Data(contentsOf: scanDirectory(document.id).appendingPathComponent(keyframe.imageFile))
                try append(keyframe.imageFile, data)
            }
            guard Int(offset) + central.count + 22 == plannedArchiveBytes else { throw StoreError.corruptArchive }
            try output.write(contentsOf: central)
            var end = Data()
            end.le(UInt32(0x06054b50)); end.le(UInt16(0)); end.le(UInt16(0)); end.le(count); end.le(count)
            end.le(UInt32(central.count)); end.le(offset); end.le(UInt16(0))
            try output.write(contentsOf: end)
            try output.synchronize()
            try output.close()
            if FileManager.default.fileExists(atPath: destination.path) {
                _ = try FileManager.default.replaceItemAt(destination, withItemAt: staging)
            } else { try FileManager.default.moveItem(at: staging, to: destination) }
            var protectedURL = destination
            var values = URLResourceValues(); values.isExcludedFromBackup = true
            try protectedURL.setResourceValues(values)
            return destination
        } catch {
            try? FileManager.default.removeItem(at: staging)
            throw error
        }
    }

    private nonisolated struct ExportManifest: Codable {
        let schemaVersion: Int
        let coordinateSystem: String
        let boundaryMeaning: String
        let checksumsSHA256: [String: String]
    }
    private func validateImages(_ document: SpatialScanDocument) throws {
        for keyframe in document.keyframes {
            let url = scanDirectory(document.id).appendingPathComponent(keyframe.imageFile)
            guard FileManager.default.fileExists(atPath: url.path) else { throw StoreError.missingKeyframe(keyframe.imageFile) }
            let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
            guard attributes[.type] as? FileAttributeType == .typeRegular,
                  let size = attributes[.size] as? NSNumber, size.intValue > 0,
                  size.intValue <= 10 * 1_024 * 1_024 else { throw StoreError.invalidDocument }
        }
    }
    private nonisolated static func crc32(_ data: Data) -> UInt32 {
        var crc: UInt32 = 0xffffffff
        for byte in data {
            crc = (crc >> 8) ^ crcTable[Int((crc ^ UInt32(byte)) & 0xff)]
        }
        return crc ^ 0xffffffff
    }

    private nonisolated static let crcTable: [UInt32] = (0..<256).map { value in
        var crc = UInt32(value)
        for _ in 0..<8 { crc = (crc >> 1) ^ ((crc & 1) == 1 ? 0xedb88320 : 0) }
        return crc
    }

    private func scanDirectory(_ id: UUID) -> URL { baseURL.appendingPathComponent(id.uuidString, isDirectory: true) }
    private func prepare(_ directory: URL) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                attributes: [.protectionKey: FileProtectionType.complete])
        for var url in [baseURL, directory] {
            var values = URLResourceValues()
            values.isExcludedFromBackup = true
            try url.setResourceValues(values)
        }
    }
    private func validFilename(_ name: String) -> Bool {
        !name.isEmpty && name.count <= 128 && name != "." && name != ".."
            && !name.contains("/") && !name.contains("\\") && name.lowercased().hasSuffix(".jpg")
    }
    private func validate(_ document: SpatialScanDocument) throws {
        guard document.schemaVersion == 1 else { throw StoreError.unsupportedVersion(document.schemaVersion) }
        if let reference = document.worldMap {
            guard reference.filename == "world-map.archive", reference.sha256.count == 64,
                  reference.sha256.allSatisfy({ $0.isHexDigit }), reference.referenceEpoch >= 0,
                  reference.capturedAt.isFinite, reference.referenceSurfaces.allSatisfy({ $0.vertices.allSatisfy(\.isFinite) }) else { throw StoreError.invalidDocument }
        }
        func validPose(_ pose: SpatialPose) -> Bool {
            pose.time.isFinite && pose.position.isFinite && pose.forward.isFinite && pose.epoch >= 0
        }
        guard document.createdAt.timeIntervalSince1970.isFinite,
              document.poses.allSatisfy(validPose),
              (document.trackingEvents ?? []).allSatisfy({ $0.time.isFinite && $0.time >= 0 && $0.epoch >= 0
                  && ($0.removedAnchorIDs ?? []).allSatisfy({ !$0.isEmpty }) }),
              Set(document.surfaces.map(\.id)).count == document.surfaces.count,
              document.surfaces.allSatisfy({ !$0.id.isEmpty && !$0.kind.isEmpty && $0.vertices.allSatisfy(\.isFinite) && $0.observationTime.isFinite && $0.epoch >= 0 }),
              Set(document.keyframes.map(\.id)).count == document.keyframes.count,
              Set(document.keyframes.map(\.imageFile)).count == document.keyframes.count,
              document.keyframes.count <= 240,
              document.keyframes.allSatisfy({ !$0.id.isEmpty && $0.time.isFinite && validPose($0.pose)
                  && $0.hasValidDisplayMetadata
                  && $0.width > 0 && $0.height > 0 && $0.intrinsics.count == 9
                  && $0.intrinsics.allSatisfy(\.isFinite) && validFilename($0.imageFile)
                  && $0.cameraToWorld.count == 16 && $0.cameraToWorld.allSatisfy(\.isFinite)
                  && abs($0.time - $0.pose.time) < 0.000001
                  && abs($0.cameraToWorld[12] - $0.pose.position.x) < 0.001
                  && abs($0.cameraToWorld[13] - $0.pose.position.y) < 0.001
                  && abs($0.cameraToWorld[14] - $0.pose.position.z) < 0.001 }) else {
            throw StoreError.invalidDocument
        }
    }
}

private nonisolated extension Data {
    mutating func le<T: FixedWidthInteger>(_ value: T) {
        var little = value.littleEndian
        Swift.withUnsafeBytes(of: &little) { append(contentsOf: $0) }
    }
}
