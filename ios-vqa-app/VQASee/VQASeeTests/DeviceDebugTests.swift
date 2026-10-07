import XCTest
@testable import VQASee

final class DeviceDebugTests: XCTestCase {
    func testPairingRejectsAmbiguousOrCredentialBearingEndpoints() {
        XCTAssertNotNil(DeviceDebugPolicy.baseURL("http://192.168.1.2:8000"))
        for value in ["file:///tmp/debug", "https://user:secret@host", "https://host/path", "https://host?token=secret", "https://host#secret"] {
            XCTAssertNil(DeviceDebugPolicy.baseURL(value), value)
        }
        XCTAssertNotNil(DeviceDebugPolicy.pairing("{\"url\":\"http://192.168.1.2:8000\",\"token\":\"abcdefghijklmnop\"}"))
        XCTAssertNotNil(DeviceDebugPolicy.pairing("http://192.168.1.2:8000#abcdefghijklmnop"))
        XCTAssertNil(DeviceDebugPolicy.pairing("http://192.168.1.2:8000#short"))
        let previous = UUID().uuidString
        let retest = "{\"url\":\"http://192.168.1.2:8000\",\"token\":\"abcdefghijklmnop\",\"previous_session_id\":\"\(previous)\"}"
        XCTAssertEqual(DeviceDebugPolicy.pairing(retest)?.previousSessionID, previous)
        XCTAssertNil(DeviceDebugPolicy.pairing(retest.replacingOccurrences(of: previous, with: "not-a-session")))
    }
    func testServerErrorsGiveSpecificRecoveryWithoutLeakingResponseBodies() {
        XCTAssertTrue(DeviceDebugPolicy.serverError(401).contains("配对码"))
        XCTAssertTrue(DeviceDebugPolicy.serverError(507).contains("存储空间"))
        XCTAssertTrue(DeviceDebugPolicy.serverError(413).contains("较小"))
    }
    func testScreenGateBoundsInFlightAndSamplingAndCancellation() {
        let gate = DeviceDebugFrameGate()
        XCTAssertTrue(gate.admit(at: 1))
        XCTAssertFalse(gate.admit(at: 5), "Slow upload must not retain more buffers")
        gate.finish()
        XCTAssertFalse(gate.admit(at: 1.9))
        XCTAssertTrue(gate.admit(at: 2))
        gate.finish()
        gate.cancel()
        XCTAssertFalse(gate.admit(at: 100))
    }
    func testSampleFilenameCannotEscapeDestination() {
        XCTAssertTrue(DeviceDebugPolicy.validFilename("capture.json"))
        for name in ["../sample", "a/b", "a\\b", "\r\nInjected: yes", "..", ""] {
            XCTAssertFalse(DeviceDebugPolicy.validFilename(name))
        }
    }
    @MainActor
    func testSamplesRequireSeparateExplicitConsent() async {
        let controller = DeviceDebugController()
        XCTAssertFalse(controller.shareOriginalSamples)
        await controller.uploadAttachment(data: Data([1]), filename: "sample.bin")
        XCTAssertEqual(controller.attachmentStatus, "原始样本共享未开启")
        XCTAssertFalse(controller.uploadingAttachment)
    }
}
