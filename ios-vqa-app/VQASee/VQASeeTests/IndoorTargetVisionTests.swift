import XCTest
import CoreGraphics
@testable import VQASee

final class IndoorTargetVisionTests: XCTestCase {
    private var clearContext: IndoorTargetComparisonContext {
        IndoorTargetComparisonContext(comparableView: true, originalRegionVisible: true,
                                      imageQualityAcceptable: true, occlusionRuledOut: true)
    }
    // Synthetic policy fixture, not a production calibration or accuracy claim.
    private var fixtureCalibration: IndoorTargetCalibration {
        IndoorTargetCalibration(featureRevision: 2, maximumSameAppearanceDistance: 0.1,
                                minimumDifferentAppearanceDistance: 0.9, evaluationID: "unit-fixture-only")
    }

    func testPerfectFeatureMatchWithoutCalibrationCannotClaimPresence() {
        let result = IndoorTargetComparisonPolicy.assess(distance: 0, hasCurrentTarget: true,
                                                         context: clearContext, calibration: nil)
        guard case .unableToConfirm = result else { return XCTFail("Feature equality is not object identity") }
    }

    func testMissingSegmentationNeverClaimsAbsence() {
        let result = IndoorTargetComparisonPolicy.assess(distance: nil, hasCurrentTarget: false,
                                                         context: clearContext, calibration: fixtureCalibration)
        guard case .unableToConfirm = result else { return XCTFail("Foreground segmentation can fail on a present object") }
    }

    func testEachUnknownObservationConditionBlocksJudgment() {
        for index in 0..<4 {
            var context = clearContext
            switch index {
            case 0: context.comparableView = false
            case 1: context.originalRegionVisible = false
            case 2: context.imageQualityAcceptable = false
            default: context.occlusionRuledOut = false
            }
            for distance: Float in [0, 10] {
                let result = IndoorTargetComparisonPolicy.assess(distance: distance, hasCurrentTarget: true,
                                                                 context: context, calibration: fixtureCalibration)
                guard case .unableToConfirm = result else { return XCTFail("Unknown evidence must block both positive and negative claims") }
            }
        }
    }

    func testInvalidDistancesAndAmbiguousDistanceStayUnknown() {
        for distance: Float in [.nan, .infinity, -1, 0.5] {
            let result = IndoorTargetComparisonPolicy.assess(distance: distance, hasCurrentTarget: true,
                                                             context: clearContext, calibration: fixtureCalibration)
            guard case .unableToConfirm = result else { return XCTFail("Invalid or ambiguous distance must not classify") }
        }
    }

    func testEvidenceMetadataRoundTripsWithoutLosingCoordinatesOrRevision() throws {
        let evidence = IndoorTargetEvidence(maskPNG: Data([1, 2]), normalizedBounds: CGRect(x: 0.2, y: 0.3, width: 0.1, height: 0.4),
                                            featurePrintData: Data([3]), featureRevision: 2, imageWidth: 960, imageHeight: 1280)
        let decoded = try JSONDecoder().decode(IndoorTargetEvidence.self, from: JSONEncoder().encode(evidence))
        XCTAssertEqual(decoded.normalizedBounds, evidence.normalizedBounds)
        XCTAssertEqual(decoded.maskPNG, evidence.maskPNG)
        XCTAssertEqual(decoded.featurePrintData, evidence.featurePrintData)
        XCTAssertEqual(decoded.featureRevision, evidence.featureRevision)
    }

    @available(iOS 17.0, macOS 14.0, *)
    func testInvalidTapRejectedBeforeVisionExecutes() throws {
        let context = try XCTUnwrap(CGContext(data: nil, width: 4, height: 4, bitsPerComponent: 8, bytesPerRow: 16,
                                             space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
        let image = try XCTUnwrap(context.makeImage())
        let worker = IndoorTargetVision()
        for point in [CGPoint(x: -0.1, y: 0.5), CGPoint(x: 1, y: 0.5), CGPoint(x: CGFloat.nan, y: 0)] {
            XCTAssertThrowsError(try worker.extract(image: image, normalizedTap: point)) { error in
                guard case IndoorTargetVisionError.invalidPoint = error else { return XCTFail("Wrong error: \(error)") }
            }
        }
    }
}
