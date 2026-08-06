import SwiftUI

struct CameraRiskOverlay: View {
    let signal: LocalPerceptionSignal
    let mode: AssistanceMode
    let isActive: Bool

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                if isActive {
                    movementGuide(in: proxy.size)
                    roadCueOverlay(in: proxy.size)
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

    private func movementGuide(in size: CGSize) -> some View {
        Canvas { context, _ in
            let bottomY = size.height * 0.92
            let midY = size.height * 0.52
            let topY = size.height * 0.30
            let centerX = size.width * 0.5
            let bottomHalf = size.width * 0.22
            let midHalf = size.width * 0.13
            let topHalf = size.width * 0.07

            var left = Path()
            left.move(to: CGPoint(x: centerX - bottomHalf, y: bottomY))
            left.addLine(to: CGPoint(x: centerX - midHalf, y: midY))
            left.addLine(to: CGPoint(x: centerX - topHalf, y: topY))

            var right = Path()
            right.move(to: CGPoint(x: centerX + bottomHalf, y: bottomY))
            right.addLine(to: CGPoint(x: centerX + midHalf, y: midY))
            right.addLine(to: CGPoint(x: centerX + topHalf, y: topY))

            let style = StrokeStyle(lineWidth: 3, lineCap: .round, lineJoin: .round, dash: [10, 8])
            context.stroke(left, with: .color(.cyan.opacity(0.72)), style: style)
            context.stroke(right, with: .color(.cyan.opacity(0.72)), style: style)

            var center = Path()
            center.move(to: CGPoint(x: centerX, y: bottomY))
            center.addLine(to: CGPoint(x: centerX, y: topY))
            context.stroke(center, with: .color(.white.opacity(0.22)), style: StrokeStyle(lineWidth: 1.5, dash: [4, 10]))
        }
    }

    private func roadCueOverlay(in size: CGSize) -> some View {
        Canvas { context, _ in
            if signal.roadCues.curb == .possible || signal.roadCues.laneMarking == .possible {
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
