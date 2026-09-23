import ARKit
import AVFoundation
import SwiftUI
import UIKit

// MARK: - Camera capture & preview
//
// Frame-capture delegate and the SwiftUI camera preview.
// Perception runs async on a copied pixel buffer so the live preview never stalls.

enum PixelBufferCopy {
    /// Deep-copy a camera/AR buffer so analysis can leave the capture callback.
    static func deepCopy(_ source: CVPixelBuffer) -> CVPixelBuffer? {
        let width = CVPixelBufferGetWidth(source)
        let height = CVPixelBufferGetHeight(source)
        let format = CVPixelBufferGetPixelFormatType(source)
        var destination: CVPixelBuffer?
        let attrs: [CFString: Any] = [
            kCVPixelBufferIOSurfacePropertiesKey: [:] as CFDictionary
        ]
        let status = CVPixelBufferCreate(
            kCFAllocatorDefault,
            width,
            height,
            format,
            attrs as CFDictionary,
            &destination
        )
        guard status == kCVReturnSuccess, let destination else {
            return nil
        }

        CVPixelBufferLockBaseAddress(source, .readOnly)
        CVPixelBufferLockBaseAddress(destination, [])
        defer {
            CVPixelBufferUnlockBaseAddress(source, .readOnly)
            CVPixelBufferUnlockBaseAddress(destination, [])
        }

        let planeCount = CVPixelBufferGetPlaneCount(source)
        if planeCount == 0 {
            guard
                let src = CVPixelBufferGetBaseAddress(source),
                let dst = CVPixelBufferGetBaseAddress(destination)
            else {
                return nil
            }
            let srcBytesPerRow = CVPixelBufferGetBytesPerRow(source)
            let dstBytesPerRow = CVPixelBufferGetBytesPerRow(destination)
            let rowBytes = min(srcBytesPerRow, dstBytesPerRow)
            for y in 0..<height {
                memcpy(dst.advanced(by: y * dstBytesPerRow), src.advanced(by: y * srcBytesPerRow), rowBytes)
            }
        } else {
            for plane in 0..<planeCount {
                guard
                    let src = CVPixelBufferGetBaseAddressOfPlane(source, plane),
                    let dst = CVPixelBufferGetBaseAddressOfPlane(destination, plane)
                else {
                    continue
                }
                let planeHeight = CVPixelBufferGetHeightOfPlane(source, plane)
                let srcBytesPerRow = CVPixelBufferGetBytesPerRowOfPlane(source, plane)
                let dstBytesPerRow = CVPixelBufferGetBytesPerRowOfPlane(destination, plane)
                let rowBytes = min(srcBytesPerRow, dstBytesPerRow)
                for y in 0..<planeHeight {
                    memcpy(
                        dst.advanced(by: y * dstBytesPerRow),
                        src.advanced(by: y * srcBytesPerRow),
                        rowBytes
                    )
                }
            }
        }
        return destination
    }
}

final class FrameCaptureProxy: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    /// Delivers optional JPEG (empty when remote upload is off), encode ms, and local vision.
    var onFrame: (@Sendable (Data, Double, LocalVisionSignal) -> Void)?
    /// When false (local-only product path), skip JPEG encode — overlays do not need it.
    var encodesJPEGForRemote: Bool = false

    private var lastPerceptionTime: CFTimeInterval = 0
    private var isPerceptionBusy = false
    private let stateLock = NSLock()
    private let perceptionQueue = DispatchQueue(label: "vqasee.local-perception", qos: .userInitiated)
    private let localVisionAnalyzer = LocalVisionAnalyzer()
    private var encodingProfile = FrameEncodingProfile(
        maxDimension: StreamingLimits.maxImageDimension,
        jpegQuality: StreamingLimits.jpegQuality,
        maxJPEGBytes: StreamingLimits.maxJPEGBytes
    )

    func setEncodingProfile(_ profile: FrameEncodingProfile) {
        encodingProfile = profile
    }

    /// Apply a perception config (e.g. after an OTA fetch) to the local analyzer.
    func applyPerceptionConfig(_ config: PerceptionConfig) {
        localVisionAnalyzer.apply(config: config)
    }

    func captureOutput(
        _ output: AVCaptureOutput,
        didOutput sampleBuffer: CMSampleBuffer,
        from connection: AVCaptureConnection
    ) {
        // Preview stays on the AVCapture path. Perception is throttled + async so a
        // slow TwinLite forward never freezes the live camera layer.
        let now = CACurrentMediaTime()
        stateLock.lock()
        let tooSoon = now - lastPerceptionTime < StreamingLimits.minLocalPerceptionInterval
        let busy = isPerceptionBusy
        if tooSoon || busy {
            stateLock.unlock()
            return
        }
        lastPerceptionTime = now
        isPerceptionBusy = true
        stateLock.unlock()

        guard
            let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer),
            let copied = PixelBufferCopy.deepCopy(pixelBuffer)
        else {
            stateLock.lock()
            isPerceptionBusy = false
            stateLock.unlock()
            return
        }

        let encodesJPEG = encodesJPEGForRemote
        let profile = encodingProfile
        perceptionQueue.async { [weak self] in
            defer {
                self?.stateLock.lock()
                self?.isPerceptionBusy = false
                self?.stateLock.unlock()
            }
            guard let self else {
                return
            }
            let localVisionSignal = self.localVisionAnalyzer.analyze(pixelBuffer: copied)
            var jpegData = Data()
            var encodeMs = 0.0
            if encodesJPEG {
                let encodeStart = CACurrentMediaTime()
                if let encoded = FrameJPEGEncoder.encode(
                    pixelBuffer: copied,
                    maxDimension: profile.maxDimension,
                    quality: profile.jpegQuality,
                    maxBytes: profile.maxJPEGBytes
                ) {
                    jpegData = encoded
                    encodeMs = (CACurrentMediaTime() - encodeStart) * 1000.0
                }
            }
            self.onFrame?(jpegData, encodeMs, localVisionSignal)
        }
    }

    /// Let the next captured frame skip the min-interval throttle so a
    /// user-initiated capture (single-shot / voice question) is answered promptly.
    func forceNextFrame() {
        stateLock.lock()
        lastPerceptionTime = 0
        stateLock.unlock()
    }

    /// Clears throttle state so a fresh stream always sends its first frame immediately.
    func resetGateState() {
        stateLock.lock()
        lastPerceptionTime = 0
        isPerceptionBusy = false
        stateLock.unlock()
        localVisionAnalyzer.reset()
    }
}


final class ARFrameCaptureProxy: NSObject, ARSessionDelegate {
    var onFrame: (@Sendable (Data, Double, LocalVisionSignal) -> Void)?
    var encodesJPEGForRemote: Bool = false

    private var lastPerceptionTime: CFTimeInterval = 0
    private var isPerceptionBusy = false
    private let stateLock = NSLock()
    private let perceptionQueue = DispatchQueue(label: "vqasee.ar-local-perception", qos: .userInitiated)
    private let localVisionAnalyzer = LocalVisionAnalyzer()
    private var encodingProfile = FrameEncodingProfile(
        maxDimension: StreamingLimits.maxImageDimension,
        jpegQuality: StreamingLimits.jpegQuality,
        maxJPEGBytes: StreamingLimits.maxJPEGBytes
    )

    static var isDepthCaptureSupported: Bool {
        ARWorldTrackingConfiguration.isSupported
            && (ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)
                || ARWorldTrackingConfiguration.supportsFrameSemantics(.smoothedSceneDepth))
    }

    static func makeConfiguration() -> ARWorldTrackingConfiguration? {
        guard ARWorldTrackingConfiguration.isSupported else {
            return nil
        }
        let configuration = ARWorldTrackingConfiguration()
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.smoothedSceneDepth) {
            configuration.frameSemantics.insert(.smoothedSceneDepth)
        } else if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
            configuration.frameSemantics.insert(.sceneDepth)
        }
        configuration.worldAlignment = .gravity
        return configuration
    }

    func setEncodingProfile(_ profile: FrameEncodingProfile) {
        encodingProfile = profile
    }

    /// Apply a perception config (e.g. after an OTA fetch) to the local analyzer.
    func applyPerceptionConfig(_ config: PerceptionConfig) {
        localVisionAnalyzer.apply(config: config)
    }

    func forceNextFrame() {
        stateLock.lock()
        lastPerceptionTime = 0
        stateLock.unlock()
    }

    func resetGateState() {
        stateLock.lock()
        lastPerceptionTime = 0
        isPerceptionBusy = false
        stateLock.unlock()
        localVisionAnalyzer.reset()
    }

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let now = CACurrentMediaTime()
        stateLock.lock()
        let tooSoon = now - lastPerceptionTime < StreamingLimits.minLocalPerceptionInterval
        let busy = isPerceptionBusy
        if tooSoon || busy {
            stateLock.unlock()
            return
        }
        lastPerceptionTime = now
        isPerceptionBusy = true
        stateLock.unlock()

        guard let copied = PixelBufferCopy.deepCopy(frame.capturedImage) else {
            stateLock.lock()
            isPerceptionBusy = false
            stateLock.unlock()
            return
        }
        let depthCues = ARDepthCueExtractor.extract(from: frame)
        let encodesJPEG = encodesJPEGForRemote
        let profile = encodingProfile
        perceptionQueue.async { [weak self] in
            defer {
                self?.stateLock.lock()
                self?.isPerceptionBusy = false
                self?.stateLock.unlock()
            }
            guard let self else {
                return
            }
            let localVisionSignal = self.localVisionAnalyzer.analyze(
                pixelBuffer: copied,
                depthCues: depthCues,
                depthCapability: .active
            )
            var jpegData = Data()
            var encodeMs = 0.0
            if encodesJPEG {
                let encodeStart = CACurrentMediaTime()
                if let encoded = FrameJPEGEncoder.encode(
                    pixelBuffer: copied,
                    maxDimension: profile.maxDimension,
                    quality: profile.jpegQuality,
                    maxBytes: profile.maxJPEGBytes
                ) {
                    jpegData = encoded
                    encodeMs = (CACurrentMediaTime() - encodeStart) * 1000.0
                }
            }
            self.onFrame?(jpegData, encodeMs, localVisionSignal)
        }
    }
}

enum ARDepthCueExtractor {
    static func extract(from frame: ARFrame) -> LocalDepthCueSignal {
        guard let depthData = frame.smoothedSceneDepth ?? frame.sceneDepth else {
            return LocalDepthCueSignal()
        }
        return extract(fromDepthMap: depthData.depthMap)
    }

    static func extract(fromDepthMap depthMap: CVPixelBuffer) -> LocalDepthCueSignal {
        CVPixelBufferLockBaseAddress(depthMap, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(depthMap, .readOnly) }

        guard CVPixelBufferGetPixelFormatType(depthMap) == kCVPixelFormatType_DepthFloat32,
              let base = CVPixelBufferGetBaseAddress(depthMap)
        else {
            return LocalDepthCueSignal()
        }

        let width = CVPixelBufferGetWidth(depthMap)
        let height = CVPixelBufferGetHeight(depthMap)
        let bytesPerRow = CVPixelBufferGetBytesPerRow(depthMap)
        let nearThreshold: Float = 1.15

        var leftNear = 0
        var centerNear = 0
        var rightNear = 0
        var valid = 0

        let yStart = Int(Double(height) * 0.48)
        let yEnd = Int(Double(height) * 0.92)
        let xStart = Int(Double(width) * 0.10)
        let xEnd = Int(Double(width) * 0.90)
        let stepY = max(1, (yEnd - yStart) / 8)
        let stepX = max(1, (xEnd - xStart) / 10)

        for y in stride(from: yStart, to: yEnd, by: stepY) {
            let row = base.advanced(by: y * bytesPerRow).assumingMemoryBound(to: Float32.self)
            for x in stride(from: xStart, to: xEnd, by: stepX) {
                let depth = row[x]
                guard depth.isFinite, depth > 0.08, depth < 6 else {
                    continue
                }
                valid += 1
                if depth < nearThreshold {
                    let ratio = Double(x) / Double(width)
                    if ratio < 0.33 {
                        leftNear += 1
                    } else if ratio > 0.67 {
                        rightNear += 1
                    } else {
                        centerNear += 1
                    }
                }
            }
        }

        guard valid >= 8 else {
            return LocalDepthCueSignal()
        }
        let maxNear = max(leftNear, centerNear, rightNear)
        guard maxNear >= 3 else {
            return LocalDepthCueSignal()
        }
        let direction: LocalVisionDirection
        if centerNear >= leftNear && centerNear >= rightNear {
            direction = .center
        } else if leftNear >= rightNear {
            direction = .left
        } else {
            direction = .right
        }
        return LocalDepthCueSignal(nearDrop: .possible, nearestObstacleDirection: direction)
    }
}

final class CameraPreviewUIView: UIView {
    override class var layerClass: AnyClass {
        AVCaptureVideoPreviewLayer.self
    }

    var previewLayer: AVCaptureVideoPreviewLayer {
        guard let layer = layer as? AVCaptureVideoPreviewLayer else {
            fatalError("Unexpected layer type")
        }
        return layer
    }
}


struct ARCameraPreview: UIViewRepresentable {
    let session: ARSession

    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView(frame: .zero)
        view.session = session
        view.automaticallyUpdatesLighting = false
        return view
    }

    func updateUIView(_ uiView: ARSCNView, context: Context) {
        uiView.session = session
    }
}

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> CameraPreviewUIView {
        let view = CameraPreviewUIView()
        view.previewLayer.videoGravity = .resizeAspectFill
        view.previewLayer.session = session
        return view
    }

    func updateUIView(_ uiView: CameraPreviewUIView, context: Context) {
        uiView.previewLayer.session = session
    }
}
