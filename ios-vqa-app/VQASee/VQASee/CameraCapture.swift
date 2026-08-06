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
