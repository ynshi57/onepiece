import CryptoKit
import Foundation

nonisolated struct IndoorMemoryStoredRecord: Codable, Sendable, Equatable {
    let id: UUID
    let name: String
    let recordedAt: Date
    let sessionID: UUID
    let photo: Data
    var metadata: Data? = nil
}

nonisolated struct IndoorMemorySnapshot: Codable, Sendable, Equatable {
    static let currentSchemaVersion = 1
    var schemaVersion: Int = currentSchemaVersion
    let records: [IndoorMemoryStoredRecord]
    let worldMapData: Data?
    var savedAt: Date = Date()
}

/// One protected atomic archive commits photos, metadata and map together.
/// The caller owns consent and secure ARWorldMap decoding; loading never grants tracking validity.
actor IndoorMemoryStore {
    nonisolated enum StoreError: LocalizedError {
        case unsupportedVersion(Int), corruptArchive, invalidRecords, oversizedArchive

        var errorDescription: String? {
            switch self {
            case .unsupportedVersion: return "记录版本不兼容，未覆盖原有资料。"
            case .corruptArchive: return "本地物品记录已损坏，无法读取。"
            case .invalidRecords: return "物品记录不完整，未保存。"
            case .oversizedArchive: return "物品记录过大，未保存或载入。"
            }
        }
    }

    private nonisolated struct Envelope: Codable {
        let payload: Data
        let checksum: Data
    }
    private let directory: URL
    private var archiveURL: URL { directory.appendingPathComponent("memory.plist") }
    private static let maximumArchiveBytes = 80 * 1_024 * 1_024

    init(directory: URL? = nil) {
        self.directory = directory ?? URL.applicationSupportDirectory.appendingPathComponent("IndoorMemory", isDirectory: true)
    }

    func save(_ snapshot: IndoorMemorySnapshot) throws {
        try validate(snapshot)
        let encoder = PropertyListEncoder()
        encoder.outputFormat = .binary
        let payload = try encoder.encode(snapshot)
        let data = try encoder.encode(Envelope(payload: payload, checksum: Data(SHA256.hash(data: payload))))
        guard data.count <= Self.maximumArchiveBytes else { throw StoreError.oversizedArchive }
        try prepareDirectory()
        // A failed replacement leaves the previous snapshot intact. Protection is applied
        // during creation, not after a window in which an unprotected file exists.
        try data.write(to: archiveURL, options: [.atomic, .completeFileProtection])
    }

    func load() throws -> IndoorMemorySnapshot? {
        let attributes: [FileAttributeKey: Any]
        do {
            attributes = try FileManager.default.attributesOfItem(atPath: archiveURL.path)
        } catch let error as NSError where error.domain == NSCocoaErrorDomain && (error.code == NSFileNoSuchFileError || error.code == NSFileReadNoSuchFileError) {
            return nil
        }
        if let bytes = attributes[.size] as? NSNumber, bytes.intValue > Self.maximumArchiveBytes {
            throw StoreError.oversizedArchive
        }
        // I/O and data-protection failures propagate distinctly from corrupt content.
        let data = try Data(contentsOf: archiveURL)
        let snapshot: IndoorMemorySnapshot
        do {
            let decoder = PropertyListDecoder()
            let envelope = try decoder.decode(Envelope.self, from: data)
            guard envelope.checksum == Data(SHA256.hash(data: envelope.payload)) else {
                throw StoreError.corruptArchive
            }
            snapshot = try decoder.decode(IndoorMemorySnapshot.self, from: envelope.payload)
        } catch {
            throw StoreError.corruptArchive
        }
        try validate(snapshot)
        return snapshot
    }

    /// Delete the complete archive, including the saved spatial map. Errors are never hidden.
    func delete() throws {
        do {
            try FileManager.default.removeItem(at: archiveURL)
        } catch let error as NSError where error.domain == NSCocoaErrorDomain && (error.code == NSFileNoSuchFileError || error.code == NSFileReadNoSuchFileError) {
            return
        }
    }

    private func prepareDirectory() throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                attributes: [.protectionKey: FileProtectionType.complete])
        var url = directory
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try url.setResourceValues(values)
    }

    private func validate(_ snapshot: IndoorMemorySnapshot) throws {
        guard snapshot.schemaVersion == IndoorMemorySnapshot.currentSchemaVersion else {
            throw StoreError.unsupportedVersion(snapshot.schemaVersion)
        }
        guard snapshot.records.count <= 10,
              Set(snapshot.records.map(\.id)).count == snapshot.records.count,
              snapshot.savedAt.timeIntervalSince1970.isFinite,
              snapshot.records.allSatisfy({ record in
                  !record.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && record.name.count <= 30
                  && record.recordedAt.timeIntervalSince1970.isFinite && !record.photo.isEmpty
                  && record.photo.count <= 5 * 1_024 * 1_024
                  && (record.metadata?.count ?? 0) <= 1_024 * 1_024
              }), (snapshot.worldMapData?.count ?? 0) <= 20 * 1_024 * 1_024 else {
            throw StoreError.invalidRecords
        }
    }
}
