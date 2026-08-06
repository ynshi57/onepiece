import SwiftUI

// MARK: - Design system
//
// A single namespace for spacing, corner radii, typography and semantic colors so
// the immersive UI stays visually coherent. Typography is built on Dynamic Type
// text styles (never fixed point sizes) so everything scales for low-vision users;
// colors are read from the asset catalog so they adapt to dark mode and the
// increase-contrast setting automatically.

enum Theme {
    /// 4 / 8 / 12 / 16 / 24 spacing scale.
    enum Spacing {
        static let xs: CGFloat = 4
        static let sm: CGFloat = 8
        static let md: CGFloat = 12
        static let lg: CGFloat = 16
        static let xl: CGFloat = 24
    }

    /// Corner radii: 20 for glass panels, 999 (fully rounded) for pills/capsules.
    enum Radius {
        static let panel: CGFloat = 20
        static let pill: CGFloat = 999
    }

    /// Dynamic-Type-relative fonts. Built from text styles so they respond to the
    /// user's preferred content size — deliberately no `.system(size:)`.
    enum Typography {
        /// The primary answer/summary line — the most important text on screen.
        static let answer = Font.system(.title2, design: .rounded).weight(.semibold)
        /// Secondary answer lines (spatial direction, suggested action).
        static let detail = Font.system(.body, design: .rounded)
        /// Risk line — emphasized but scales with Dynamic Type.
        static let risk = Font.system(.headline, design: .rounded)
        /// Status pill / small floating labels.
        static let pill = Font.system(.subheadline, design: .rounded).weight(.medium)
        /// Mode chips and control captions.
        static let control = Font.system(.callout, design: .rounded).weight(.medium)
        /// Ancillary hints and footnotes.
        static let caption = Font.system(.footnote, design: .rounded)
    }

    // MARK: Semantic colors (asset-backed, adapt to dark mode + high contrast)

    /// Brand accent, mirrors the asset-catalog AccentColor.
    static let accent = Color.accentColor
    /// Medium-risk / caution text ("注意").
    static let riskWarning = Color("RiskWarning")
    /// High-risk / danger text ("高风险").
    static let riskDanger = Color("RiskDanger")
    /// Safe / low-risk text — the standard system green reads well in both schemes.
    static let riskSafe = Color.green

    /// Opaque surface used when `accessibilityReduceTransparency` is on and the
    /// Liquid Glass effect must be replaced by a solid panel.
    static let solidSurface = Color(.secondarySystemBackground)

    /// Map a backend risk level ("low"/"medium"/"high") to its semantic color.
    /// Falls back to the safe color for unknown values.
    static func riskColor(for level: String) -> Color {
        switch level.lowercased() {
        case "high":
            return riskDanger
        case "medium":
            return riskWarning
        default:
            return riskSafe
        }
    }
}
