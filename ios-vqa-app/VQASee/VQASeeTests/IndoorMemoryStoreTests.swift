import XCTest
@testable import VQASee

final class IndoorMemoryStoreTests: XCTestCase {
    private var directory: URL!

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    }

    override func tearDownWithError() throws {
        if FileManager.default.fileExists(atPath: directory.path) {
            try FileManager.default.removeItem(at: directory)
        }
    }

    private func snapshot() -> IndoorMemorySnapshot {
        // Storage treats photo/map as opaque bytes. ARKit and image decoding belong to integration tests.
        IndoorMemorySnapshot(records: [IndoorMemoryStoredRecord(id: UUID(), name: "杯子", recordedAt: Date(),
            sessionID: UUID(), photo: Data([1, 2, 3]), metadata: Data("pose".utf8))],
            worldMapData: Data("opaque-map".utf8))
    }

    func testRoundTripAndPrivacyAttributes() async throws {
        let store = IndoorMemoryStore(directory: directory)
        let original = snapshot()
        try await store.save(original)
        let loaded = try await store.load()
        XCTAssertEqual(loaded, original)
        let resources = try directory.resourceValues(forKeys: [.isExcludedFromBackupKey])
        XCTAssertEqual(resources.isExcludedFromBackup, true)
        // Simulator uses the host filesystem and does not implement iOS data protection.
        // Keep the actual protection assertion on device; simulator round-trip is not privacy validation.
#if !targetEnvironment(simulator)
        let attributes = try FileManager.default.attributesOfItem(atPath: directory.appendingPathComponent("memory.plist").path)
        XCTAssertEqual(attributes[.protectionKey] as? FileProtectionType, .complete)
#endif
    }

    func testFreshStoreAndDeletionReturnNil() async throws {
        let store = IndoorMemoryStore(directory: directory)
        let fresh = try await store.load()
        XCTAssertNil(fresh)
        try await store.save(snapshot())
        try await store.delete()
        let deleted = try await store.load()
        XCTAssertNil(deleted)
        try await store.delete()
    }

    func testInvalidReplacementPreservesPreviousSnapshot() async throws {
        let store = IndoorMemoryStore(directory: directory)
        let original = snapshot()
        try await store.save(original)
        var invalid = original
        invalid.schemaVersion = 99
        do {
            try await store.save(invalid)
            XCTFail("Unsupported schema must not replace saved evidence")
        } catch IndoorMemoryStore.StoreError.unsupportedVersion(99) { }
        let loaded = try await store.load()
        XCTAssertEqual(loaded, original)
    }

    func testCorruptArchiveIsNotTreatedAsEmpty() async throws {
        let store = IndoorMemoryStore(directory: directory)
        try await store.save(snapshot())
        try Data("broken".utf8).write(to: directory.appendingPathComponent("memory.plist"))
        do {
            _ = try await store.load()
            XCTFail("Corruption must remain visible")
        } catch IndoorMemoryStore.StoreError.corruptArchive { }
        XCTAssertTrue(FileManager.default.fileExists(atPath: directory.appendingPathComponent("memory.plist").path))
    }

    func testPhotoOnlyReplacementRemovesOldMap() async throws {
        let store = IndoorMemoryStore(directory: directory)
        let original = snapshot()
        try await store.save(original)
        let photoOnly = IndoorMemorySnapshot(records: original.records, worldMapData: nil)
        try await store.save(photoOnly)
        let loaded = try await store.load()
        XCTAssertEqual(loaded, photoOnly)
        XCTAssertNil(loaded?.worldMapData)
    }

    func testDuplicateRecordsRejected() async throws {
        let store = IndoorMemoryStore(directory: directory)
        let record = try XCTUnwrap(snapshot().records.first)
        do {
            try await store.save(IndoorMemorySnapshot(records: [record, record], worldMapData: nil))
            XCTFail("Duplicate anchor identities must be rejected")
        } catch IndoorMemoryStore.StoreError.invalidRecords { }
    }
}
