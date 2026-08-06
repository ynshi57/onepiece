import SwiftUI

// MARK: - AnswerPanel
//
// The bottom glass card that shows the latest result: summary, spatial direction,
// risk, suggested action, plus a latency line with an "updating" spinner. This is
// the ONLY place allowed to scroll — and only internally, capped at a fraction of
// the screen — so a very long answer never pushes the fixed controls off-screen or
// forces the whole page to scroll.
//
// For VoiceOver the whole card is a single element whose label is composed
// risk-first (safety before everything), then summary, direction, and advice.

struct AnswerPanel: View {
    let summary: String
    let spatial: String
    let riskLevel: String
    let risk: String
    let action: String
    let latency: String
    let isProcessing: Bool

    /// Cap the internal scroll region so large Dynamic Type sizes stay on one screen.
    let maxContentHeight: CGFloat

    private var combinedAccessibilityLabel: Text {
        // Risk first (safety), then summary, direction, advice.
        Text("\(risk)。\(summary)。方向：\(spatial)。建议：\(action)")
    }

    var body: some View {
        GlassPanel {
            VStack(alignment: .leading, spacing: Theme.Spacing.md) {
                ScrollView {
                    VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
                        Text(summary)
                            .font(Theme.Typography.answer)
                            .frame(maxWidth: .infinity, alignment: .leading)

                        Text("方向：\(spatial)")
                            .font(Theme.Typography.detail)
                            .frame(maxWidth: .infinity, alignment: .leading)

                        Text(risk)
                            .font(Theme.Typography.risk)
                            .foregroundStyle(Theme.riskColor(for: riskLevel))
                            .frame(maxWidth: .infinity, alignment: .leading)

                        Text(action)
                            .font(Theme.Typography.detail)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .frame(maxHeight: maxContentHeight)

                Divider()

                HStack(spacing: Theme.Spacing.sm) {
                    Image(systemName: "timer")
                        .foregroundStyle(.secondary)
                    Text("延迟：\(latency)")
                        .font(Theme.Typography.caption)
                        .fontWeight(.medium)
                    if isProcessing {
                        ProgressView()
                            .controlSize(.mini)
                        Text("更新中…")
                            .font(Theme.Typography.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(combinedAccessibilityLabel)
        .accessibilityAddTraits(.updatesFrequently)
    }
}
