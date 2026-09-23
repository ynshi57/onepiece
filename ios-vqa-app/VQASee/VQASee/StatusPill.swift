import SwiftUI

// MARK: - StatusPill
//
// Small floating capsule at the top of the immersive screen showing connection
// state: a colored dot + the localized StreamStatus title. VoiceOver reads it as
// a single frequently-updating element.

struct StatusPill: View {
    let status: StreamStatus
    let connectionText: String

    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    private var dotColor: Color {
        switch status {
        case .idle:
            return .secondary
        case .preparing:
            return Theme.riskWarning
        case .streaming:
            return .green
        case .error:
            return Theme.riskDanger
        }
    }

    /// Prefer a short connection hint when it adds product meaning (e.g. 本地看路中)
    /// over the generic StreamStatus title alone.
    private var displayTitle: String {
        let trimmed = connectionText.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty, trimmed.count <= 16 {
            return trimmed
        }
        return status.title
    }

    var body: some View {
        HStack(spacing: Theme.Spacing.sm) {
            Circle()
                .fill(dotColor)
                .frame(width: 10, height: 10)
            Text(displayTitle)
                .font(Theme.Typography.pill)
                .lineLimit(1)
        }
        .padding(.horizontal, Theme.Spacing.md)
        .padding(.vertical, Theme.Spacing.sm)
        .background {
            let shape = Capsule(style: .continuous)
            if reduceTransparency {
                shape.fill(Theme.solidSurface)
            } else {
                shape.fill(.ultraThinMaterial)
            }
        }
        .clipShape(Capsule(style: .continuous))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text("状态：\(status.title)。连接：\(connectionText)"))
        .accessibilityAddTraits(.updatesFrequently)
    }
}
