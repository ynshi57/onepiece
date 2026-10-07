import SwiftUI

/// Menu contents are static. AR publishes at 20 Hz, but an already presented
/// UIKit menu must keep its action instances until the user selects one.
struct IndoorMemoryActionsMenu: View, Equatable {
    enum Action { case debug, capture, rescan, saveMap, clear, diagnostics, outdoor }
    let owner: ObjectIdentifier
    let perform: (Action) -> Void

    // The owner is a stable controller. Handlers use its reference and SwiftUI
    // State bindings, not snapshots of tracking values. If dynamic menu content
    // is introduced, include that content in this comparison.
    static func == (lhs: Self, rhs: Self) -> Bool { lhs.owner == rhs.owner }

    var body: some View {
        Menu {
            Button("真机调试", systemImage: "desktopcomputer") { perform(.debug) }
            Button("采集测试样本", systemImage: "camera.badge.clock") { perform(.capture) }
            Button("重新扫描", systemImage: "arrow.clockwise") { perform(.rescan) }
            Button("重试保存记录与空间地图", systemImage: "square.and.arrow.down") { perform(.saveMap) }
            Button("清除本次记录", systemImage: "trash", role: .destructive) { perform(.clear) }
            Menu("反馈", systemImage: "bubble.left") {
                Button("本次定位诊断", systemImage: "waveform.path.ecg") { perform(.diagnostics) }
            }
            Button("户外看路", systemImage: "figure.walk") { perform(.outdoor) }
        } label: {
            Image(systemName: "ellipsis")
                .font(.title3.bold()).frame(width: 44, height: 44)
                .background(.regularMaterial, in: Circle())
        }
        .accessibilityLabel("物品记忆选项")
    }
}
