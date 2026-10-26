import Foundation
import simd
import Vision
import ImageIO
import CoreGraphics
import CoreVideo

/// Local learned correspondence only; matches do not establish object identity or depth.
actor SpatialLocalCorrespondence {
    enum Failure: Error { case incompatibleFrames, unreadableImage, unsupportedNetwork, missingFlow, invalidFlow, calibrationFailed
        case insufficientCalibrationMatches(stage: String, count: Int)
        case calibrationDepthMismatch(stage: String, supports: Int, medianErrorMeters: Float)
        case gridCalibrationMismatch(candidates: Int, valid: Int, supports: Int, medianErrorMeters: Float)
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
        var depthGridCandidateCount: Int
        var depthGridValidCount: Int
        var depthGridReasonCounts: [String:Int]
        var depthGridTriangulatedCount: Int
        var depthGridMedianDepthErrorMeters: Float
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

    /// Samples requested native pixel-index coordinates while keeping both full images and K unchanged.
    func matches(source:SpatialKeyframe,target:SpatialKeyframe,directory:URL,nativePixels:[[Float]]) throws -> [SpatialImageMatch] {
        guard source.width == target.width,source.height == target.height,source.pose.epoch == target.pose.epoch,
              nativePixels.allSatisfy({$0.count == 2 && $0.allSatisfy {$0.isFinite && $0>=0 && $0<=1}}) else {throw Failure.incompatibleFrames}
        _ = try calibrate()
        guard !nativePixels.isEmpty else {return []}
        let a=try Self.image(directory.appendingPathComponent(source.imageFile),frame:source)
        let b=try Self.image(directory.appendingPathComponent(target.imageFile),frame:target)
        return try Self.rawMatches(source:a,target:b,nativeWidth:source.width,nativeHeight:source.height,nativePixels:nativePixels)
    }

    /// Every one of the 64x48 source cells returns an outcome, including rejected evidence.
    func depthGrid(source: SpatialKeyframe, target: SpatialKeyframe, directory: URL) throws -> [SpatialDepthGridMatch] {
        try depthGridDetailed(source:source,target:target,directory:directory).grid
    }

    func depthGridDetailed(source: SpatialKeyframe, target: SpatialKeyframe, directory: URL) throws -> SpatialDepthGridResult {
        guard source.width == target.width, source.height == target.height, source.pose.epoch == target.pose.epoch,
              let h = SpatialObservationGeometry.rotationHomography(source:source,target:target) else { throw Failure.incompatibleFrames }
        _ = try calibrate()
        let began = ProcessInfo.processInfo.systemUptime
        let a = try Self.image(directory.appendingPathComponent(source.imageFile),frame:source)
        let b = try Self.image(directory.appendingPathComponent(target.imageFile),frame:target)
        var result = try Self.depthGridResult(source:a,target:b,nativeWidth:source.width,nativeHeight:source.height,homography:h)
        result.diagnostics.sourceFrameID = source.id; result.diagnostics.targetFrameID = target.id
        result.diagnostics.stageSeconds["totalExcludingCalibration"] = ProcessInfo.processInfo.systemUptime-began
        return result
    }

    static func depthGrid(source: CGImage, target: CGImage, nativeWidth: Int, nativeHeight: Int) throws -> [SpatialDepthGridMatch] {
        try depthGridResult(source:source,target:target,nativeWidth:nativeWidth,nativeHeight:nativeHeight,homography:matrix_identity_float3x3).grid
    }

    static func depthGridResult(source: CGImage, target: CGImage, nativeWidth: Int, nativeHeight: Int, homography: simd_float3x3) throws -> SpatialDepthGridResult {
        guard source.width == target.width, source.height == target.height, nativeWidth > 0, nativeHeight > 0 else { throw Failure.incompatibleFrames }
        let began = ProcessInfo.processInfo.systemUptime
        let a = try resized(source), b = try resized(target)
        guard let resize = SpatialObservationGeometry.resizePixelTransform(nativeWidth:nativeWidth,nativeHeight:nativeHeight,width:a.width,height:a.height) else { throw Failure.incompatibleFrames }
        let workingHomography = resize * homography * resize.inverse
        let aligned = try rotationWarp(image:b,width:a.width,height:a.height,homography:workingHomography)
        let warpEnded = ProcessInfo.processInfo.systemUptime
        let forward = try flow(source:a,target:aligned.image), reverse = try flow(source:aligned.image,target:a)
        let flowEnded = ProcessInfo.processInfo.systemUptime
        let ga = try Gray(a), gb = try Gray(aligned.image,validMask:aligned.valid)
        let width = a.width, height = a.height
        let sx = Float(nativeWidth)/Float(width), sy = Float(nativeHeight)/Float(height)
        var result: [SpatialDepthGridMatch] = []
        result.reserveCapacity(3072)
        for row in 0..<48 { for column in 0..<64 {
            let x = (Float(column)+0.5)*Float(width)/64-0.5
            let y = (Float(row)+0.5)*Float(height)/48-0.5
            guard let guess = forward.sample(x,y) else { result.append(.init(match:nil,state:.invalidFlow)); continue }
            let refined = refine(source:ga,target:gb,x:x,y:y,guess:guess)
            guard let f = refined.displacement else { result.append(.init(match:nil,state:refined.state)); continue }
            guard let backGuess = reverse.sample(x+f.0,y+f.1) else { result.append(.init(match:nil,state:.outsideView)); continue }
            let backward = refine(source:gb,target:ga,x:x+f.0,y:y+f.1,guess:backGuess)
            guard let r = backward.displacement else { result.append(.init(match:nil,state:backward.state)); continue }
            let cycle = hypot((f.0+r.0)*sx,(f.1+r.1)*sy)
            guard cycle.isFinite, cycle <= 1.5 else { result.append(.init(match:nil,state:.cycleInconsistent)); continue }
            guard let targetPixel = SpatialObservationGeometry.mapPixel(SIMD2(x+f.0,y+f.1),homography:workingHomography) else {
                result.append(.init(match:nil,state:.outsideView)); continue
            }
            let match = SpatialImageMatch(sourcePixel:[nativeCoordinate(pixelIndex:x,workingExtent:width,nativeExtent:nativeWidth),nativeCoordinate(pixelIndex:y,workingExtent:height,nativeExtent:nativeHeight)],
                targetPixel:[nativeCoordinate(pixelIndex:targetPixel.x,workingExtent:width,nativeExtent:nativeWidth),nativeCoordinate(pixelIndex:targetPixel.y,workingExtent:height,nativeExtent:nativeHeight)],cycleErrorPixels:cycle)
            result.append(.init(match:match,state:.valid))
        }}
        let refinementEnded = ProcessInfo.processInfo.systemUptime
        return SpatialDepthGridResult(grid:result,diagnostics:SpatialDepthPairDiagnostics(sourceFrameID:"",targetFrameID:"",candidateCount:result.count,
            reasonCounts:Dictionary(grouping:result,by:{ $0.state.rawValue }).mapValues(\.count),
            warpValidPixelCount:aligned.valid.filter { $0 }.count,warpPixelCount:aligned.valid.count,
            stageSeconds:["resizeWarp":warpEnded-began,"flow":flowEnded-warpEnded,"refinement":refinementEnded-flowEnded]))
    }

    /// Image-domain refinement can be verified independently of Vision network availability.
    static func refinedDisplacement(source: CGImage, target: CGImage, pixel: [Float], proposal: [Float]) throws -> [Float]? {
        try refinementOutcome(source:source,target:target,pixel:pixel,proposal:proposal).displacement
    }

    static func refinementOutcome(source: CGImage, target: CGImage, pixel: [Float], proposal: [Float], targetValidMask: [Bool]? = nil) throws -> (displacement: [Float]?, state: SpatialDepthPixelState) {
        guard source.width == target.width, source.height == target.height, pixel.count == 2, proposal.count == 2,
              pixel.allSatisfy(\.isFinite), proposal.allSatisfy(\.isFinite) else { throw Failure.incompatibleFrames }
        let result = refine(source:try Gray(source),target:try Gray(target,validMask:targetValidMask),x:pixel[0],y:pixel[1],guess:(proposal[0],proposal[1]))
        return (result.displacement.map { [$0.0,$0.1] },result.state)
    }

    /// Exact Swift/CG objective trace, used only to inspect a known synthetic failure.
    static func refinementDebugScores(source: CGImage, target: CGImage, pixel: [Float], proposal: [Float], truth: [Float]) throws -> [String:Float] {
        guard pixel.count == 2, proposal.count == 2, truth.count == 2 else { throw Failure.incompatibleFrames }
        var report: [String:Float] = [:]
        _ = refine(source:try Gray(source),target:try Gray(target),x:pixel[0],y:pixel[1],guess:(proposal[0],proposal[1]),probe:(truth[0],truth[1]),trace:{ report = $0 })
        return report
    }

    /// A network vector is only a bounded search proposal. Real patches determine the final match.
    private static func refine(source: Gray, target: Gray, x: Float, y: Float, guess: (Float,Float), probe: (Float,Float)? = nil, trace: (([String:Float])->Void)? = nil) -> (displacement: (Float,Float)?, state: SpatialDepthPixelState) {
        var reference: [Float] = []
        for dy in -4...4 { for dx in -4...4 {
            guard let value = source.sample(x+Float(dx),y+Float(dy)) else { return (nil,.outsideView) }
            reference.append(value)
        }}
        let mean = reference.reduce(0,+)/81
        let centred = reference.map { $0-mean }
        let variance = centred.reduce(0) { $0+$1*$1 }
        guard variance/81 > 0.0004 else { return (nil,.lowTexture) }
        // A truncated search cannot establish a unique peak: the true correspondence
        // may lie in its missing part. Include patch radius and bilinear footprint.
        guard target.containsValidRectangle(minX:x+guess.0-12,minY:y+guess.1-12,
            maxX:x+guess.0+12,maxY:y+guess.1+12) else { return (nil,.outsideView) }
        func score(_ dx: Float,_ dy: Float) -> Float? {
            var samples: [Float] = []
            for oy in -4...4 { for ox in -4...4 {
                guard let value = target.sample(x+dx+Float(ox),y+dy+Float(oy)) else { return nil }
                samples.append(value)
            }}
            let m = samples.reduce(0,+)/81
            var v: Float = 0, cov: Float = 0
            for i in 0..<81 { let value = samples[i]-m; v += value*value; cov += centred[i]*value }
            guard v/81 > 0.0004 else { return nil }
            return cov/sqrt(variance*v)
        }
        var candidates: [(Float,Float,Float)] = []
        // Enumerate integer DISPLACEMENTS, retaining the fractional source sampling phase.
        // Absolute target rounding would compare differently interpolated image patches.
        let targetX = guess.0.rounded(), targetY = guess.1.rounded()
        for dy in -8...8 { for dx in -8...8 {
            let u = targetX+Float(dx), v = targetY+Float(dy)
            guard abs(u-guess.0) <= 8, abs(v-guess.1) <= 8 else { continue }
            if let ncc = score(u,v), ncc.isFinite { candidates.append((u,v,ncc)) }
        }}
        guard var best = candidates.max(by:{ $0.2 < $1.2 }) else { return (nil,.outsideView) }
        let coarseBest = best
        // NCC over bilinearly sampled, correlated 2D patches is not a separable parabola.
        // Evaluate the actual joint image objective at successively finer bounded offsets.
        for step: Float in [0.5,0.25,0.125,0.0625,0.03125] {
            let centre = best
            for dy in -1...1 { for dx in -1...1 {
                let u = centre.0+Float(dx)*step, v = centre.1+Float(dy)*step
                if abs(u-guess.0) <= 8, abs(v-guess.1) <= 8,
                   let ncc = score(u,v), ncc > best.2 { best = (u,v,ncc) }
            }}
        }
        let alternative = candidates.filter { hypot($0.0-best.0,$0.1-best.1) >= 2 }.map { $0.2 }.max() ?? -1
        if let trace {
            var report: [String:Float] = ["sourceX":x,"sourceY":y,"coarseU":coarseBest.0,"coarseV":coarseBest.1,"coarseNCC":coarseBest.2,
                "finalU":best.0,"finalV":best.1,"finalNCC":best.2,"alternativeNCC":alternative]
            if let probe {
                report["trueNCC"] = score(probe.0,probe.1) ?? -2
                for dy in -1...1 { for dx in -1...1 { report["trueNeighborhood_\(dx)_\(dy)"] = score(probe.0+Float(dx),probe.1+Float(dy)) ?? -2 }}
                for dy in -1...1 { for dx in -1...1 { report["finalNeighborhood_\(dx)_\(dy)"] = score(best.0+Float(dx)*0.25,best.1+Float(dy)*0.25) ?? -2 }}
            }
            trace(report)
        }
        guard abs(best.0-guess.0) < 7.5, abs(best.1-guess.1) < 7.5, best.2 >= 0.8 else { return (nil,.photometricInconsistent) }
        guard best.2-alternative >= 0.04 else { return (nil,.photometricInconsistent) }
        return ((best.0,best.1),.valid)
    }

    /// Destination is the source-camera working grid; H samples the original target image.
    static func rotationWarp(image: CGImage, width: Int, height: Int, homography: simd_float3x3) throws -> (image: CGImage, valid: [Bool]) {
        let w = image.width, h = image.height
        var input = [UInt8](repeating:0,count:w*h*4)
        let rendered = input.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(data:buffer.baseAddress,width:w,height:h,bitsPerComponent:8,bytesPerRow:w*4,
                space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            context.draw(image,in:CGRect(x:0,y:0,width:w,height:h)); return true
        }
        guard rendered else { throw Failure.unreadableImage }
        var output = [UInt8](repeating:0,count:width*height*4), valid = [Bool](repeating:false,count:width*height)
        for y in 0..<height { for x in 0..<width {
            let index = y*width+x; output[index*4+3] = 255
            guard let q = SpatialObservationGeometry.mapPixel(SIMD2(Float(x),Float(y)),homography:homography),
                  q.x >= 0, q.y >= 0, q.x < Float(w-1), q.y < Float(h-1) else { continue }
            let ix = Int(q.x), iy = Int(q.y), dx = q.x-Float(ix), dy = q.y-Float(iy)
            for channel in 0..<3 {
                func value(_ px:Int,_ py:Int)->Float { Float(input[(py*w+px)*4+channel]) }
                let pixel = value(ix,iy)*(1-dx)*(1-dy)+value(ix+1,iy)*dx*(1-dy)+value(ix,iy+1)*(1-dx)*dy+value(ix+1,iy+1)*dx*dy
                output[index*4+channel] = UInt8(min(255,max(0,pixel.rounded())))
            }
            valid[index] = true
        }}
        guard let provider = CGDataProvider(data:Data(output) as CFData),
              let result = CGImage(width:width,height:height,bitsPerComponent:8,bitsPerPixel:32,bytesPerRow:width*4,
                space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGBitmapInfo(rawValue:CGImageAlphaInfo.premultipliedLast.rawValue),
                provider:provider,decode:nil,shouldInterpolate:false,intent:.defaultIntent) else { throw Failure.unreadableImage }
        return (result,valid)
    }

    private struct Gray {
        var width: Int
        var height: Int
        var values: [Float]
        var validMask: [Bool]?
        var invalidIntegral: [Int]?
        init(_ image: CGImage, validMask: [Bool]? = nil) throws {
            self.validMask = validMask
            invalidIntegral = nil
            width = image.width; height = image.height; values = []
            guard validMask == nil || validMask?.count == width*height else { throw Failure.invalidFlow }
            var bytes = [UInt8](repeating:0,count:width*height*4)
            let rendered = bytes.withUnsafeMutableBytes { buffer -> Bool in
                guard let ctx = CGContext(data:buffer.baseAddress,width:width,height:height,bitsPerComponent:8,bytesPerRow:width*4,
                    space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
                ctx.draw(image,in:CGRect(x:0,y:0,width:width,height:height)); return true
            }
            guard rendered else { throw Failure.unreadableImage }
            values = stride(from:0,to:bytes.count,by:4).map { (0.299*Float(bytes[$0])+0.587*Float(bytes[$0+1])+0.114*Float(bytes[$0+2]))/255 }
            if let validMask {
                let stride = width+1
                var integral = [Int](repeating:0,count:stride*(height+1))
                for y in 0..<height {
                    var rowInvalid = 0
                    for x in 0..<width {
                        rowInvalid += validMask[y*width+x] ? 0 : 1
                        integral[(y+1)*stride+x+1] = integral[y*stride+x+1]+rowInvalid
                    }
                }
                invalidIntegral = integral
            }
        }
        /// O(1) query of the complete continuous rectangle plus bilinear neighbours.
        func containsValidRectangle(minX: Float, minY: Float, maxX: Float, maxY: Float) -> Bool {
            guard minX.isFinite,minY.isFinite,maxX.isFinite,maxY.isFinite,
                  minX >= 0,minY >= 0,maxX >= minX,maxY >= minY,
                  maxX < Float(width-1),maxY < Float(height-1) else { return false }
            guard let integral = invalidIntegral else { return true }
            let x0 = Int(floor(minX)), y0 = Int(floor(minY))
            let x1 = Int(floor(maxX))+2, y1 = Int(floor(maxY))+2, stride = width+1
            return integral[y1*stride+x1]-integral[y0*stride+x1]-integral[y1*stride+x0]+integral[y0*stride+x0] == 0
        }
        func sample(_ x: Float, _ y: Float) -> Float? {
            guard x.isFinite,y.isFinite,x >= 0,y >= 0,x < Float(width-1),y < Float(height-1) else { return nil }
            let ix = Int(x), iy = Int(y), dx = x-Float(ix), dy = y-Float(iy), p = iy*width+ix
            if let validMask {
                guard validMask.count == values.count, validMask[p], validMask[p+1], validMask[p+width], validMask[p+width+1] else { return nil }
            }
            return values[p]*(1-dx)*(1-dy)+values[p+1]*dx*(1-dy)+values[p+width]*(1-dx)*dy+values[p+width+1]*dx*dy
        }
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

    private static func rawMatches(source: CGImage, target: CGImage, nativeWidth: Int, nativeHeight: Int, nativePixels:[[Float]]? = nil) throws -> [SpatialImageMatch] {
        guard source.width == target.width, source.height == target.height,
              nativeWidth > 0, nativeHeight > 0 else { throw Failure.incompatibleFrames }
        let a = try resized(source), b = try resized(target)
        let forward = try flow(source: a, target: b)
        let reverse = try flow(source: b, target: a)
        let ga = try Gray(a), gb = try Gray(b)
        let width = a.width, height = a.height
        let scaleX = Float(nativeWidth) / Float(width), scaleY = Float(nativeHeight) / Float(height)
        var result: [SpatialImageMatch] = []
        let samples:[SIMD2<Float>]
        if let nativePixels {
            samples=nativePixels.map {SIMD2(workingCoordinate(normalizedNative:$0[0],workingExtent:width,nativeExtent:nativeWidth),workingCoordinate(normalizedNative:$0[1],workingExtent:height,nativeExtent:nativeHeight))}
        } else {
            // Existing calibration and consumers retain the original 15 x 10 interior grid.
            var grid:[SIMD2<Float>]=[]
            for row in 0..<10 {
                for column in 0..<15 {
                    let x=Float(column+1)*Float(width-1)/16
                    let y=Float(row+1)*Float(height-1)/11
                    grid.append(SIMD2<Float>(x,y))
                }
            }
            samples=grid
        }
        for sample in samples {
                let x=sample.x,y=sample.y
                guard let guess = forward.sample(x,y), let f = refine(source:ga,target:gb,x:x,y:y,guess:guess).displacement,
                      let reverseGuess = reverse.sample(x+f.0,y+f.1),
                      let r = refine(source:gb,target:ga,x:x+f.0,y:y+f.1,guess:reverseGuess).displacement else { continue }
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
        return result
    }

    static func workingCoordinate(normalizedNative:Float,workingExtent:Int,nativeExtent:Int)->Float {
        (normalizedNative*Float(nativeExtent)+0.5)*Float(workingExtent)/Float(nativeExtent)-0.5
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
            guard matches.count > 15 else { throw Failure.insufficientCalibrationMatches(stage:"sparse-median",count:matches.count) }
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
        guard supports.count > 15 else { throw Failure.calibrationDepthMismatch(stage:"sparse-support",supports:supports.count,medianErrorMeters:depthErrors.first ?? -1) }
        let medianDepthError = depthErrors[depthErrors.count / 2]
        guard medianDepthError.isFinite, medianDepthError <= 0.15 else { throw Failure.calibrationDepthMismatch(stage:"sparse-depth",supports:supports.count,medianErrorMeters:medianDepthError) }
        // This static path never calls calibrate(), avoiding recursive device self-checks.
        let grid = try depthGrid(source:a,target:b,nativeWidth:width,nativeHeight:height)
        let gridMatches = grid.compactMap(\.match)
        let gridSupports = gridMatches.compactMap { SpatialObservationGeometry.triangulate($0,source:cameras.source,target:cameras.target) }
        let gridErrors = gridSupports.map { abs($0.position.z+3) }.sorted()
        guard grid.count == 3072, gridSupports.count > 15 else { throw Failure.gridCalibrationMismatch(candidates:grid.count,valid:gridMatches.count,supports:gridSupports.count,medianErrorMeters:gridErrors.first ?? -1) }
        let gridMedian = gridErrors[gridErrors.count/2]
        guard gridMedian.isFinite, gridMedian <= 0.15 else { throw Failure.gridCalibrationMismatch(candidates:grid.count,valid:gridMatches.count,supports:gridSupports.count,medianErrorMeters:gridMedian) }
        let reasons = Dictionary(grouping:grid,by:{ $0.state.rawValue }).mapValues(\.count)
        return Calibration(elapsedSeconds: ProcessInfo.processInfo.systemUptime-began,
            forwardMedianPixels: f, reverseMedianPixels: r,
            forwardMatchCount: forward.count, reverseMatchCount: reverse.count,
            triangulatedSupportCount: supports.count, medianDepthErrorMeters: medianDepthError,
            depthGridCandidateCount:grid.count,depthGridValidCount:gridMatches.count,
            depthGridReasonCounts:reasons,depthGridTriangulatedCount:gridSupports.count,
            depthGridMedianDepthErrorMeters:gridMedian)
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
            // Apple's SDK opticalFlowFromImage:toImage: example puts fromImage in the handler
            // and toImage in the targeted request. The prose above that example is ambiguous;
            // the real asymmetric translation calibration gates the actual runtime convention.
            let request = VNGenerateOpticalFlowRequest(targetedCGImage: target, options: [:])
            request.revision = 2
            request.computationAccuracy = .high
            request.keepNetworkOutput = false
            request.outputPixelFormat = kCVPixelFormatType_TwoComponent32Float
            try VNImageRequestHandler(cgImage: source, options: [:]).perform([request])
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
