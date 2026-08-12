import ARKit
import AVFoundation
import SwiftUI
import UIKit

// MARK: - Camera capture & preview
//
// Frame-capture delegate and the SwiftUI camera preview, extracted verbatim from
// ContentView.swift.

final class FrameCaptureProxy: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    /// Delivers the encoded JPEG, on-device encode time, and local fast-vision signal.
    var onFrame: (@Sendable (Data, Double, LocalVisionSignal) -> Void)?
    private var lastFrameTime: CFTimeInterval = 0
    private let minInterval: CFTimeInterval = StreamingLimits.minFrameInterval
    private let localVisionAnalyzer = LocalVisionAnalyzer()
    private var encodingProfile = FrameEncodingProfile(
        maxDimension: StreamingLimits.maxImageDimension,
        jpegQuality: StreamingLimits.jpegQuality,
        maxJPEGBytes: StreamingLimits.maxJPEGBytes
    )

    func setEncodingProfile(_ profile: FrameEncodingProfile) {
        encodingProfile = profile
    }

    func captureOutput(
        _ output: AVCaptureOutput,
        didOutput sampleBuffer: CMSampleBuffer,
        from connection: AVCaptureConnection
    ) {
        // `minInterval` is the only throttle: it caps how often we send frames so
        // we don't flood the model, but every frame past the interval IS sent.
        // We deliberately do NOT try to guess "the scene didn't change" and drop
        // frames here — for a vision-assistance app that hides real changes from
        // the user (e.g. panning the camera to new content), which is unsafe.
        // Whether to *speak* the result is decided downstream by SpeechGate so we
        // still avoid repeating ourselves without ever hiding the current frame.
        let now = CACurrentMediaTime()
        guard now - lastFrameTime >= minInterval else {
            return
        }
        lastFrameTime = now

        let localVisionSignal = localVisionAnalyzer.analyze(sampleBuffer: sampleBuffer)
        let encodeStart = CACurrentMediaTime()
        guard let jpegData = encode(sampleBuffer: sampleBuffer) else {
            return
        }
        let encodeMs = (CACurrentMediaTime() - encodeStart) * 1000.0
        onFrame?(jpegData, encodeMs, localVisionSignal)
    }

    /// Let the next captured frame skip the min-interval throttle so a
    /// user-initiated capture (single-shot / voice question) is answered promptly.
    func forceNextFrame() {
        lastFrameTime = 0
    }

    /// Clears throttle state so a fresh stream always sends its first frame immediately.
    func resetGateState() {
        lastFrameTime = 0
        localVisionAnalyzer.reset()
    }

    private func encode(sampleBuffer: CMSampleBuffer) -> Data? {
        FrameJPEGEncoder.encode(
            sampleBuffer: sampleBuffer,
            maxDimension: encodingProfile.maxDimension,
            quality: encodingProfile.jpegQuality,
            maxBytes: encodingProfile.maxJPEGBytes
        )
    }
}


final class ARFrameCaptureProxy: NSObject, ARSessionDelegate {
    var onFrame: (@Sendable (Data, Double, LocalVisionSignal) -> Void)?
    private var lastFrameTime: CFTimeInterval = 0
    private let minInterval: CFTimeInterval = StreamingLimits.minFrameInterval
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

    func forceNextFrame() {
        lastFrameTime = 0
    }

    func resetGateState() {
        lastFrameTime = 0
        localVisionAnalyzer.reset()
    }

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let now = CACurrentMediaTime()
        guard now - lastFrameTime >= minInterval else {
            return
        }
        lastFrameTime = now

        let depthCues = ARDepthCueExtractor.extract(from: frame)
        let localVisionSignal = localVisionAnalyzer.analyze(
            pixelBuffer: frame.capturedImage,
            depthCues: depthCues,
            depthCapability: .active
        )
        let encodeStart = CACurrentMediaTime()
        guard let jpegData = FrameJPEGEncoder.encode(
            pixelBuffer: frame.capturedImage,
            maxDimension: encodingProfile.maxDimension,
            quality: encodingProfile.jpegQuality,
            maxBytes: encodingProfile.maxJPEGBytes
        ) else {
            return
        }
        let encodeMs = (CACurrentMediaTime() - encodeStart) * 1000.0
        onFrame?(jpegData, encodeMs, localVisionSignal)
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
