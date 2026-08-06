import SwiftUI

// MARK: - ServerPickerView
//
// The multi-backend selection list, shown inside the Settings sheet (not on the
// immersive main screen). Lets the user pick among several discovered Mac backends;
// the currently selected one is check-marked. Behavior mirrors the old inline list.

struct ServerPickerView: View {
    let servers: [DiscoveredServer]
    let selectedURLString: String
    let onSelect: (DiscoveredServer) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.sm) {
            Text("发现多个 Mac 后端，请选择一个：")
                .font(Theme.Typography.control)

            ForEach(servers) { server in
                Button {
                    onSelect(server)
                } label: {
                    HStack {
                        Image(systemName: "server.rack")
                        VStack(alignment: .leading, spacing: 2) {
                            Text(server.name)
                                .fontWeight(.medium)
                            Text(server.host)
                                .font(Theme.Typography.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        if selectedURLString == server.url.absoluteString {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(.green)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.bordered)
                .accessibilityLabel(Text("Mac 后端 \(server.name)，地址 \(server.host)"))
                .accessibilityAddTraits(
                    selectedURLString == server.url.absoluteString ? [.isButton, .isSelected] : .isButton
                )
            }
        }
    }
}
