import SwiftUI

// MARK: - GlassPanel
//
// The one place that decides "Liquid Glass vs. solid surface". On iOS 26 it uses
// the system glass material; when the user has turned on Reduce Transparency it
// falls back to an opaque Theme surface. Centralizing the branch here keeps every
// floating overlay consistent and accessible.

struct GlassPanel<Content: View>: View {
    var cornerRadius: CGFloat = Theme.Radius.panel
    @ViewBuilder var content: Content

    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var body: some View {
        content
            .padding(Theme.Spacing.lg)
            .background {
                let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                if reduceTransparency {
                    // Opaque surface: no see-through, meets contrast needs.
                    shape.fill(Theme.solidSurface)
                } else {
                    // Liquid Glass (iOS 26). Regular material is the graceful,
                    // widely-available translucent backing.
                    shape.fill(.regularMaterial)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
    }
}
