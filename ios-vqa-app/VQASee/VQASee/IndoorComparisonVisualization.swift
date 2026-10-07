import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers
import Vision
import simd

nonisolated struct IndoorComparisonVisualizationResult: Sendable {
    let referenceAnnotatedPNG: Data
    let currentAnnotatedPNG: Data
    let statusMessage: String
    let currentTarget: IndoorTargetEvidence?
    let alignmentSucceeded: Bool
}

/// Rendered evidence, never a calibrated identity or disappearance classifier.
@available(iOS 17.0, macOS 14.0, *)
nonisolated enum IndoorComparisonVisualizer {
    static func render(referenceImage: CGImage, referenceTarget: IndoorTargetEvidence,
                       currentImage: CGImage, currentTap: CGPoint?) throws -> IndoorComparisonVisualizationResult {
        let referencePaths = try contours(referenceTarget.maskPNG)
        let alignment = try? registration(reference: referenceImage, current: currentImage,
                                         excluded: referenceTarget.normalizedBounds)
        let mappedPaths = alignment.map { matrix in referencePaths.compactMap { mapped($0, matrix: matrix) } } ?? []
        let inferredTap = alignment.flatMap { project(CGPoint(x: referenceTarget.normalizedBounds.midX,
                                                              y: referenceTarget.normalizedBounds.midY), matrix: $0) }
        let tap = currentTap ?? inferredTap
        let target: IndoorTargetEvidence?
        if let tap, (0..<1).contains(tap.x), (0..<1).contains(tap.y) {
            target = try? IndoorTargetVision().extract(image: currentImage, normalizedTap: tap, requireFeaturePrint: false)
        } else { target = nil }
        let currentPaths = target.flatMap { try? contours($0.maskPNG) } ?? []
        let referencePNG = try draw(referenceImage, oldPaths: referencePaths, currentPaths: [])
        let currentPNG = try draw(currentImage, oldPaths: mappedPaths, currentPaths: currentPaths)
        let message: String
        if target != nil {
            message = alignment != nil
                ? "橙色虚线是原轮廓的对齐参考，青色实线是当前分出的轮廓；是否同一物品尚未确认。"
                : "已画出原轮廓和当前轮廓；视角未对齐，两张图分开比较。"
        } else {
            message = alignment != nil
                ? "橙色虚线标出原轮廓的参考位置；当前未分出物品，可能遮挡或移走，尚不能确认。"
                : "原照片已标出物品轮廓；当前视角未对齐，请点当前照片中的物品查看轮廓。"
        }
        return .init(referenceAnnotatedPNG: referencePNG, currentAnnotatedPNG: currentPNG,
                     statusMessage: message, currentTarget: target, alignmentSucceeded: alignment != nil)
    }

    static func renderMask(image: CGImage, maskPNG: Data, reference: Bool) throws -> Data {
        let paths = try contours(maskPNG)
        return try draw(image, oldPaths: reference ? paths : [], currentPaths: reference ? [] : paths)
    }

    /// Normalized top-left coordinates throughout; matrices act in that same space.
    static func project(_ point: CGPoint, matrix: simd_float3x3) -> CGPoint? {
        let vector = matrix * SIMD3<Float>(Float(point.x), Float(point.y), 1)
        guard vector.x.isFinite, vector.y.isFinite, vector.z.isFinite, abs(vector.z) > 0.00001 else { return nil }
        return CGPoint(x: CGFloat(vector.x / vector.z), y: CGFloat(vector.y / vector.z))
    }

    private static func mapped(_ path: CGPath, matrix: simd_float3x3) -> CGPath? {
        let result = CGMutablePath()
        var invalid = false
        path.applyWithBlock { pointer in
            let element = pointer.pointee
            switch element.type {
            case .moveToPoint, .addLineToPoint:
                guard let point = project(element.points[0], matrix: matrix),
                      (-0.25...1.25).contains(point.x), (-0.25...1.25).contains(point.y) else { invalid = true; return }
                if element.type == .moveToPoint { result.move(to: point) } else { result.addLine(to: point) }
            case .closeSubpath: result.closeSubpath()
            default: invalid = true
            }
        }
        return invalid ? nil : result
    }

    private static func contours(_ png: Data) throws -> [CGPath] {
        let request = VNDetectContoursRequest()
        request.detectsDarkOnLight = false
        request.contrastAdjustment = 1
        request.maximumImageDimension = 512
        try VNImageRequestHandler(data: png, orientation: .up).perform([request])
        guard let observation = request.results?.first else { throw IndoorTargetVisionError.emptyMask }
        // Vision contours are bottom-left; convert once to the source image's top-left convention.
        var transform = CGAffineTransform(a: 1, b: 0, c: 0, d: -1, tx: 0, ty: 1)
        return observation.topLevelContours.compactMap { $0.normalizedPath.copy(using: &transform) }
    }

    private static func registration(reference: CGImage, current: CGImage, excluded: CGRect) throws -> simd_float3x3? {
        // Same dimensions are required by Vision. Small inputs bound latency; quality is
        // verified on the background, excluding the remembered object itself.
        let width = 192, height = 192
        let lhs = try resized(reference, width: width, height: height)
        let rhs = try resized(current, width: width, height: height)
        let request = VNHomographicImageRegistrationRequest(targetedCGImage: lhs, options: [:])
        try VNImageRequestHandler(cgImage: rhs, orientation: .up).perform([request])
        guard let result = request.results?.first else { return nil }
        let pixel = result.warpTransform
        // Vision's pixel coordinates have a bottom-left origin.
        let normalizedToPixel = simd_float3x3(columns: (
            SIMD3(Float(width), 0, 0), SIMD3(0, -Float(height), 0), SIMD3(0, Float(height), 1)))
        let matrix = normalizedToPixel.inverse * pixel * normalizedToPixel
        guard backgroundMatches(lhs, rhs, matrix: matrix, excluded: excluded) else { return nil }
        return matrix
    }

    private static func backgroundMatches(_ reference: CGImage, _ current: CGImage,
                                          matrix: simd_float3x3, excluded: CGRect) -> Bool {
        guard let lhs = gray(reference), let rhs = gray(current) else { return false }
        let w = reference.width, h = reference.height
        var differences: [Double] = [], unaligned: [Double] = [], referenceValues: [Double] = []
        for y in stride(from: 8, to: h - 8, by: 8) {
            for x in stride(from: 8, to: w - 8, by: 8) {
                let p = CGPoint(x: CGFloat(x) / CGFloat(w), y: CGFloat(y) / CGFloat(h))
                guard !excluded.insetBy(dx: -0.04, dy: -0.04).contains(p),
                      let q = project(p, matrix: matrix), (0..<1).contains(q.x), (0..<1).contains(q.y) else { continue }
                let value = Double(lhs[y * w + x])
                differences.append(abs(value - Double(rhs[Int(q.y * CGFloat(h)) * w + Int(q.x * CGFloat(w))])))
                unaligned.append(abs(value - Double(rhs[y * w + x])))
                referenceValues.append(value)
            }
        }
        guard differences.count >= 180 else { return false }
        let mean = referenceValues.reduce(0, +) / Double(referenceValues.count)
        let variance = referenceValues.reduce(0) { $0 + pow($1 - mean, 2) } / Double(referenceValues.count)
        // Experimental conservative display gate, not a disappearance decision threshold.
        let alignedError = differences.reduce(0, +) / Double(differences.count)
        let baselineError = unaligned.reduce(0, +) / Double(unaligned.count)
        return variance > 100 && alignedError < 18
            && (baselineError < 8 || alignedError < baselineError * 0.8)
    }

    private static func gray(_ image: CGImage) -> [UInt8]? {
        var pixels = [UInt8](repeating: 0, count: image.width * image.height)
        let success = pixels.withUnsafeMutableBytes { bytes -> Bool in
            guard let context = CGContext(data: bytes.baseAddress, width: image.width, height: image.height,
                                          bitsPerComponent: 8, bytesPerRow: image.width,
                                          space: CGColorSpaceCreateDeviceGray(), bitmapInfo: 0) else { return false }
            context.translateBy(x: 0, y: CGFloat(image.height)); context.scaleBy(x: 1, y: -1)
            context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
            return true
        }
        return success ? pixels : nil
    }

    private static func resized(_ image: CGImage, width: Int, height: Int) throws -> CGImage {
        guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                                      bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { throw IndoorTargetVisionError.imageEncoding }
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        guard let output = context.makeImage() else { throw IndoorTargetVisionError.imageEncoding }
        return output
    }

    private static func draw(_ image: CGImage, oldPaths: [CGPath], currentPaths: [CGPath]) throws -> Data {
        let w = image.width, h = image.height
        guard let context = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                      space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { throw IndoorTargetVisionError.imageEncoding }
        context.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
        context.translateBy(x: 0, y: CGFloat(h)); context.scaleBy(x: CGFloat(w), y: -CGFloat(h))
        let line = 3 / CGFloat(max(w, h))
        context.setLineWidth(line); context.setLineJoin(.round)
        context.setStrokeColor(CGColor(red: 1, green: 0.58, blue: 0.15, alpha: 1))
        context.setLineDash(phase: 0, lengths: [line * 3, line * 2])
        for path in oldPaths { context.addPath(path); context.strokePath() }
        context.setLineDash(phase: 0, lengths: [])
        context.setStrokeColor(CGColor(red: 0, green: 0.85, blue: 0.75, alpha: 1))
        for path in currentPaths { context.addPath(path); context.strokePath() }
        guard let output = context.makeImage() else { throw IndoorTargetVisionError.imageEncoding }
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else { throw IndoorTargetVisionError.imageEncoding }
        CGImageDestinationAddImage(destination, output, nil)
        guard CGImageDestinationFinalize(destination) else { throw IndoorTargetVisionError.imageEncoding }
        return data as Data
    }
}
