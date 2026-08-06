import SwiftUI

// MARK: - ModeBar
//
// Horizontal assistance-mode selector. Iterates AssistanceMode.allCases and binds
// the current selection. Each chip is a large, high-contrast tap target so it's
// usable one-handed and with motor impairments.

struct ModeBar: View {
    let selectedMode: AssistanceMode
    let onSelect: (AssistanceMode) -> Void

    var body: some View {
        HStack(spacing: Theme.Spacing.sm) {
            ForEach(AssistanceMode.allCases) { mode in
                let isSelected = mode == selectedMode
                Button {
                    onSelect(mode)
                } label: {
                    Text(mode.title)
                        .font(Theme.Typography.control)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, Theme.Spacing.md)
                        .background {
                            let shape = Capsule(style: .continuous)
                            if isSelected {
                                shape.fill(Theme.accent)
                            } else {
                                shape.fill(.regularMaterial)
                            }
                        }
                        .foregroundStyle(isSelected ? Color.white : Color.primary)
                        .clipShape(Capsule(style: .continuous))
                        .contentShape(Capsule(style: .continuous))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(Text("\(mode.title)模式"))
                .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
            }
        }
    }
}
