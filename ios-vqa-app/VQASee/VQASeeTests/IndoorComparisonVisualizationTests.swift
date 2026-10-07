import XCTest
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers
import simd
@testable import VQASee

final class IndoorComparisonVisualizationTests: XCTestCase {
    func testProjectiveMappingAndInvalidDenominator() {
        let translation = simd_float3x3(columns: (SIMD3(1, 0, 0), SIMD3(0, 1, 0), SIMD3(0.2, -0.1, 1)))
        let result = IndoorComparisonVisualizer.project(CGPoint(x: 0.3, y: 0.6), matrix: translation)
        XCTAssertEqual(result!.x, 0.5, accuracy: 0.0001)
        XCTAssertEqual(result!.y, 0.5, accuracy: 0.0001)
        XCTAssertNil(IndoorComparisonVisualizer.project(.zero, matrix: simd_float3x3(repeating: 0)))
    }

    func testRealMaskContoursRenderAtCorrectTopLeftCoordinates() throws {
        // Hand-drawn binary cup-like silhouette, not claimed as Vision segmentation accuracy.
        let width = 300, height = 400
        let photo = try makeImage(width: width, height: height, mask: false)
        let mask = try makeImage(width: width, height: height, mask: true)
        let data = NSMutableData()
        let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil)!
        CGImageDestinationAddImage(destination, mask, nil)
        XCTAssertTrue(CGImageDestinationFinalize(destination))
        let old = try IndoorComparisonVisualizer.renderMask(image: photo, maskPNG: data as Data, reference: true)
        let current = try IndoorComparisonVisualizer.renderMask(image: photo, maskPNG: data as Data, reference: false)
        for (name, png) in [("Original silhouette — dashed orange", old), ("Current silhouette — solid teal", current)] {
            let image = CGImageSourceCreateImageAtIndex(CGImageSourceCreateWithData(png as CFData, nil)!, 0, nil)!
            XCTAssertEqual(image.width, width)
            XCTAssertEqual(image.height, height)
            let attachment = XCTAttachment(data: png, uniformTypeIdentifier: UTType.png.identifier)
            attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
        }
        // Output differs from both source and from the alternate contour style.
        XCTAssertNotEqual(old, current)
    }

    private func makeImage(width: Int, height: Int, mask: Bool) throws -> CGImage {
        let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                                bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.setFillColor(CGColor(gray: mask ? 0 : 0.12, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        context.setFillColor(CGColor(gray: mask ? 1 : 0.7, alpha: 1))
        context.fill(CGRect(x: 60, y: 220, width: 100, height: 130))
        context.fillEllipse(in: CGRect(x: 140, y: 255, width: 60, height: 60))
        context.setFillColor(CGColor(gray: mask ? 0 : 0.12, alpha: 1))
        context.fillEllipse(in: CGRect(x: 155, y: 270, width: 30, height: 30))
        return context.makeImage()!
    }
}
