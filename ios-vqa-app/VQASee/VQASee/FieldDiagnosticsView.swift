import SwiftUI
import UIKit

/// Observe the store itself: publishing inside a nested ObservableObject does
/// not notify StreamingViewModel's observers.
struct FieldDiagnosticsView: View {
    @ObservedObject var viewModel: StreamingViewModel
    @ObservedObject var store: FieldDiagnosticStore
    @State private var includeImages = false
    @State private var includeLocation = false
    @State private var selecting = false
    @State private var selection: Set<UUID> = []
    @State private var pendingDeletion: Set<UUID> = []
    @State private var confirmDeletion = false

    var body: some View {
        List {
            Section("当前识别") {
                Text(viewModel.localDiagnosticSummary)
                    .font(.callout.monospacedDigit())
                    .textSelection(.enabled)
                Text("诊断仅提供运行证据；模型置信度不代表准确率。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section {
                Toggle("包含问题画面", isOn: $includeImages)
                    .disabled(store.isRecording)
                Toggle("包含位置与速度", isOn: $includeLocation)
                    .disabled(store.isRecording)
                if includeLocation {
                    Text(viewModel.locationText.isEmpty ? "暂无位置" : viewModel.locationText)
                        .font(.caption).foregroundStyle(.secondary)
                }
                if store.isRecording {
                    Button("标记刚才的问题", systemImage: "flag") { store.markIssue() }
                    Button("结束记录", role: .destructive) { store.stop() }
                } else {
                    Button("开始记录", systemImage: "record.circle") {
                        store.start(includeImages: includeImages, includeLocation: includeLocation)
                    }
                }
                Text(store.status).font(.callout)
            } header: {
                Text("现场记录")
            } footer: {
                Text("开始记录后，请返回首页开始观察。默认只记录识别数据；画面为问题前后的低频图片片段，并非完整视频。位置和画面仅在勾选后记录，导出可能包含敏感内容。请停车后或由乘员操作。")
            }
            Section {
                LabeledContent("本机占用", value: Self.size(store.usedBytes) + " / 300 MB")
                Text("单次最多 10 分钟。未保留的记录在 7 天后或空间不足时清理；保留记录仍占容量。停止后不再采集。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section {
                if store.records.isEmpty {
                    Text("还没有现场记录").foregroundStyle(.secondary)
                }
                ForEach(store.records) { record in
                    HStack {
                        if selecting {
                            Button {
                                if selection.contains(record.id) { selection.remove(record.id) }
                                else { selection.insert(record.id) }
                            } label: {
                                Image(systemName: selection.contains(record.id) ? "checkmark.circle.fill" : "circle")
                            }
                            .buttonStyle(.borderless)
                            .accessibilityLabel(selection.contains(record.id) ? "取消选择记录" : "选择记录")
                        }
                        NavigationLink {
                            FieldDiagnosticDetailView(store: store, initialRecord: record)
                        } label: {
                            VStack(alignment: .leading, spacing: 5) {
                                HStack {
                                    Text(record.startedAt.formatted(date: .abbreviated, time: .shortened))
                                    if record.isKept { Image(systemName: "pin.fill").font(.caption) }
                                }
                                Text(record.summary).font(.caption).lineLimit(2)
                                Text(recordFlags(record)).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                    .swipeActions {
                        Button("删除", role: .destructive) { requestDeletion([record.id]) }
                    }
                }
            } header: {
                HStack {
                    Text("已保存记录")
                    Spacer()
                    if !store.records.isEmpty {
                        Button(selecting ? "完成" : "选择") {
                            selecting.toggle()
                            if !selecting { selection.removeAll() }
                        }
                    }
                }
            }
            if !store.records.isEmpty {
                Section {
                    if selecting {
                        Button("删除所选（\(selection.count)）", role: .destructive) { requestDeletion(selection) }
                            .disabled(selection.isEmpty)
                    }
                    Button("删除全部记录", role: .destructive) {
                        requestDeletion(Set(store.records.map(\.id)))
                    }
                }
            }
        }
        .navigationTitle("现场诊断")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { store.refresh() }
        .confirmationDialog("删除 \(pendingDeletion.count) 条现场记录？", isPresented: $confirmDeletion, titleVisibility: .visible) {
            Button("删除记录及图片", role: .destructive) {
                store.delete(ids: pendingDeletion)
                selection.subtract(pendingDeletion)
                pendingDeletion.removeAll()
            }
            Button("取消", role: .cancel) { pendingDeletion.removeAll() }
        } message: {
            Text("所选记录的图片与识别数据将一并删除，无法撤销。已标记保留的记录也会删除；正在录制的当前记录需先结束。")
        }
    }

    private func requestDeletion(_ ids: Set<UUID>) {
        pendingDeletion = ids
        confirmDeletion = true
    }

    private func recordFlags(_ record: FieldDiagnosticRecord) -> String {
        [Self.size(record.sizeBytes), record.includesImages ? "已允许画面" : "不含画面",
         record.includesLocation ? "已允许位置" : "不含位置"].joined(separator: " · ")
    }

    fileprivate static func size(_ bytes: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }
}

private struct DiagnosticShareItem: Identifiable {
    let id = UUID()
    let url: URL
}

private struct FieldDiagnosticDetailView: View {
    @ObservedObject var store: FieldDiagnosticStore
    let initialRecord: FieldDiagnosticRecord
    @State private var exporting = false
    @State private var exportError: String?
    @State private var shareItem: DiagnosticShareItem?
    @State private var temporaryExport: URL?
    @State private var frames: [FieldDiagnosticFrame] = []
    @State private var loadingFrames = false
    @State private var hasMoreFrames = true
    @State private var readError: String?

    private var record: FieldDiagnosticRecord {
        store.records.first { $0.id == initialRecord.id } ?? initialRecord
    }

    var body: some View {
        List {
            Section("记录信息") {
                Text(record.startedAt.formatted(date: .abbreviated, time: .standard))
                Text(record.summary)
                LabeledContent("大小", value: FieldDiagnosticsView.size(record.sizeBytes))
                Toggle("保留这条记录", isOn: Binding(
                    get: { record.isKept },
                    set: { store.setKept(id: record.id, kept: $0) }
                ))
                Text("保留后不会被自动清理，仍计入 300 MB 容量。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section {
                if frames.isEmpty && !loadingFrames && readError == nil {
                    Text("还没有可回看的识别帧。请开始观察后再刷新。")
                        .foregroundStyle(.secondary)
                }
                ForEach(Array(frames.enumerated()), id: \.offset) { _, frame in
                    NavigationLink {
                        FieldDiagnosticFrameView(frame: frame)
                    } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(frame.recordedAt?.formatted(date: .omitted, time: .standard) ?? "识别帧")
                            Text(frame.id).font(.caption.monospaced()).lineLimit(1)
                            Text(frame.imageURL == nil ? "识别数据 · 未保存画面" : "识别数据与问题画面")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                if let readError {
                    Text(readError).font(.callout).foregroundStyle(.red)
                }
                if loadingFrames { ProgressView("读取记录…") }
                if hasMoreFrames && !loadingFrames {
                    Button(frames.isEmpty ? "读取识别帧" : "加载更多") {
                        Task { await loadFrames(reset: false) }
                    }
                }
                Button("刷新记录") { Task { await loadFrames(reset: true) } }
                    .disabled(loadingFrames)
            } header: {
                Text("逐帧回看")
            } footer: {
                Text("按记录时间排列。只有开启画面记录并标记问题，或自动捕获到问题时，才会保留附近图片。单帧数据用于定位问题，不代表准确率评测。")
            }
            Section {
                Button {
                    Task { await exportRecord() }
                } label: {
                    HStack {
                        Label("导出诊断包", systemImage: "square.and.arrow.up")
                        if exporting { Spacer(); ProgressView() }
                    }
                }
                .disabled(exporting || store.isRecording)
                if store.isRecording {
                    Text("请先结束记录再导出。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let exportError {
                    Text(exportError).foregroundStyle(.red).font(.callout)
                }
            } footer: {
                Text("导出通过系统分享完成，不会自动上传。分享或取消后清理临时导出包，原始记录保留。")
            }
        }
        .navigationTitle("记录详情")
        .navigationBarTitleDisplayMode(.inline)
        .task { await loadFrames(reset: true) }
        .sheet(item: $shareItem, onDismiss: cleanupExport) { item in
            DiagnosticActivitySheet(url: item.url) { error in
                if let error { exportError = "分享失败：\(error)" }
                cleanupExport()
                shareItem = nil
            }
        }
    }

    @MainActor private func loadFrames(reset: Bool) async {
        guard !loadingFrames else { return }
        loadingFrames = true
        readError = nil
        defer { loadingFrames = false }
        do {
            let page = try await store.readFrames(id: record.id, offset: reset ? 0 : frames.count, limit: 50)
            if reset { frames = page } else { frames.append(contentsOf: page) }
            hasMoreFrames = page.count == 50
        } catch {
            readError = "读取失败：\(error.localizedDescription)"
        }
    }

    @MainActor private func exportRecord() async {
        exporting = true
        exportError = nil
        defer { exporting = false }
        do {
            let url = try await store.export(id: record.id)
            temporaryExport = url
            shareItem = DiagnosticShareItem(url: url)
        } catch {
            exportError = "导出失败：\(error.localizedDescription)"
        }
    }

    private func cleanupExport() {
        guard let url = temporaryExport else { return }
        temporaryExport = nil
        store.cleanupExport(url: url)
    }
}

private struct FieldDiagnosticFrameView: View {
    let frame: FieldDiagnosticFrame
    @State private var image: UIImage?
    @State private var imageError: String?

    var body: some View {
        List {
            Section("帧信息") {
                Text(frame.id).font(.caption.monospaced()).textSelection(.enabled)
                if let capturedAt = frame.capturedAt {
                    LabeledContent("拍摄时间", value: capturedAt.formatted(date: .omitted, time: .standard))
                }
                if let recordedAt = frame.recordedAt {
                    LabeledContent("记录时间", value: recordedAt.formatted(date: .omitted, time: .standard))
                }
            }
            Section("同帧原图") {
                if let image {
                    Image(uiImage: image).resizable().scaledToFit()
                        .accessibilityLabel("这条识别结果对应的原始摄像头画面")
                } else if let imageError {
                    Text(imageError).foregroundStyle(.secondary)
                } else if frame.imageURL != nil {
                    ProgressView("读取图片…")
                } else {
                    Text("这帧没有保存图片。未开启画面记录或不在问题片段内时，只保存识别数据。")
                        .foregroundStyle(.secondary)
                }
            }
            Section("识别过程与发布结果") {
                Text(frame.metadata)
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
            }
        }
        .navigationTitle("识别帧")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            guard let url = frame.imageURL else { return }
            // File I/O stays off the main actor. Decode only the selected frame.
            let data = await Task.detached(priority: .utility) { try? Data(contentsOf: url) }.value
            if let data, let decoded = UIImage(data: data) { image = decoded }
            else { imageError = "图片已清理或无法读取。" }
        }
    }
}

private struct DiagnosticActivitySheet: UIViewControllerRepresentable {
    let url: URL
    let onCompletion: @MainActor (String?) -> Void

    func makeUIViewController(context: Context) -> UIActivityViewController {
        let controller = UIActivityViewController(activityItems: [url], applicationActivities: nil)
        controller.completionWithItemsHandler = { _, _, _, error in
            let message = error?.localizedDescription
            Task { @MainActor in onCompletion(message) }
        }
        return controller
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
