import SwiftUI

struct IndoorMemoryScreen: View {
    @ObservedObject var controller: IndoorMemoryController
    @EnvironmentObject private var deviceDebug: DeviceDebugController
    @Environment(\.scenePhase) private var scenePhase
    @State private var name = ""
    @State private var showingPhoto = false
    @State private var showingDiagnostics = false
    @State private var confirmingClear = false
    @State private var showingComparison = false
    @State private var showingCapture = false
    @State private var topHeight: CGFloat = 100
    @State private var bottomHeight: CGFloat = 320
    let onOutdoor: () -> Void
    var onDeviceDebug: () -> Void = {}

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                Color.black.ignoresSafeArea()
                IndoorMemoryCamera(controller: controller)
                    .accessibilityHidden(true)
                if let arrow = controller.guidanceArrow {
                    VStack(spacing: 8) {
                        Image(systemName: arrow).font(.system(size: 42, weight: .semibold))
                        Text("转回记录位置").font(.headline)
                    }
                    .foregroundStyle(.white).padding(18)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
                    .position(x: geometry.size.width/2, y: geometry.size.height/2-60)
                    .allowsHitTesting(false)
                }
                // A camera-space target, not a claim about the object's present location.
                if let marker = controller.marker, let record = controller.selected {
                    Circle().fill(.mint).frame(width: 18, height: 18)
                        .overlay { Circle().stroke(.white, lineWidth: 2) }
                        .overlay(alignment: .bottom) {
                            VStack(spacing: 3) {
                                if let mask = record.target?.maskPNG, let image = UIImage(data: mask) {
                                    Image(uiImage: image).resizable().scaledToFit().frame(width: 56, height: 56)
                                    Text("记录轮廓 · 原视角").font(.caption2)
                                }
                                Text(record.name).font(.headline).lineLimit(2)
                                    .frame(maxWidth: .infinity)
                                    .accessibilityIdentifier("indoor-marker-name")
                                Text("记录位置 · \(record.recordedAt.formatted(date: .omitted, time: .shortened))")
                                    .font(.caption)
                                Text(String(format: "约 %.1f 米", marker.distance)).font(.caption)
                            }
                            .frame(width: min(220, max(120, geometry.size.width - 80)))
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(10)
                            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
                            .padding(.bottom, 28)
                        }
                        .position(marker.point)
                    .allowsHitTesting(false)
                    .accessibilityElement(children: .contain)
                    .accessibilityIdentifier("indoor-record-marker")
                }
                Image(systemName: "plus")
                    .font(.system(size: 28, weight: .light))
                    .foregroundStyle(controller.canRecord ? .mint : .white)
                    .shadow(color: .black, radius: 3)
                    .position(x: geometry.size.width / 2, y: geometry.size.height / 2)
                    .accessibilityHidden(true)
                VStack {
                    VStack {
                        topBar
                        IndoorCaptureStatusBar(recorder: controller.capture)
                    }
                    .onGeometryChange(for: CGFloat.self) { $0.frame(in: .named("memory-camera")).maxY } action: { topHeight = $0 }
                    Spacer(minLength: 16)
                    bottomPanel
                        .onGeometryChange(for: CGFloat.self) { $0.frame(in: .named("memory-camera")).minY } action: { bottomHeight = $0 }
                }
                .padding(.horizontal, 20)
                .safeAreaPadding(.top, 12)
                .safeAreaPadding(.bottom, 12)
            }
            .coordinateSpace(name: "memory-camera")
            .onChange(of: topHeight) { _, _ in updateVisibleRect(size: geometry.size) }
            .onChange(of: bottomHeight) { _, _ in updateVisibleRect(size: geometry.size) }
            .onChange(of: geometry.size) { _, _ in updateVisibleRect(size: geometry.size) }
            .onAppear { updateVisibleRect(size: geometry.size) }
        }
        .sheet(isPresented: $showingComparison) { comparisonSheet }
        .sheet(isPresented: $showingCapture) { IndoorCaptureScreen(controller: controller, recorder: controller.capture) }
        .preferredColorScheme(.dark)
        .task { await controller.start() }
        // Sheets (including debugging) must not stop AR. Scene backgrounding and
        // explicit mode transitions below own camera shutdown.
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await controller.start() } }
            else { controller.stop() }
        }
        .sheet(item: Binding(get: { controller.draft }, set: { if $0 == nil { controller.cancelDraft() } })) { _ in
            namingSheet
        }
        .sheet(isPresented: $showingDiagnostics) {
            NavigationStack {
                TimelineView(.periodic(from: .now, by: 1)) { _ in
                    Text(controller.diagnosticSummary).frame(maxWidth: .infinity, alignment: .leading).padding(24)
                }
                .navigationTitle("本次定位诊断")
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { showingDiagnostics = false } } }
            }
        }
        .sheet(isPresented: $showingPhoto) {
            if let record = controller.selected { photoSheet(record) }
        }
        .alert("未能记录", isPresented: Binding(get: { controller.error != nil }, set: { if !$0 { controller.error = nil } })) {
            Button("知道了", role: .cancel) { controller.error = nil }
        } message: { Text(controller.error ?? "请重试") }
        .confirmationDialog("清除本次物品记录与照片？", isPresented: $confirmingClear, titleVisibility: .visible) {
            Button("清除并重新扫描", role: .destructive) {
                controller.clear()
                Task { await controller.start() }
            }
        }
    }

    private func updateVisibleRect(size: CGSize) {
        controller.visibleRect = CGRect(x: 0, y: topHeight + 75, width: size.width,
                                        height: max(0, bottomHeight - topHeight - 85))
    }

    private var topBar: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 5) {
                Text("物品记忆").font(.title2.bold())
                if controller.isLayoutPreview {
                    Text("UI 布局示例 · 非真实定位").font(.caption).foregroundStyle(.yellow)
                }
                Label(controller.status, systemImage: "viewfinder")
                    .font(.caption).foregroundStyle(.secondary)
                    .accessibilityIdentifier("indoor-status")
            }
            Spacer()
            IndoorMemoryActionsMenu(owner: ObjectIdentifier(controller), perform: performMenuAction)
                .equatable()
        }
        .padding(14)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 22))
    }

    private func performMenuAction(_ action: IndoorMemoryActionsMenu.Action) {
        deviceDebug.recordEvent("menu_action", details: ["action": String(describing: action)])
        switch action {
        case .debug: onDeviceDebug()
        case .capture:
            deviceDebug.recordEvent("open_capture", details: ["availability": controller.captureAvailability.message])
            showingCapture = true
        case .rescan:
            controller.stop()
            Task { await controller.start() }
        case .saveMap: controller.retrySave()
        case .clear: confirmingClear = true
        case .diagnostics: showingDiagnostics = true
        case .outdoor:
            controller.stop()
            onOutdoor()
        }
    }

    private var bottomPanel: some View {
        VStack(alignment: .leading, spacing: 14) {
            if controller.records.isEmpty {
                Text("让手机记住一个位置").font(.headline)
                Text("对准物品记录照片，再点选物品。无需先找到桌面。")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(controller.records) { record in
                            Button {
                                controller.select(record)
                            } label: {
                                Text(record.name).font(.subheadline.weight(.semibold))
                                    .padding(.horizontal, 16).padding(.vertical, 12)
                                    .background(controller.selectedID == record.id ? Color.mint : Color.white.opacity(0.12), in: Capsule())
                                    .foregroundStyle(controller.selectedID == record.id ? .black : .white)
                            }
                        }
                    }
                }
                if let record = controller.selected {
                    HStack {
                        Text("记录于 \(record.recordedAt.formatted(date: .omitted, time: .shortened))")
                        Spacer()
                        Button("查看当时照片") { showingPhoto = true }
                    }.font(.caption)
                }
            }
            Text(controller.guidance)
                .font(.subheadline).fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("indoor-guidance")
            if controller.selected != nil {
                Text(controller.liveObservation.message)
                    .font(.subheadline).fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("indoor-live-observation")
                Text(controller.liveAppearanceMessage).font(.caption).foregroundStyle(.secondary)
                Text("连续观察 · 实验性深度线索，不代表物品身份确认")
                    .font(.caption2).foregroundStyle(.secondary)
                Button("查看轮廓变化", systemImage: "rectangle.on.rectangle") {
                    deviceDebug.recordEvent("compare_now_pressed")
                    showingComparison = true
                    Task { await controller.checkSelectedObject() }
                }
                .disabled(!controller.canCheckSelectedObject)
                Text(controller.detail).font(.caption).foregroundStyle(.secondary)
            }
            if controller.selected != nil && !controller.canRecord {
                Text(controller.recordHint).font(.caption).foregroundStyle(.secondary)
            }
            Button {
                deviceDebug.recordEvent("remember_pressed", details: ["can_record": String(controller.canRecord)])
                name = ""
                controller.beginRecord()
            } label: {
                Label("记住位置", systemImage: "mappin.and.ellipse")
                    .frame(maxWidth: .infinity).padding(.vertical, 6)
                    .foregroundStyle(controller.canRecord ? Color.black : Color.gray)
            }
            .buttonStyle(.borderedProminent).tint(.mint)
            .controlSize(.large).disabled(!controller.canRecord)
            .accessibilityIdentifier("indoor-record")
            if case .paused = controller.state {
                Button("重新扫描") {
                    controller.stop()
                    Task { await controller.start() }
                }.frame(maxWidth: .infinity)
            }
            if controller.state == .failed || controller.state == .denied {
                Button(controller.state == .denied ? "打开系统设置" : "重新扫描") {
                    if controller.state == .denied, let url = URL(string: UIApplication.openSettingsURLString) {
                        UIApplication.shared.open(url)
                    } else { Task { await controller.start() } }
                }.frame(maxWidth: .infinity)
            }
            Text(controller.persistenceMessage + "\n照片、轮廓和空间地图仅存本机，不上传")
                .font(.caption2).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity).multilineTextAlignment(.center)
        }
        .padding(20)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 26))
    }

    private var namingSheet: some View {
        NavigationStack {
            Form {
                Section("名字（可选）") {
                    TextField(controller.draft?.suggestedName ?? "物品", text: $name)
                        .accessibilityIdentifier("indoor-name")
                    Text("不填也能保存。记录照片和轮廓仅保存在本机，可随时删除。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let draft = controller.draft, let image = UIImage(data: draft.photo) {
                    GeometryReader { geometry in
                        ZStack {
                            Image(uiImage: image).resizable().scaledToFit()
                            if let mask = draft.target?.maskPNG, let maskImage = UIImage(data: mask) {
                                Color.mint.opacity(0.4).mask {
                                    Image(uiImage: maskImage).resizable().scaledToFit().luminanceToAlpha()
                                }
                            }
                        }
                        .contentShape(Rectangle())
                        .onTapGesture { point in
                            Task { await controller.selectDraftTarget(normalizedPoint: CGPoint(
                                x: point.x / geometry.size.width, y: point.y / geometry.size.height)) }
                        }
                        .accessibilityLabel("记录照片，点选要记住的物品")
                    }
                    .aspectRatio(image.size.width / image.size.height, contentMode: .fit)
                    Text(draft.targetSelectionStatus).font(.caption)
                    if controller.isSelectingTarget { ProgressView("提取物品轮廓") }
                }
            }
            .navigationTitle("记住位置").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { controller.cancelDraft() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        deviceDebug.recordEvent("remember_save_pressed", details: ["named": String(!name.isEmpty)])
                        controller.save(name: name)
                    }
                        .disabled(controller.isSelectingTarget || name.count > 30)
                }
            }
        }.presentationDetents([.large])
    }

    private func photoSheet(_ record: IndoorMemoryRecord) -> some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if let image = UIImage(data: record.photo) {
                        ZStack {
                            Image(uiImage: image).resizable().scaledToFit()
                            if let mask = record.target?.maskPNG, let maskImage = UIImage(data: mask) {
                                Color.mint.opacity(0.35).mask {
                                    Image(uiImage: maskImage).resizable().scaledToFit().luminanceToAlpha()
                                }
                            }
                        }.aspectRatio(image.size.width / image.size.height, contentMode: .fit)
                    }
                    Text(record.recordedAt.formatted(date: .abbreviated, time: .standard)).font(.headline)
                    if let observation = record.lastObservation {
                        Text("\(observation.observedAt.formatted(date: .abbreviated, time: .shortened)) · 你确认：\(observation.present ? "仍在原处" : "原处未见")")
                            .font(.headline)
                        if let image = UIImage(data: observation.photo) { Image(uiImage: image).resizable().scaledToFit() }
                        Text("这是历史人工确认，不代表现在的状态。").font(.caption)
                    }
                    Text("这是记录时的照片与轮廓，不能确认物品现在是否还在。重开后只有成功找回空间位置，才会显示位置标记。")
                        .foregroundStyle(.secondary)
                    Button("删除这条记录", role: .destructive) {
                        controller.delete(record)
                        showingPhoto = false
                    }
                }.padding(20)
            }
            .navigationTitle(record.name).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { showingPhoto = false } } }
        }
    }

    private var comparisonSheet: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("轮廓变化").font(.title2.bold())
                    Text("橙色虚线：记录轮廓　青色实线：本次轮廓").font(.caption)
                    Text("对齐成功时，两次轮廓叠在本次照片上。也可点本次照片中的物品，重新提取轮廓。")
                    if let record = controller.selected, let image = UIImage(data: controller.comparisonVisualization?.referenceAnnotatedPNG ?? record.photo) {
                        Text("记录时 · \(record.recordedAt.formatted(date: .abbreviated, time: .shortened))").font(.caption)
                        Image(uiImage: image).resizable().scaledToFit()
                    }
                    if let photo = controller.comparisonPhoto, let image = UIImage(data: photo) {
                        Text("本次观察").font(.caption)
                        Image(uiImage: UIImage(data: controller.comparisonVisualization?.currentAnnotatedPNG ?? photo) ?? image)
                            .resizable().aspectRatio(contentMode: .fit)
                            .overlay {
                                GeometryReader { geometry in
                                    Color.clear.contentShape(Rectangle())
                                        .gesture(SpatialTapGesture().onEnded { value in
                                            let point = CGPoint(x: value.location.x / geometry.size.width,
                                                                y: value.location.y / geometry.size.height)
                                            Task { await controller.selectComparisonTarget(point) }
                                        })
                                }
                            }
                            .accessibilityIdentifier("indoor-comparison-contours")
                    }
                    if controller.checkStatus == .checking { ProgressView("正在对齐并绘制轮廓…") }
                    Text(controller.detail).font(.headline)
                    if let time = controller.checkedAt { Text(time.formatted(date: .abbreviated, time: .standard)).font(.caption) }
                    if controller.checkStatus != .checking && controller.comparisonPhoto != nil {
                        Text("由你确认，仅针对这次照片").font(.caption).foregroundStyle(.secondary)
                        Button("我确认仍在原处") { controller.confirmSelectedObject(present: true) }
                        Button("我确认原处未见") { controller.confirmSelectedObject(present: false) }
                    }
                    Text("轮廓差异展示形状与位置线索；遮挡、视角变化或其他物品也可能产生差异。")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(20)
            }
            .navigationTitle("查看变化").navigationBarTitleDisplayMode(.inline)
            .onDisappear { controller.cancelComparison() }
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { showingComparison = false } } }
        }
    }
}
