import Foundation
import Vision
import ImageIO
import CoreGraphics
import CoreVideo

/// Local learned correspondence only; matches do not establish object identity or depth.
actor SpatialLocalCorrespondence {
    enum Failure: Error { case incompatibleFrames, unreadableImage, unsupportedNetwork, missingFlow, invalidFlow, calibrationFailed
        case calibrationMismatch(forward: [Float], reverse: [Float], forwardCount: Int, reverseCount: Int)
    }

    struct Calibration: Sendable {
        var elapsedSeconds: Double
        var forwardMedianPixels: [Float]
        var reverseMedianPixels: [Float]
        var forwardMatchCount: Int
        var reverseMatchCount: Int
        var triangulatedSupportCount: Int
        /// Controlled synthetic plane only; never a real-scene metric accuracy claim.
        var medianDepthErrorMeters: Float
    }
    private var calibrationResult: Result<Calibration, Error>?

    /// One real self-check per actor, including cached explicit failures; no automatic retry.
    func calibrate() throws -> Calibration {
        if let calibrationResult { return try calibrationResult.get() }
        let result = Result { try Self.verifyCalibration() }
        calibrationResult = result
        return try result.get()
    }

    func matches(source: SpatialKeyframe, target: SpatialKeyframe, directory: URL) throws -> [SpatialImageMatch] {
        guard source.width == target.width, source.height == target.height,
              source.width > 0, source.height > 0, source.pose.epoch == target.pose.epoch else { throw Failure.incompatibleFrames }
        _ = try calibrate() // Gate geometry before loading any private photo.
        let a = try Self.image(directory.appendingPathComponent(source.imageFile), frame: source)
        let b = try Self.image(directory.appendingPathComponent(target.imageFile), frame: target)
        return try Self.rawMatches(source: a, target: b, nativeWidth: source.width, nativeHeight: source.height)
    }

    /// Internal entry point lets real Vision tests use known asymmetric image translations.
    static func matches(source: CGImage, target: CGImage, nativeWidth: Int, nativeHeight: Int) throws -> [SpatialImageMatch] {
        _ = try verifyCalibration()
        return try rawMatches(source: source, target: target, nativeWidth: nativeWidth, nativeHeight: nativeHeight)
    }

    /// Normalized native pixel-index coordinate, accounting for resize sampling centres.
    static func nativeCoordinate(pixelIndex: Float, workingExtent: Int, nativeExtent: Int) -> Float {
        ((pixelIndex + 0.5) * Float(nativeExtent) / Float(workingExtent) - 0.5) / Float(nativeExtent)
    }

    private static func rawMatches(source: CGImage, target: CGImage, nativeWidth: Int, nativeHeight: Int) throws -> [SpatialImageMatch] {
        guard source.width == target.width, source.height == target.height,
              nativeWidth > 0, nativeHeight > 0 else { throw Failure.incompatibleFrames }
        let a = try resized(source), b = try resized(target)
        let forward = try flow(source: a, target: b)
        let reverse = try flow(source: b, target: a)
        let width = a.width, height = a.height
        let scaleX = Float(nativeWidth) / Float(width), scaleY = Float(nativeHeight) / Float(height)
        var result: [SpatialImageMatch] = []
        // 15 x 10 interior grid: at most 150 candidate correspondences.
        for row in 0..<10 {
            for column in 0..<15 {
                let x = Float(column + 1) * Float(width - 1) / 16
                let y = Float(row + 1) * Float(height - 1) / 11
                guard let f = forward.sample(x, y), let r = reverse.sample(x + f.0, y + f.1) else { continue }
                let error = hypot((f.0 + r.0) * scaleX, (f.1 + r.1) * scaleY)
                guard error.isFinite, error <= 1.5 else { continue }
                // K consumes native pixel indices; resize sampling centres must be mapped back first.
                result.append(SpatialImageMatch(sourcePixel: [
                    nativeCoordinate(pixelIndex: x, workingExtent: width, nativeExtent: nativeWidth),
                    nativeCoordinate(pixelIndex: y, workingExtent: height, nativeExtent: nativeHeight)],
                    targetPixel: [
                    nativeCoordinate(pixelIndex: x + f.0, workingExtent: width, nativeExtent: nativeWidth),
                    nativeCoordinate(pixelIndex: y + f.1, workingExtent: height, nativeExtent: nativeHeight)], cycleErrorPixels: error))
            }
        }
        return result
    }

    /// Device self-check: a real learned-network translation, with no private input or mocked flow.
    static func verifyCalibration() throws -> Calibration {
        let began = ProcessInfo.processInfo.systemUptime
        let width = 768, height = 512
        let a = try texture(width: width, height: height, dx: 0, dy: 0)
        let b = try texture(width: width, height: height, dx: 24, dy: 16)
        let forward = try rawMatches(source: a, target: b, nativeWidth: width, nativeHeight: height)
        let reverse = try rawMatches(source: b, target: a, nativeWidth: width, nativeHeight: height)
        func median(_ matches: [SpatialImageMatch]) throws -> [Float] {
            guard matches.count > 15 else { throw Failure.calibrationFailed }
            let x = matches.map { ($0.targetPixel[0]-$0.sourcePixel[0])*Float(width) }.sorted()
            let y = matches.map { ($0.targetPixel[1]-$0.sourcePixel[1])*Float(height) }.sorted()
            return [x[x.count/2], y[y.count/2]]
        }
        let f = try median(forward), r = try median(reverse)
        guard abs(f[0]-24) <= 2, abs(f[1]-16) <= 2,
              abs(r[0]+24) <= 2, abs(r[1]+16) <= 2 else {
            throw Failure.calibrationMismatch(forward: f, reverse: r, forwardCount: forward.count, reverseCount: reverse.count)
        }
        let cameras = calibrationFrames()
        let supports = forward.compactMap { SpatialObservationGeometry.triangulate($0, source: cameras.source, target: cameras.target) }
        let depthErrors = supports.map { abs($0.position.z + 3) }.sorted()
        guard supports.count > 15 else { throw Failure.calibrationFailed }
        let medianDepthError = depthErrors[depthErrors.count / 2]
        guard medianDepthError.isFinite, medianDepthError <= 0.15 else { throw Failure.calibrationFailed }
        return Calibration(elapsedSeconds: ProcessInfo.processInfo.systemUptime-began,
            forwardMedianPixels: f, reverseMedianPixels: r,
            forwardMatchCount: forward.count, reverseMatchCount: reverse.count,
            triangulatedSupportCount: supports.count, medianDepthErrorMeters: medianDepthError)
    }

    /// Known synthetic geometry, not a measured room. For a plane Z=-3,
    /// target C=(-.12,+.08,0) gives du=600*.12/3=+24 and dv=600*.08/3=+16.
    /// Image Y points down while AR camera Y points up and forward is -Z.
    static func calibrationFrames() -> (source: SpatialKeyframe, target: SpatialKeyframe) {
        func frame(_ id: String, time: Double, x: Float, y: Float) -> SpatialKeyframe {
            let pose = SpatialPose(time: time, position: SpatialPoint(x: x, y: y, z: 0),
                forward: SpatialPoint(x: 0, y: 0, z: -1), epoch: 0)
            return SpatialKeyframe(id: id, time: time, pose: pose,
                intrinsics: [600,0,0,0,600,0,384,256,1], width: 768, height: 512,
                imageFile: "synthetic-calibration-only.jpg",
                cameraToWorld: [1,0,0,0,0,1,0,0,0,0,1,0,x,y,0,1])
        }
        return (frame("synthetic-source", time: 0, x: 0, y: 0),
                frame("synthetic-target", time: 1, x: -0.12, y: 0.08))
    }

    private static func texture(width: Int, height: Int, dx: Int, dy: Int) throws -> CGImage {
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        for y in 0..<height {
            for x in 0..<width {
                let sx = x-dx, sy = y-dy, offset = (y*width+x)*4
                if sx >= 0, sy >= 0, sx < width, sy < height {
                    // Stable asymmetric multiscale texture; translation is defined in native raster pixels.
                    let tile = (sx/23 * 73 + sy/31 * 151 + (sx/61)*(sy/47)*29) % 211
                    bytes[offset] = UInt8(20 + tile)
                    bytes[offset+1] = UInt8(20 + (tile*3 + sx/7) % 211)
                    bytes[offset+2] = UInt8(20 + (tile*7 + sy/9) % 211)
                }
                bytes[offset+3] = 255
            }
        }
        guard let provider = CGDataProvider(data: Data(bytes) as CFData) else { throw Failure.calibrationFailed }
        guard let image = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32,
            bytesPerRow: width*4, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue),
            provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent) else { throw Failure.calibrationFailed }
        return image
    }

    private static func image(_ url: URL, frame: SpatialKeyframe) throws -> CGImage {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil),
              image.width == frame.width, image.height == frame.height else { throw Failure.unreadableImage }
        return image // Native JPEG axes; do not apply presentation/EXIF rotation.
    }

    private static func resized(_ image: CGImage) throws -> CGImage {
        let scale = min(1, 384 / Double(max(image.width, image.height)))
        let width = max(1, Int((Double(image.width) * scale).rounded()))
        let height = max(1, Int((Double(image.height) * scale).rounded()))
        guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { throw Failure.unreadableImage }
        context.interpolationQuality = .high
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        guard let resized = context.makeImage() else { throw Failure.unreadableImage }
        return resized
    }

    private struct Field {
        var width: Int; var height: Int; var values: [Float]
        func sample(_ x: Float, _ y: Float) -> (Float, Float)? {
            guard x.isFinite, y.isFinite, x >= 0, y >= 0, x < Float(width - 1), y < Float(height - 1) else { return nil }
            let ix = Int(x), iy = Int(y), dx = x - Float(ix), dy = y - Float(iy)
            func value(_ component: Int) -> Float {
                let p = (iy * width + ix) * 2 + component
                return values[p] * (1-dx) * (1-dy) + values[p+2] * dx * (1-dy)
                    + values[p+width*2] * (1-dx) * dy + values[p+width*2+2] * dx * dy
            }
            let u = value(0), v = value(1)
            return u.isFinite && v.isFinite ? (u,v) : nil
        }
    }

    private static func flow(source: CGImage, target: CGImage) throws -> Field {
        try autoreleasepool {
            guard VNGenerateOpticalFlowRequest.supportedRevisions.contains(2) else { throw Failure.unsupportedNetwork }
            // SDK contract: targeted source -> handler target. Real translation tests gate this convention.
            let request = VNGenerateOpticalFlowRequest(targetedCGImage: source, options: [:])
            request.revision = 2
            request.computationAccuracy = .high
            request.keepNetworkOutput = false
            request.outputPixelFormat = kCVPixelFormatType_TwoComponent32Float
            try VNImageRequestHandler(cgImage: target, options: [:]).perform([request])
            guard let buffer = request.results?.first?.pixelBuffer,
                  CVPixelBufferGetPixelFormatType(buffer) == kCVPixelFormatType_TwoComponent32Float,
                  CVPixelBufferGetWidth(buffer) == source.width,
                  CVPixelBufferGetHeight(buffer) == source.height else { throw Failure.missingFlow }
            guard CVPixelBufferLockBaseAddress(buffer, .readOnly) == kCVReturnSuccess else { throw Failure.invalidFlow }
            defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
            guard let base = CVPixelBufferGetBaseAddress(buffer) else { throw Failure.invalidFlow }
            let stride = CVPixelBufferGetBytesPerRow(buffer)
            var values = [Float](); values.reserveCapacity(source.width * source.height * 2)
            for row in 0..<source.height {
                let pointer = base.advanced(by: row * stride).assumingMemoryBound(to: Float.self)
                values.append(contentsOf: UnsafeBufferPointer(start: pointer, count: source.width * 2))
            }
            return Field(width: source.width, height: source.height, values: values)
        }
    }
}
