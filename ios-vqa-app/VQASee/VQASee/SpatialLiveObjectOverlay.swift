import SwiftUI
import ARKit

nonisolated enum SpatialLiveObjectPresentation {
    static func isFresh(capturedAt: Double, now: Double) -> Bool {
        capturedAt.isFinite && now.isFinite && now >= capturedAt && now - capturedAt <= 0.35
    }

    static func viewportBox(_ box: [Float], transform: [Float]?, recordedViewport: [Float]?, viewport: CGSize) -> CGRect? {
        guard box.count == 4, box.allSatisfy(\.isFinite), box[0] < box[2], box[1] < box[3],
              let t = transform, t.count == 6, t.allSatisfy(\.isFinite),
              abs(t[0]*t[3]-t[1]*t[2]) > 0.000001,
              let recorded = recordedViewport, recorded.count == 2, recorded.allSatisfy(\.isFinite),
              viewport.width.isFinite, viewport.height.isFinite, viewport.width > 0, viewport.height > 0,
              abs(CGFloat(recorded[0])-viewport.width) <= 0.5, abs(CGFloat(recorded[1])-viewport.height) <= 0.5 else { return nil }
        let affine = CGAffineTransform(a: CGFloat(t[0]), b: CGFloat(t[1]), c: CGFloat(t[2]), d: CGFloat(t[3]), tx: CGFloat(t[4]), ty: CGFloat(t[5]))
        let points = [CGPoint(x: CGFloat(box[0]), y: CGFloat(box[1])), CGPoint(x: CGFloat(box[2]), y: CGFloat(box[1])), CGPoint(x: CGFloat(box[0]), y: CGFloat(box[3])), CGPoint(x: CGFloat(box[2]), y: CGFloat(box[3]))].map { $0.applying(affine) }
        let left = max(0, points.map(\.x).min()!), right = min(1, points.map(\.x).max()!)
        let top = max(0, points.map(\.y).min()!), bottom = min(1, points.map(\.y).max()!)
        guard left.isFinite, right.isFinite, top.isFinite, bottom.isFinite, left < right, top < bottom else { return nil }
        return CGRect(x: left*viewport.width, y: top*viewport.height, width: (right-left)*viewport.width, height: (bottom-top)*viewport.height)
    }
}

struct SpatialLiveObjectOverlay: View {
    @ObservedObject var controller: SpatialScanController
    var body: some View {
        GeometryReader { geometry in
            TimelineView(.periodic(from: .now, by: 0.1)) { _ in
                let result = controller.liveObjectResult
                let fresh = result.map { SpatialLiveObjectPresentation.isFresh(capturedAt: $0.capturedAt, now: ProcessInfo.processInfo.systemUptime) } ?? false
                ZStack(alignment: .topLeading) {
                    if fresh, let result {
                        ForEach(Array(result.detections.enumerated()), id: \.offset) { _, detection in
                            if let box = SpatialLiveObjectPresentation.viewportBox(detection.boundingBox, transform: result.nativeToViewport, recordedViewport: result.viewportSize, viewport: geometry.size) {
                                Rectangle().stroke(.orange, lineWidth: 2)
                                    .frame(width: box.width, height: box.height).position(x: box.midX, y: box.midY)
                                Text(SpatialObjectLabels.title(detection.label)).font(.caption.bold())
                                    .padding(3).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 4))
                                    .position(x: box.midX, y: max(12, box.minY + 10))
                            }
                        }
                    }
                    VStack {
                        Spacer()
                        Text(status(result: result, fresh: fresh, viewport: geometry.size)).font(.caption)
                            .padding(6).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }.padding(.horizontal, 8).padding(.bottom, 52)
                }
            }
        }.allowsHitTesting(false)
    }

    private func status(result: SpatialLiveObjectResult?, fresh: Bool, viewport: CGSize) -> String {
        guard let result else { return controller.liveObjectStatus }
        if !fresh { return result.failure == nil ? "识别未跟上画面" : "识别暂不可用，请继续观察" }
        if result.failure != nil || !result.detectionFailures.isEmpty { return result.detections.isEmpty ? "识别暂不可用，请继续观察" : "部分识别未完成；候选仍可能出错" }
        if SpatialLiveObjectPresentation.viewportBox([0,0,1,1], transform: result.nativeToViewport, recordedViewport: result.viewportSize, viewport: viewport) == nil { return "正在对齐物品画面" }
        return result.detections.isEmpty ? "暂未识别到物品，不代表没有" : "物品候选，类别与位置仍需核对"
    }
}

/// Layout reports the actual camera viewport, including picture-in-picture changes.
final class SpatialLiveObjectCameraView: ARSCNView {
    var viewportChanged: ((CGSize, UIInterfaceOrientation) -> Void)?
    private var reportedSize: CGSize?
    private var reportedOrientation: UIInterfaceOrientation?
    override func layoutSubviews() { super.layoutSubviews(); reportViewport() }
    override func didMoveToWindow() { super.didMoveToWindow(); reportViewport() }
    private func reportViewport() {
        let orientation = window?.windowScene?.interfaceOrientation ?? .unknown
        let size = bounds.size
        guard reportedSize != size || reportedOrientation != orientation else { return }
        reportedSize = size; reportedOrientation = orientation
        // Avoid publishing controller changes within a SwiftUI view update.
        Task { @MainActor [weak self] in
            guard let self, self.bounds.size == size,
                  (self.window?.windowScene?.interfaceOrientation ?? .unknown) == orientation else { return }
            self.viewportChanged?(size, orientation)
        }
    }
}
