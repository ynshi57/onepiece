import SwiftUI

struct CameraRiskOverlay: View {
    let signal: LocalPerceptionSignal
    let mode: AssistanceMode
    let isActive: Bool

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                if isActive {
                    drivableAreaOverlay(in: proxy.size)
                    laneMarkingOverlay(in: proxy.size)
                    pathGuidanceOverlay(in: proxy.size)
                    roadCueOverlay(in: proxy.size)
                    riskRegionOverlay(in: proxy.size)
                    objectOverlay(in: proxy.size)
                    cueChips
                        .padding(.top, 94)
                        .padding(.horizontal, 16)
                }
            }
            .allowsHitTesting(false)
            .accessibilityHidden(true)
        }
    }

    private var cueChips: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(cueTexts, id: \.self) { text in
                Text(text)
                    .font(.caption.bold())
                    .foregroundStyle(.black)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(.yellow.opacity(0.86), in: Capsule())
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var cueTexts: [String] {
        // The coarse near/left/right status chips + "关注左前方" focus label were
        // removed: they echoed the retired three-box discrete signal. The guidance
        // corridor + road/obstacle cues carry the on-screen guidance now.
        var texts: [String] = []
        if signal.roadCues.crosswalk == .possible {
            texts.append("疑似人行横道")
        }
        if signal.roadCues.laneMarking == .possible {
            texts.append("疑似车道线")
        }
        if signal.roadCues.curb == .possible {
            texts.append("疑似路沿/边界")
        }
        if signal.depthCues.nearDrop == .possible {
            texts.append("近处疑似落差")
        }
        if signal.depthCues.nearestObstacleDirection != .unknown {
            texts.append("最近障碍：\(signal.depthCues.nearestObstacleDirection.chineseLabel)")
        }
        return texts
    }

    private var shouldDrawGuidanceCorridor: Bool {
        let guidance = signal.pathGuidance
        if guidance.blockedRegions.isEmpty
            && guidance.uncertainRegions.isEmpty
            && guidance.reasons.contains(.yoloOnly) {
            return false
        }
        return guidance.guidanceCorridor != nil
    }

    private func pathGuidanceOverlay(in size: CGSize) -> some View {
        Canvas { context, _ in
            if let line = signal.guidancePath?.primary, line.points.count >= 2 {
                var halo = Path()
                var stroke = Path()
                for (index, point) in line.points.enumerated() {
                    let view = CGPoint(x: size.width * point.x, y: size.height * (1 - point.y))
                    if index == 0 {
                        halo.move(to: view)
                        stroke.move(to: view)
                    } else {
                        halo.addLine(to: view)
                        stroke.addLine(to: view)
                    }
                }
                context.stroke(halo, with: .color(.white.opacity(0.85)), style: StrokeStyle(lineWidth: 8, lineCap: .round, lineJoin: .round))
                context.stroke(stroke, with: .color(Color(red: 10/255, green: 132/255, blue: 1)), style: StrokeStyle(lineWidth: 5, lineCap: .round, lineJoin: .round))
            } else {
                let guidance = signal.pathGuidance
                if shouldDrawGuidanceCorridor, let corridor = guidance.guidanceCorridor {
                    let polygon = guidanceCorridorPath(from: corridor, in: size)
                    let color = guidance.blockedRegions.isEmpty ? Color.blue : Color.orange
                    context.fill(polygon, with: .color(color.opacity(guidance.blockedRegions.isEmpty ? 0.08 : 0.20)))
                    context.stroke(
                        polygon,
                        with: .color(color.opacity(0.86)),
                        style: StrokeStyle(lineWidth: 3, lineCap: .round, lineJoin: .round, dash: [12, 8])
                    )
                } else {
                    drawSubtleNearPathReference(in: &context, size: size)
                }
            }

            for rect in signal.pathGuidance.uncertainRegions {
                let viewRect = overlayRect(forNormalizedRect: rect, in: size)
                let path = Path(roundedRect: viewRect, cornerRadius: 16)
                context.fill(path, with: .color(.gray.opacity(0.20)))
                context.stroke(path, with: .color(.gray.opacity(0.70)), style: StrokeStyle(lineWidth: 2, dash: [6, 6]))
            }
        }
    }

    private func drivableAreaOverlay(in size: CGSize) -> some View {
        Canvas { context, _ in
            guard let grid = signal.traversableGrid,
                  grid.cols > 0, grid.rows > 0,
                  grid.cells.count == grid.cols * grid.rows else { return }
            let cellW = size.width / CGFloat(grid.cols)
            let cellH = size.height / CGFloat(grid.rows)
            let color = Color(red: 180/255, green: 40/255, blue: 40/255).opacity(0.32)
            for r in 0..<grid.rows {
                let rowBase = r * grid.cols
                for c in 0..<grid.cols where grid.cells[rowBase + c] != 0 {
                    let rect = CGRect(
                        x: CGFloat(c) * cellW, y: CGFloat(r) * cellH,
                        width: cellW + 0.5, height: cellH + 0.5
                    )
                    context.fill(Path(rect), with: .color(color))
                }
            }
        }
    }

    /// Draw lane polylines as strokes. Pixel `laneGrid` is not a product overlay.
    private func laneMarkingOverlay(in size: CGSize) -> some View {
        Canvas { context, _ in
            let lanes = signal.lanePolylines
            guard !lanes.isEmpty else { return }
            let color = Color(red: 255/255, green: 214/255, blue: 10/255)
            for lane in lanes where lane.points.count >= 2 {
                var halo = Path()
                var stroke = Path()
                for (index, point) in lane.points.enumerated() {
                    let view = CGPoint(x: size.width * point.x, y: size.height * point.y)
                    if index == 0 {
                        halo.move(to: view)
                        stroke.move(to: view)
                    } else {
                        halo.addLine(to: view)
                        stroke.addLine(to: view)
                    }
                }
                context.stroke(halo, with: .color(.black.opacity(0.45)), style: StrokeStyle(lineWidth: 5, lineCap: .round, lineJoin: .round))
                context.stroke(stroke, with: .color(color), style: StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
            }
        }
    }

    private func riskRegionOverlay(in size: CGSize) -> some View {
        Canvas { context, _ in
            for rect in signal.pathGuidance.blockedRegions.prefix(8) {
                let viewRect = overlayRect(forNormalizedRect: rect, in: size).insetBy(dx: -8, dy: -8)
                let path = Path(roundedRect: viewRect, cornerRadius: 16)
                let objectColor = Theme.riskDanger
                context.fill(path, with: .color(objectColor.opacity(0.16)))
                context.stroke(
                    path,
                    with: .color(objectColor.opacity(0.62)),
                    style: StrokeStyle(lineWidth: 2, lineCap: .round, dash: [10, 6])
                )
            }
        }
    }

    private func guidanceCorridorPath(from rect: CGRect, in size: CGSize) -> Path {
        let bottomY = size.height * (1 - rect.minY)
        let topY = size.height * (1 - min(rect.maxY, 0.62))
        var path = Path()
        path.move(to: CGPoint(x: size.width * 0.30, y: bottomY))
        path.addLine(to: CGPoint(x: size.width * 0.42, y: topY))
        path.addQuadCurve(
            to: CGPoint(x: size.width * 0.58, y: topY),
            control: CGPoint(x: size.width * 0.50, y: topY - size.height * 0.04)
        )
        path.addLine(to: CGPoint(x: size.width * 0.70, y: bottomY))
        path.closeSubpath()
        return path
    }

    private func drawSubtleNearPathReference(in context: inout GraphicsContext, size: CGSize) {
        var centerLine = Path()
        centerLine.move(to: CGPoint(x: size.width * 0.5, y: size.height * 0.88))
        centerLine.addLine(to: CGPoint(x: size.width * 0.5, y: size.height * 0.58))
        context.stroke(
            centerLine,
            with: .color(.cyan.opacity(0.28)),
            style: StrokeStyle(lineWidth: 2, lineCap: .round, dash: [6, 10])
        )
    }

    private func roadCueOverlay(in size: CGSize) -> some View {
        Canvas { context, _ in
            // Curb/boundary heuristic only. Lane markings are no longer drawn as the
            // old hardcoded diagonal placeholder here — the dedicated lane segmenter's
            // real grid renders in laneMarkingOverlay(in:) instead.
            if signal.roadCues.curb == .possible {
                var leftBoundary = Path()
                leftBoundary.move(to: CGPoint(x: size.width * 0.12, y: size.height * 0.88))
                leftBoundary.addLine(to: CGPoint(x: size.width * 0.32, y: size.height * 0.38))

                var rightBoundary = Path()
                rightBoundary.move(to: CGPoint(x: size.width * 0.88, y: size.height * 0.88))
                rightBoundary.addLine(to: CGPoint(x: size.width * 0.68, y: size.height * 0.38))

                let color = Color.yellow.opacity(0.75)
                context.stroke(leftBoundary, with: .color(color), style: StrokeStyle(lineWidth: 3, lineCap: .round, dash: [14, 8]))
                context.stroke(rightBoundary, with: .color(color), style: StrokeStyle(lineWidth: 3, lineCap: .round, dash: [14, 8]))
            }

            if signal.roadCues.crosswalk == .possible {
                for index in 0..<5 {
                    let y = size.height * (0.62 + CGFloat(index) * 0.045)
                    var stripe = Path()
                    stripe.move(to: CGPoint(x: size.width * 0.28, y: y))
                    stripe.addLine(to: CGPoint(x: size.width * 0.72, y: y))
                    context.stroke(stripe, with: .color(.white.opacity(0.72)), style: StrokeStyle(lineWidth: 5, lineCap: .round))
                }
            }
        }
    }

    private func objectOverlay(in size: CGSize) -> some View {
        ForEach(Array(signal.objects.prefix(8).enumerated()), id: \.offset) { _, object in
            let rect = overlayRect(for: object, in: size)
            let color = color(for: object.kind)
            ZStack(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(color, lineWidth: object.kind.isPriorityRisk ? 4 : 2)
                    .background {
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .fill(color.opacity(0.10))
                    }

                Text(label(for: object))
                    .font(.caption.bold())
                    .foregroundStyle(.black)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(color, in: Capsule())
                    .offset(x: 6, y: -12)
            }
            .frame(width: rect.width, height: rect.height)
            .position(x: rect.midX, y: rect.midY)
        }
    }

    private func overlayRect(forNormalizedRect box: CGRect, in size: CGSize) -> CGRect {
        let width = max(2, box.width * size.width)
        let height = max(2, box.height * size.height)
        let x = box.minX * size.width
        let y = (1 - box.maxY) * size.height
        return CGRect(x: x, y: y, width: width, height: height)
    }

    private func overlayRect(for object: LocalPerceptionObject, in size: CGSize) -> CGRect {
        if let box = object.normalizedBoundingBox {
            // Vision normalized rect origin is lower-left; SwiftUI origin is upper-left.
            let width = max(44, box.width * size.width)
            let height = max(44, box.height * size.height)
            let x = box.minX * size.width
            let y = (1 - box.maxY) * size.height
            return CGRect(x: x, y: y, width: width, height: height)
        }

        let width = size.width * 0.26
        let height = size.height * 0.18
        let centerX: CGFloat
        switch object.direction {
        case .left:
            centerX = size.width * 0.25
        case .center, .unknown:
            centerX = size.width * 0.5
        case .right:
            centerX = size.width * 0.75
        }
        let centerY = size.height * 0.48
        return CGRect(x: centerX - width / 2, y: centerY - height / 2, width: width, height: height)
    }

    private func color(for kind: LocalPerceptionObjectKind) -> Color {
        switch kind {
        case .car, .truck, .bus, .motorcycle, .bicycle:
            return .orange
        case .person, .dog:
            return .yellow
        case .obstacle, .stairs, .pothole, .curb:
            return .red
        case .crosswalk, .laneMarking:
            return .white
        case .trafficLight, .sign:
            return .cyan
        case .unknown:
            return .gray
        }
    }

    private func label(for object: LocalPerceptionObject) -> String {
        let percent = Int((object.confidence * 100).rounded())
        return "\(object.kind.chineseLabel) \(percent)%"
    }
}
