import SwiftUI
import ARKit
import SceneKit
import Combine

/// Top view draws structure wall segments and floor rings. Raw polygons stay saved.
struct SpatialScanScreen: View {
    @ObservedObject var controller: SpatialScanController
    let onOutdoor: () -> Void
    let onLegacy: () -> Void
    let onDebug: () -> Void
    @Environment(\.scenePhase) private var scenePhase
    @State private var showingHistory = false
    @State private var topViewIsPrimary = false
    @State private var mapZoom: CGFloat = 1
    @State private var mapOffset: CGSize = .zero
    @GestureState private var mapDrag: CGSize = .zero
    @GestureState private var mapMagnification: CGFloat = 1
    @State private var confirmingClear = false
    @State private var confirmingNew = false
    @State private var pendingHistory: SpatialScanDocument?
    @State private var editingSurface: String?
    @State private var surfaceLayer = SpatialSurfaceLayer.all
    @StateObject private var textureEvidence = SpatialTextureEvidence()
    @StateObject private var structurePreview = SpatialStructurePreviewCache()
    @State private var sceneSummary = "结构待核对 · 空白为未知"
    private var planeSummary: String { sceneSummary }

    private var captureCoverageMessage: String {
        let events=controller.document.events
        if events.contains("keyframe_limit_reached_remaining_scan_has_no_rgb") || events.contains("keyframe_storage_limit") {
            guard let last=controller.document.keyframes.last else { return "这段扫描没有照片支持。" }
            let start=controller.document.poses.first(where:{$0.epoch == last.pose.epoch})?.time
                ?? controller.document.keyframes.first(where:{$0.pose.epoch == last.pose.epoch})?.time ?? last.time
            return String(format:"照片记录到本段第 %.0f 秒，后续轨迹没有照片支持。",max(0,last.time-start))
        }
        return "这段扫描有照片缺口，物品与空间记录可能不完整。"
    }

    private var observedSpan: String {
        let objects = SpatialObjectLocationBuilder.candidates(document: controller.semanticDisplayDocument).count
        let objectNote = objects == 0 ? "尚无可见面候选" : "可见面候选\(objects)，进深未知"
        guard structurePreview.input == SpatialStructureInput(document:controller.semanticDisplayDocument),let map=structurePreview.result?.map else {
            return "结构待核对 · \(objectNote) · 尚无独立尺寸"
        }
        return "结构 R\(map.revision) · 墙段\(map.wallSegments.count) · 地面候选\(map.floorRegions.count) · \(objectNote) · 尺寸尚未独立核对"
    }

    @ViewBuilder private var experimentOrCombinedViewport: some View {
        #if DEBUG
        if let preview=controller.structureP0Preview {
            VStack(alignment:.leading,spacing:10) {
                Text("局部结构实验 · 非完整房间地图").font(.headline)
                Text(preview.message).font(.caption).foregroundStyle(.secondary)
                if let url=preview.mapURL,let image=UIImage(contentsOfFile:url.path) {Image(uiImage:image).resizable().scaledToFit().accessibilityIdentifier("spatial.structureP0.map")}
            }
        } else {combinedViewport}
        #else
        combinedViewport
        #endif
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 10) {
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("空间扫描").font(.title2.bold())
                        Text(controller.status).font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button {
                        controller.setCoachMuted(!controller.coachMuted)
                    } label: {
                        Image(systemName: controller.coachMuted ? "speaker.slash.fill" : "speaker.wave.2.fill")
                            .font(.title3)
                    }
                    .accessibilityLabel(controller.coachMuted ? "打开扫描语音" : "关闭扫描语音")
                    .accessibilityValue(controller.coachMuted ? "已静音" : "语音开启")
                    .accessibilityIdentifier("spatial.coach.mute")
                    Menu {
                        Button("切换户外", action: onOutdoor)
                        Button("物品记录", action: onLegacy)
                        Button("真机调试", action: onDebug)
                    } label: { Image(systemName: "ellipsis.circle").font(.title2) }
                    .accessibilityLabel("更多功能")
                    .accessibilityIdentifier("spatial.more")
                }
                experimentOrCombinedViewport
                    .onChange(of: controller.document.id) { _, _ in
                        mapZoom = 1; mapOffset = .zero
                    }


                Button {
                    if controller.isScanning { controller.stopAndSave() } else if !controller.document.surfaces.isEmpty || !controller.document.poses.isEmpty { confirmingNew = true } else { controller.start() }
                } label: {
                    Label(controller.isScanning ? "结束并保存" : "开始扫描", systemImage: controller.isScanning ? "stop.fill" : "viewfinder")
                        .frame(maxWidth: .infinity).padding(.vertical, 8)
                }.buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("spatial.scan")
                    .disabled((!controller.isScanning && (controller.isArchivingObjects || controller.isAnalyzingIndoorSemantics)) || controller.isAnalyzingObjects || controller.isLocatingObjects || controller.isSaving || controller.isFinishingAndSaving || controller.isCapturingMap)
                    #if targetEnvironment(simulator)
                    .disabled(true)
                    #endif
                    .confirmationDialog("开始新的扫描？当前结果将被替换，请先保存需要保留的扫描。", isPresented: $confirmingNew, titleVisibility: .visible) {
                        Button("开始新的扫描", role: .destructive) { controller.start(discardUnsaved: true) }
                    }

                if !controller.isScanning, !controller.document.poses.isEmpty {
                    Button("恢复这个空间", systemImage: "arrow.triangle.2.circlepath") { controller.restore() }
                        .buttonStyle(.bordered).disabled(controller.isAnalyzingIndoorSemantics || controller.isArchivingObjects || controller.isAnalyzingObjects || controller.isLocatingObjects || controller.document.worldMap == nil || controller.isSaving || controller.isFinishingAndSaving || controller.isCapturingMap)
                        .accessibilityIdentifier("spatial.restore")
                    Text(controller.document.worldMap == nil ? "此记录没有定位地图，不能与当前相机对齐" : "回到原墙角后恢复；新观测核对通过才记录轨迹")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if controller.isCapturingMap || controller.isFinishingAndSaving { ProgressView("正在结束并保存，请稍候") }
                Text("空白处未扫描，不代表可通行；颜色不代表完整墙面。")
                    .font(.caption).foregroundStyle(.secondary)

                if !SpatialHorizontalObservation.surfaces(controller.document.surfaces).isEmpty {
                    Text("紫色为已观测墙段，橙色为候选墙段；青色虚线为候选地面轮廓，红色为未闭合，灰线仅作参考。")
                        .font(.caption).foregroundStyle(.secondary)
                }

                if controller.isScanning,
                   (controller.document.indoorSemanticObservations ?? []).contains(where: { $0.status == .processed }),
                   (controller.semanticDisplayDocument.indoorSemanticObservations ?? []).isEmpty {
                    Text("正在核对最新边界，灰色部分尚未确认类别")
                        .font(.caption).foregroundStyle(.secondary)
                        .accessibilityIdentifier("spatial.semantics.reprojecting")
                }
                if controller.document.events.contains("keyframe_limit_reached_remaining_scan_has_no_rgb") || controller.document.events.contains("keyframe_storage_limit") || controller.document.events.contains("keyframe_encode_failed") || controller.document.events.contains("keyframe_write_failed") {
                    Label(captureCoverageMessage, systemImage: "exclamationmark.triangle")
                        .font(.caption).foregroundStyle(.orange)
                        .accessibilityIdentifier("spatial.capture.incomplete")
                }

                SpatialIndoorSemanticView(controller: controller)
                SpatialObjectObservationView(controller: controller)

                if SpatialScanGeometry.trajectorySegments(controller.document.poses).count > 1 {
                    Label("轨迹出现中断或位置跳变，已分段显示，请检查空间结构。", systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
                }
                if let error = controller.error {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.callout).foregroundStyle(.orange)
                        .accessibilityIdentifier("spatial.error")
                }
                #if targetEnvironment(simulator)
                Label("模拟器无法扫描真实空间，请在 iPhone 上使用。", systemImage: "iphone")
                    .font(.callout).foregroundStyle(.secondary)
                #endif
                HStack {
                    Button("保存", systemImage: "square.and.arrow.down") { controller.save() }
                        .disabled(controller.isAnalyzingIndoorSemantics || controller.isArchivingObjects || controller.isAnalyzingObjects || controller.isLocatingObjects || controller.isSaving || controller.isFinishingAndSaving || controller.isCapturingMap || controller.isScanning || (controller.document.surfaces.isEmpty && controller.document.poses.isEmpty))
                        .accessibilityIdentifier("spatial.save")
                    Spacer()
                    Button("历史", systemImage: "clock") { showingHistory = true }
                        .accessibilityIdentifier("spatial.history")
                        .disabled(controller.isAnalyzingIndoorSemantics || controller.isArchivingObjects || controller.isAnalyzingObjects || controller.isLocatingObjects || controller.isSaving || controller.isFinishingAndSaving || controller.isCapturingMap)
                    Spacer()
                    Button("清除本次", role: .destructive) { confirmingClear = true }
                        .accessibilityIdentifier("spatial.clear")
                        .disabled(controller.isAnalyzingIndoorSemantics || controller.isArchivingObjects || controller.isAnalyzingObjects || controller.isLocatingObjects || controller.isSaving || controller.isFinishingAndSaving || controller.isCapturingMap)
                        .confirmationDialog("清除本次扫描？请先保存需要保留的结果。已保存的记录会保留。", isPresented: $confirmingClear, titleVisibility: .visible) {
                            Button("清除本次", role: .destructive) { controller.clear() }
                        }
                }.buttonStyle(.bordered)
                if !controller.isScanning && !controller.document.surfaces.isEmpty {
                    Menu("检查与修正结构") {
                        ForEach(controller.document.surfaces, id: \.id) { surface in
                            Button("\(SpatialSurfaceLayer.name(surface)) · 观测跨度 \(String(format: "%.2f", SpatialScanGeometry.observedBoundarySpan(surface) ?? 0)) 米") { editingSurface = surface.id }
                        }
                    }.accessibilityIdentifier("spatial.edit")
                }
                DisclosureGroup("扫描详情") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(textureEvidence.summary).font(.caption).foregroundStyle(.secondary)
                        DisclosureGroup("照片贴图证据（不表示完整墙面）") {
                            ForEach(textureEvidence.rows) { row in
                                VStack(alignment:.leading,spacing:3) {
                                    Text(row.id.prefix(8)+" · "+row.status).font(.caption)
                                    Text("候选照片 \(row.candidates.count) 张；选中 \(row.imageFile ?? "无")").font(.caption2)
                                    Text(row.candidates.map { $0.imageFile+":"+$0.state }.joined(separator:"\n")).font(.caption2)
                                }
                            }
                            Text("材质预算 \(textureEvidence.cacheStats["decodedBytes"] ?? 0) / \(textureEvidence.cacheStats["budgetBytes"] ?? 0) 字节；显示纹理最长边1024，原照片保留。遮挡、人物与真实墙面类别尚未确认。") .font(.caption2)
                        }
                        Picker("显示观测层", selection: $surfaceLayer) {
                            ForEach(SpatialSurfaceLayer.allCases, id: \.self) { layer in Text(layer.title).tag(layer) }
                        }.pickerStyle(.segmented).accessibilityIdentifier("spatial.layers")
                        Text("\(surfaceLayer.title) · 显示 \(controller.document.surfaces.filter { $0.observed && surfaceLayer.includes($0) }.count) / \(controller.document.surfaces.count) 个原始观测，保存与导出保留全部")
                            .font(.caption).foregroundStyle(.secondary)
                        HStack(spacing: 12) {
                            Label("垂直面候选", systemImage: "rectangle.portrait").foregroundStyle(.purple)
                            Label("水平观测，未确认地面", systemImage: "rectangle").foregroundStyle(.teal)
                        }.font(.caption)
                        Text("方向不等于类别：家具也可能形成平面，尚未确认墙面或地面。")
                            .font(.caption).foregroundStyle(.secondary)
                        Text(planeSummary).font(.caption).foregroundStyle(.secondary)
                            .accessibilityIdentifier("spatial.scene.summary")
                        Text(observedSpan).font(.caption).foregroundStyle(.secondary)
                        Text("类别颜色来自多照片核对；不贴照片补造地面。灰色和冲突面保留原始几何。")
                            .font(.caption).foregroundStyle(.secondary)
                        Text("已采集 \(controller.document.keyframes.count) 张关键帧").font(.caption).foregroundStyle(.secondary)
                        Text("只显示已观察结构。空白处未扫描，不代表可通行。")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                Text("已保存在本机；分享时可选 AirDrop 或存储到文件。")
                    .font(.caption).foregroundStyle(.secondary)
                if let url = controller.exportURL {
                    ShareLink(item: url) { Label("分享扫描包", systemImage: "square.and.arrow.up") }
                        .accessibilityIdentifier("spatial.export")
                }
            }.padding(16)
        }
        .background(Color(.systemBackground))
        .sheet(isPresented: $showingHistory) {
            NavigationStack {
                List {
                    if controller.saved.isEmpty { Text("还没有保存的扫描").foregroundStyle(.secondary) }
                    ForEach(controller.saved, id: \.id) { document in
                        Button {
                            if controller.document.id != document.id && controller.hasUnsavedChanges {
                                pendingHistory = document
                            } else {
                                controller.load(document)
                                showingHistory = false
                            }
                        } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(document.createdAt.formatted(date: .abbreviated, time: .shortened))
                                Text("\(document.surfaces.count) 处结构 · \(document.poses.count) 个位置")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }.accessibilityIdentifier("spatial.history.open")
                        .swipeActions {
                            Button("删除", role: .destructive) { controller.remove(document) }
                        }
                    }
                }.confirmationDialog("打开历史记录会替换当前扫描，请先保存需要保留的结果。", isPresented: Binding(get: { pendingHistory != nil }, set: { if !$0 { pendingHistory = nil } }), titleVisibility: .visible) {
                    Button("保留当前扫描，先返回保存") {
                        pendingHistory = nil; showingHistory = false
                    }
                }.navigationTitle("扫描记录")
                    .toolbar { Button("完成") { showingHistory = false }.accessibilityIdentifier("spatial.history.done") }
            }
        }
        .sheet(isPresented: Binding(get: { editingSurface != nil }, set: { if !$0 { editingSurface = nil } })) {
            if let id = editingSurface, let index = controller.document.surfaces.firstIndex(where: { $0.id == id }) {
                SpatialSurfaceEditor(surface: controller.document.surfaces[index]) { vertices in
                    controller.document.surfaces[index].vertices = vertices
                    controller.document.events.append("人工修正结构顶点: \(id)")
                    controller.exportURL = nil
                    editingSurface = nil
                }
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background || (phase != .active && controller.isScanning) { controller.stop() }
        }
        .onDisappear { controller.stop() }
    }
    private var combinedViewport: some View {
        let displayDocument=controller.semanticDisplayDocument
        let inputMatches=structurePreview.input == SpatialStructureInput(document:displayDocument)
        let structureMap=inputMatches ? structurePreview.result?.map:nil
        return GeometryReader { geometry in
            let size = geometry.size
            let inset: CGFloat = 12
            let small = CGSize(width: min(140, size.width * 0.34), height: min(160, size.height * 0.34))
            let mainCenter = CGPoint(x: size.width / 2, y: size.height / 2)
            let smallCenter = CGPoint(x: size.width - small.width / 2 - inset, y: size.height - small.height / 2 - inset)
            ZStack {
                // Stable sibling identity: swapping never creates another AR view/session.
                ZStack {
                    if controller.isScanning {
                        SpatialCameraView(controller: controller, layer: surfaceLayer, evidence: textureEvidence,structureMap:structureMap)
                            .overlay { SpatialLiveObjectOverlay(controller: controller) }
                    } else {
                        SpatialStructureView(document: controller.semanticDisplayDocument, layer: surfaceLayer,
                            imageDirectory: controller.keyframeDirectory, evidence: textureEvidence,structureMap:structureMap).id(controller.document.id)
                    }
                }
                .overlay(alignment: .topLeading) {
                    Text((controller.isScanning ? "实时画面" : "空间预览")+(structureMap.map {" · R\($0.revision)"} ?? " · 结构待核对"))
                        .font(.caption.bold()).padding(7).background(.regularMaterial, in: Capsule()).padding(8)
                        .accessibilityIdentifier("spatial.structure.revision")
                        .accessibilityValue(structureMap.map {"revision \($0.revision), epoch \($0.epoch)"} ?? "no matching structure revision")
                }
                .allowsHitTesting(!topViewIsPrimary)
                .frame(width: topViewIsPrimary ? small.width : size.width, height: topViewIsPrimary ? small.height : size.height)
                .clipShape(RoundedRectangle(cornerRadius: topViewIsPrimary ? 14 : 20))
                .position(topViewIsPrimary ? smallCenter : mainCenter)
                .zIndex(topViewIsPrimary ? 1 : 0)

                ZStack {
                    Color(uiColor: .secondarySystemBackground)
                    SpatialTopView(document: controller.semanticDisplayDocument, layer: surfaceLayer, livePose: controller.livePose,
                        isLive: controller.isScanning, compact: !topViewIsPrimary, onSummary: { sceneSummary = $0 },structureMap:structureMap,structureFailure:inputMatches ? structurePreview.failure:nil)
                        .scaleEffect(topViewIsPrimary ? mapZoom * mapMagnification : 1)
                        .offset(x: topViewIsPrimary ? mapOffset.width + mapDrag.width : 0, y: topViewIsPrimary ? mapOffset.height + mapDrag.height : 0)
                        .allowsHitTesting(false)
                }
                .overlay(alignment: .topLeading) {
                    Text("俯视图").font(.caption.bold()).padding(7).background(.regularMaterial, in: Capsule()).padding(8)
                }
                .contentShape(Rectangle())
                .onTapGesture(count: 2) {
                    if topViewIsPrimary { mapZoom = 1; mapOffset = .zero }
                }
                .gesture(DragGesture().updating($mapDrag) { value, state, _ in state = value.translation }
                    .onEnded { value in mapOffset.width += value.translation.width; mapOffset.height += value.translation.height }, including: topViewIsPrimary ? .all : .none)
                .simultaneousGesture(MagnifyGesture().updating($mapMagnification) { value, state, _ in state = value.magnification }
                    .onEnded { value in mapZoom = min(6, max(0.5, mapZoom * value.magnification)) }, including: topViewIsPrimary ? .all : .none)
                .frame(width: topViewIsPrimary ? size.width : small.width, height: topViewIsPrimary ? size.height : small.height)
                .clipShape(RoundedRectangle(cornerRadius: topViewIsPrimary ? 20 : 14))
                .position(topViewIsPrimary ? mainCenter : smallCenter)
                .zIndex(topViewIsPrimary ? 0 : 1)

                Button {
                    topViewIsPrimary.toggle()
                } label: {
                    RoundedRectangle(cornerRadius: 14).fill(Color.clear)
                        .overlay(alignment: .bottomTrailing) {
                            Image(systemName: "arrow.up.left.and.arrow.down.right")
                                .font(.caption.bold()).padding(7).background(.regularMaterial, in: Circle()).padding(6)
                        }
                        .overlay(RoundedRectangle(cornerRadius: 14).stroke(.white.opacity(0.85), lineWidth: 2))
                        .contentShape(RoundedRectangle(cornerRadius: 14))
                }
                .buttonStyle(.plain)
                .frame(width: small.width, height: small.height)
                .position(smallCenter)
                .zIndex(2)
                .accessibilityLabel(topViewIsPrimary ? "将画面切换为主窗口" : "将俯视图切换为主窗口")
                .accessibilityValue(topViewIsPrimary ? "俯视图为主窗口" : "画面为主窗口")
                .accessibilityIdentifier("spatial.viewport.swap")

                VStack {
                    Spacer()
                    HStack {
                        Text(controller.isScanning ? controller.captureGuidance.message : (controller.captureSummary ?? (topViewIsPrimary ? "拖动或缩放 · 双击复位" : "开始扫描或查看已有空间")))
                            .font(.caption).padding(8).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
                            .frame(maxWidth: max(0, size.width - small.width - 3 * inset), alignment: .leading)
                            .accessibilityIdentifier("spatial.coach.caption")
                        Spacer()
                    }
                }.padding(inset).allowsHitTesting(false).zIndex(3)
            }
        }
        .onAppear {structurePreview.update(displayDocument)}
        .onChange(of:displayDocument) {value in structurePreview.update(value)}
        .onDisappear {structurePreview.cancel()}
        .aspectRatio(3 / 4, contentMode: .fit)
        .frame(maxHeight: 460)
        .clipShape(RoundedRectangle(cornerRadius: 20))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("spatial.structure")
    }

}

private struct SpatialCameraView: UIViewRepresentable {
    let controller: SpatialScanController
    let layer: SpatialSurfaceLayer
    let evidence: SpatialTextureEvidence
    var structureMap:SpatialStructureMap? = nil
    func makeCoordinator() -> SpatialSceneCache { SpatialSceneCache() }
    func makeUIView(context: Context) -> ARSCNView {
        let view = SpatialLiveObjectCameraView()
        view.viewportChanged = { [weak controller] size, orientation in
            controller?.updateLiveObjectViewport(size: size, orientation: orientation)
        }
        view.session = controller.session
        view.automaticallyUpdatesLighting = false
        view.scene = SCNScene()
        return view
    }
    func updateUIView(_ view: ARSCNView, context: Context) {
        let scene = view.scene
        context.coordinator.bindLive(controller.livePose)
        context.coordinator.populate(scene, document: controller.semanticDisplayDocument, layer: layer,
                                     imageDirectory: controller.keyframeDirectory, liveOverlay: true, evidence: evidence, hypothesis: nil,structureMap:structureMap)
    }
}

private struct SpatialStructureView: UIViewRepresentable {
    let document: SpatialScanDocument
    let layer: SpatialSurfaceLayer
    let imageDirectory: URL
    let evidence: SpatialTextureEvidence
    var hypothesis: SpatialSceneHypothesis? = nil
    var structureMap:SpatialStructureMap? = nil
    func makeCoordinator() -> SpatialSceneCache { SpatialSceneCache() }
    func makeUIView(context: Context) -> SCNView {
        let view = SCNView()
        view.backgroundColor = .secondarySystemBackground
        view.allowsCameraControl = true
        view.autoenablesDefaultLighting = true
        view.scene = SCNScene()
        return view
    }
    func updateUIView(_ view: SCNView, context: Context) {
        guard let scene = view.scene else { return }
        context.coordinator.populate(scene, document: document, layer: layer,
                                     imageDirectory: imageDirectory, liveOverlay: false, evidence: evidence, hypothesis: hypothesis,structureMap:structureMap)
        let reframe=context.coordinator.consumeCameraRevision(structureMap)
        if scene.rootNode.childNode(withName: "camera", recursively: false) == nil || reframe {
            scene.rootNode.childNode(withName:"camera",recursively:false)?.removeFromParentNode()
            let epoch=structureMap?.epoch ?? document.keyframes.last?.pose.epoch
            let points = document.surfaces.filter {$0.epoch == epoch}.flatMap(\.vertices) + document.poses.filter {$0.epoch == epoch}.map(\.position) + SpatialStructurePresentation.points(structureMap)
            let x = points.map(\.x), z = points.map(\.z)
            let cx = ((x.min() ?? 0) + (x.max() ?? 0)) / 2
            let cz = ((z.min() ?? 0) + (z.max() ?? 0)) / 2
            let radius = max(3, max((x.max() ?? 0) - (x.min() ?? 0), (z.max() ?? 0) - (z.min() ?? 0)))
            let camera = SCNNode(); camera.name = "camera"; camera.camera = SCNCamera()
            camera.position = SCNVector3(cx + radius, radius, cz + radius)
            camera.look(at: SCNVector3(cx, 0, cz))
            scene.rootNode.addChildNode(camera); view.pointOfView = camera
        }
    }
}

private final class SpatialTextureEvidence: ObservableObject {
    struct Row: Identifiable, Equatable { let id:String; var status:String; let imageFile:String?; let candidates:[SpatialProjectiveTexturing.Candidate] }
    @Published private(set) var rows:[Row]=[]
    @Published private(set) var cacheStats:[String:Int]=[:]
    private var generation=UUID()
    var summary:String {
        let supported=rows.filter {$0.status.hasPrefix("已投影")}.count
        return "照片投影 \(supported) / \(rows.count) 个观测面；其余保留未知底色与边界。贴图不表示完整墙面。"
    }
    private var pendingRows:[Row]=[],pendingStats:[String:Int]=[:],flushScheduled=false
    func reset(generation:UUID,rows:[Row],stats:[String:Int]) {
        self.generation=generation;pendingRows=rows;pendingStats=stats;scheduleFlush()
    }
    func update(generation:UUID,id:String,status:String,stats:[String:Int]) {
        guard self.generation==generation,let index=pendingRows.firstIndex(where: {$0.id==id}) else {return}
        pendingRows[index].status=status;pendingStats=stats;scheduleFlush()
    }
    private func scheduleFlush() {
        guard !flushScheduled else {return};flushScheduled=true
        Task { @MainActor [weak self] in
            guard let self else {return};self.flushScheduled=false
            if self.rows != self.pendingRows {self.rows=self.pendingRows}
            if self.cacheStats != self.pendingStats {self.cacheStats=self.pendingStats}
        }
    }

}

private final class SpatialSceneCache {
    private var cameraStructureRevision:Int?
    func consumeCameraRevision(_ map:SpatialStructureMap?)->Bool {
        let revision=map?.revision
        guard cameraStructureRevision != revision else {return false}
        cameraStructureRevision=revision;return true
    }
    private var surfaces: [SpatialSurface] = []
    private var textureKey = ""
    private let textures = SpatialDecodedTextureCache()
    private var textureGeneration = UUID()
    private var trajectoryCache = SpatialTrajectoryAppendCache()
    private var chunks: [[SCNVector3]] = []
    private var chunkNodes: [SCNNode] = []
    private weak var currentScene: SCNScene?
    private weak var boundLive: SpatialLivePoseState?
    private var liveSubscription: AnyCancellable?
    private var lastHistoryPose: SpatialPose?

    func bindLive(_ state: SpatialLivePoseState) {
        guard boundLive !== state else { return }
        boundLive = state
        liveSubscription = state.$pose.sink { [weak self] pose in self?.applyLive(pose) }
    }

    private func applyLive(_ pose: SpatialPose?) {
        guard let scene = currentScene else { return }
        let existing = scene.rootNode.childNode(withName: "livePose", recursively: false)
        guard let pose else { existing?.removeFromParentNode(); return }
        let root = existing ?? SCNNode(); root.name = "livePose"
        if existing == nil { scene.rootNode.addChildNode(root) }
        var edges = [SCNVector3]()
        if let previous = lastHistoryPose, SpatialTrajectoryAppendCache.canConnect(previous, pose) {
            edges += [SCNVector3(previous.position.x, previous.position.y, previous.position.z), SCNVector3(pose.position.x, pose.position.y, pose.position.z)]
        }
        let p = pose.position, f = pose.forward
        edges += [SCNVector3(p.x,p.y,p.z), SCNVector3(p.x+f.x*0.15,p.y+f.y*0.15,p.z+f.z*0.15)]
        root.geometry = lines(edges, color: .systemOrange).geometry
    }
    private var activeLayer: SpatialSurfaceLayer?
    private var semanticRows: [SpatialIndoorSemanticObservation] = []
    private var structureMap:SpatialStructureMap?
    func populate(_ scene: SCNScene, document: SpatialScanDocument, layer: SpatialSurfaceLayer,
                  imageDirectory: URL, liveOverlay: Bool, evidence: SpatialTextureEvidence, hypothesis: SpatialSceneHypothesis?,structureMap:SpatialStructureMap?) {
        let nextKey = document.id.uuidString + ":" + document.keyframes.map(\.imageFile).joined(separator: ",")
        let sameGeometry = surfaces.count==document.surfaces.count && zip(surfaces,document.surfaces).allSatisfy { a,b in
            a.id==b.id && a.kind==b.kind && a.vertices==b.vertices && a.observed==b.observed && a.epoch==b.epoch
        }
        if !sameGeometry || activeLayer != layer || textureKey != nextKey || semanticRows != (document.indoorSemanticObservations ?? []) || self.structureMap != structureMap {
            self.structureMap=structureMap
            scene.rootNode.childNode(withName:"structureLines",recursively:false)?.removeFromParentNode()
            scene.rootNode.childNode(withName:"hypothesisLines",recursively:false)?.removeFromParentNode()
            semanticRows = document.indoorSemanticObservations ?? []
            activeLayer = layer
            surfaces = document.surfaces
            textureKey = nextKey
            textureGeneration = UUID()
            let generation=textureGeneration
            var rows:[SpatialTextureEvidence.Row] = []
            scene.rootNode.childNode(withName: "surfaces", recursively: false)?.removeFromParentNode()
            let root = SCNNode(); root.name = "surfaces"
            var edgesByRole: [String: [SCNVector3]] = [:]
            let classifications = SpatialSemanticRendering.classifications(document)
            for surface in surfaces where surface.epoch == (document.keyframes.last?.pose.epoch ?? document.surfaces.last?.epoch) && layer.includes(surface) && surface.observed && surface.vertices.count >= 3 && surface.vertices.allSatisfy(\.isFinite) {
                let semantic: SpatialSurfaceSemanticEvidence.Semantic = classifications[surface.id] == .other ? .other:.unknown
                let vertices = surface.vertices.map { SCNVector3($0.x,$0.y,$0.z) }
                // Default structure never fills an anchor polygon, including semantic floor anchors.
                let role = semantic.rawValue
                for i in vertices.indices {
                    let a = surface.vertices[i], b = surface.vertices[(i+1)%vertices.count]
                    if semantic == .wall || semantic == .floor {
                        edgesByRole[role,default:[]] += [vertices[i],vertices[(i+1)%vertices.count]]
                    } else {
                        let dx = b.x-a.x, dy = b.y-a.y, dz = b.z-a.z
                        let length = sqrt(dx*dx+dy*dy+dz*dz)
                        let segments = max(1,Int(min(2048,ceil(length/0.08))))
                        for step in stride(from:0,to:segments,by:2) {
                            let lo = Float(step)/Float(segments), hi = Float(min(step+1,segments))/Float(segments)
                            edgesByRole[role,default:[]] += [SCNVector3(a.x+lo*dx,a.y+lo*dy,a.z+lo*dz),SCNVector3(a.x+hi*dx,a.y+hi*dy,a.z+hi*dz)]
                        }
                    }
                }
                let title: String
                switch semantic { case .wall: title = "多照片支持墙面"; case .floor: title = "多照片支持地面"; case .other: title = "类别证据冲突，原始几何保留"; case .unknown: title = "类别尚未确认，原始几何保留" }
                rows.append(SpatialTextureEvidence.Row(id:surface.id,status:title+"；仅显示观测边界",imageFile:nil,candidates:[]))
            }
            for edges in edgesByRole.values {
                let node = lines(edges,color:.systemGray.withAlphaComponent(0.5)); node.renderingOrder = 10
                root.addChildNode(node)
            }
            scene.rootNode.addChildNode(root)
            for surface in document.surfaces where !surface.observed || !layer.includes(surface) || surface.vertices.count<3 || !surface.vertices.allSatisfy(\.isFinite) {
                let state = !surface.observed ? "未确认观测，原记录保留" : !layer.includes(surface) ? "当前图层未显示，原观测保留" : "几何数据无效，原记录与失败保留"
                rows.append(SpatialTextureEvidence.Row(id:surface.id,status:state,imageFile:nil,candidates:[]))
            }
            _ = textures.beginScope(names:[],directory:imageDirectory)
            evidence.reset(generation:generation,rows:rows,stats:self.textures.snapshot())
            if let hypothesis, layer != .horizontal {
                let root = SCNNode()
                root.name = "hypothesisLines"
                for line in hypothesis.walls + hypothesis.objects {
                    let a = line.start, b = line.end
                    let dashed = true
                    let length = SpatialScanGeometry.distance(a, b)
                    let n = dashed ? max(1, Int(ceil(length / 0.08))) : 1
                    var edges: [SCNVector3] = []
                    for i in stride(from: 0, to: n, by: dashed ? 2 : 1) {
                        let t = Float(i) / Float(n), u = Float(min(i + 1, n)) / Float(n)
                        edges += [
                            SCNVector3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t),
                            SCNVector3(a.x + (b.x - a.x) * u, a.y + (b.y - a.y) * u, a.z + (b.z - a.z) * u),
                        ]
                    }
                    root.addChildNode(lines(edges, color: .systemGray))
                }
                scene.rootNode.addChildNode(root)
            }
            if let structureMap {
                let root=SCNNode();root.name="structureLines";root.renderingOrder=20
                func add(_ a:SpatialPoint,_ b:SpatialPoint,_ color:UIColor,_ dashed:Bool) {
                    let node=lines(SpatialStructurePresentation.edges(a,b,dashed:dashed),color:color)
                    node.renderingOrder=20;root.addChildNode(node)
                }
                if layer != .vertical {
                    for region in structureMap.floorRegions {
                        for ring in [region.outerRing]+region.holes where ring.count>=3 {
                            for i in ring.indices {add(ring[i],ring[(i+1)%ring.count],.systemTeal,true)}
                        }
                    }
                }
                if layer != .horizontal {
                    for wall in structureMap.wallSegments {
                        let observed=wall.startProvenance == .observed && wall.endProvenance == .observed
                        add(wall.start,wall.end,observed ? .systemPurple:.systemOrange,!observed)
                    }
                    for gap in structureMap.gaps {
                        if SpatialScanGeometry.distance(gap.start,gap.end)<0.001 {
                            let p=gap.start
                            add(SpatialPoint(x:p.x-0.04,y:p.y,z:p.z),SpatialPoint(x:p.x+0.04,y:p.y,z:p.z),.systemRed,false)
                            add(SpatialPoint(x:p.x,y:p.y,z:p.z-0.04),SpatialPoint(x:p.x,y:p.y,z:p.z+0.04),.systemRed,false)
                        } else {add(gap.start,gap.end,.systemRed,true)}
                    }
                }
                scene.rootNode.addChildNode(root)
                rows.append(SpatialTextureEvidence.Row(id:"structure-revision",status:SpatialStructurePresentation.summary(structureMap),imageFile:nil,candidates:[]))
                evidence.reset(generation:generation,rows:rows,stats:self.textures.snapshot())
            }
        }
        scene.rootNode.childNode(withName: "objectLocations", recursively: false)?.removeFromParentNode()
        let epoch = document.keyframes.last?.pose.epoch ?? document.surfaces.last?.epoch
        currentScene = scene
        lastHistoryPose = document.poses.last
        var trajectoryDocument=document
        trajectoryDocument.poses=document.poses.filter { $0.epoch == epoch }
        let change = trajectoryCache.consume(trajectoryDocument)
        if change.reset {
            scene.rootNode.childNode(withName: "trajectory", recursively: false)?.removeFromParentNode()
            chunks.removeAll(); chunkNodes.removeAll()
        }
        let trajectory = scene.rootNode.childNode(withName: "trajectory", recursively: false) ?? SCNNode()
        trajectory.name = "trajectory"
        if trajectory.parent == nil { scene.rootNode.addChildNode(trajectory) }
        var changed = Set<Int>()
        for (a,b) in change.edges {
            if chunks.isEmpty || chunks[chunks.count-1].count >= 128 {
                chunks.append([]); let node = SCNNode(); chunkNodes.append(node); trajectory.addChildNode(node)
            }
            let index = chunks.count-1
            chunks[index] += [SCNVector3(a.position.x,a.position.y,a.position.z), SCNVector3(b.position.x,b.position.y,b.position.z)]
            changed.insert(index)
        }
        for index in changed { chunkNodes[index].geometry = lines(chunks[index], color: .systemOrange).geometry }
        if let boundLive { applyLive(boundLive.pose) }

    }

    private func lines(_ vertices: [SCNVector3], color: UIColor) -> SCNNode {
        guard !vertices.isEmpty else { return SCNNode() }
        let geometry = SCNGeometry(sources: [SCNGeometrySource(vertices: vertices)], elements: [SCNGeometryElement(indices: vertices.indices.map { Int32($0) }, primitiveType: .line)])
        let material = SCNMaterial(); material.diffuse.contents = color; material.lightingModel = .constant
        geometry.materials = [material]; return SCNNode(geometry: geometry)
    }
}

private enum SpatialStructurePresentation {
    static func points(_ map:SpatialStructureMap?)->[SpatialPoint] {
        guard let map else {return []}
        var points=map.wallSegments.flatMap {[$0.start,$0.end]}
        points += map.corners.map(\.position)
        points += map.gaps.flatMap {[$0.start,$0.end]}
        for region in map.floorRegions {points += region.outerRing;points += region.holes.flatMap {$0}}
        return points.filter(\.isFinite)
    }
    static func summary(_ map:SpatialStructureMap)->String {
        map.statusLine+" · 地面候选闭环\(map.floorRegions.count) · 未闭合记录\(map.gaps.count) · "+(map.floorRegions.isEmpty ? "还没看全":"候选轮廓，未核对房间尺寸")+" · "+map.quality.observedCoverageLine
    }
    static func edges(_ a:SpatialPoint,_ b:SpatialPoint,dashed:Bool)->[SCNVector3] {
        guard a.isFinite,b.isFinite else {return []}
        let length=SpatialScanGeometry.distance(a,b)
        let count=dashed ? max(1,Int(min(4096,ceil(length/0.08)))):1
        var result:[SCNVector3]=[]
        for i in stride(from:0,to:count,by:dashed ? 2:1) {
            let lo=Float(i)/Float(count),hi=Float(min(i+1,count))/Float(count)
            result += [SCNVector3(a.x+(b.x-a.x)*lo,a.y+(b.y-a.y)*lo,a.z+(b.z-a.z)*lo),SCNVector3(a.x+(b.x-a.x)*hi,a.y+(b.y-a.y)*hi,a.z+(b.z-a.z)*hi)]
        }
        return result
    }
}

private enum SpatialSemanticRendering {
    static func classifications(_ document: SpatialScanDocument) -> [String: SpatialSurfaceSemanticEvidence.Semantic] {
        let records = (document.indoorSemanticObservations ?? []).filter { $0.status == .processed }.flatMap(\.records)
        let bySurface = Dictionary(grouping:records,by: \.surfaceID)
        var result: [String: SpatialSurfaceSemanticEvidence.Semantic] = [:]
        for surface in document.surfaces {
            result[surface.id] = SpatialSurfaceSemanticEvidence.resolve(surface:surface,records:bySurface[surface.id] ?? [])
        }
        return result
    }
    static func walls(_ document: SpatialScanDocument) -> [SpatialSurface] {
        let mapping = classifications(document)
        return document.surfaces.filter { $0.observed && $0.kind == "vertical" && mapping[$0.id] == .wall }
    }
    static func color(_ semantic: SpatialSurfaceSemanticEvidence.Semantic) -> UIColor {
        switch semantic { case .wall: return .systemPurple; case .floor: return .systemTeal; case .other: return .systemOrange; case .unknown: return .systemGray }
    }
}

private struct SpatialTopProjection {
    let scale: CGFloat, offset: CGPoint
    init(document: SpatialScanDocument, size: CGSize, extra: [SpatialPoint] = [], layer: SpatialSurfaceLayer = .all) {
        let visible = document.surfaces.filter { layer.includes($0) }
        let drawn = visible.filter { $0.observed && $0.vertices.allSatisfy(\.isFinite) }.flatMap(\.vertices)
        let points = drawn + extra + document.poses.map(\.position)
        let x = points.map(\.x), z = points.map(\.z)
        let minX = x.min() ?? -1, maxX = x.max() ?? 1, minZ = z.min() ?? -1, maxZ = z.max() ?? 1
        scale = min((size.width-32)/CGFloat(max(2,maxX-minX)), (size.height-32)/CGFloat(max(2,maxZ-minZ)))
        offset = CGPoint(x: size.width/2-CGFloat((minX+maxX)/2)*scale, y: size.height/2-CGFloat((minZ+maxZ)/2)*scale)
    }
    func map(_ point: SpatialPoint) -> CGPoint { CGPoint(x: offset.x+CGFloat(point.x)*scale, y: offset.y+CGFloat(point.z)*scale) }
}

private final class SpatialTopPathCache: ObservableObject {
    private var cache = SpatialTrajectoryAppendCache()
    private var path = CGMutablePath()
    func update(_ document: SpatialScanDocument) -> CGPath {
        let change = cache.consume(document)
        if change.reset { path = CGMutablePath() }
        for (a,b) in change.edges {
            path.move(to: CGPoint(x: CGFloat(a.position.x), y: CGFloat(a.position.z)))
            path.addLine(to: CGPoint(x: CGFloat(b.position.x), y: CGFloat(b.position.z)))
        }
        return path
    }
}

private struct SpatialSceneInput: Equatable, Sendable {
    let scanID: UUID
    let frames: [SpatialKeyframe]
    let rows: [SpatialIndoorSemanticObservation]
    let surfaces: [SpatialSurface]
    var document: SpatialScanDocument {
        var doc = SpatialScanDocument(id: scanID)
        doc.keyframes = frames
        doc.surfaces = surfaces
        doc.indoorSemanticObservations = rows
        return doc
    }
}

private final class SpatialTopSceneCache: ObservableObject {
    @Published private(set) var result: SpatialSceneHypothesis?
    private(set) var input: SpatialSceneInput?
    private var generation = UUID()
    private var task: Task<Void, Never>?
    func update(_ input: SpatialSceneInput) {
        guard self.input != input else { return }
        self.input = input
        result = nil
        generation = UUID()
        task?.cancel()
        let token = generation
        task = Task { @MainActor [weak self] in
            let worker = Task.detached(priority: .utility) {
                SpatialSceneHypothesis.rebuild(document: input.document)
            }
            let result = await withTaskCancellationHandler(operation: { await worker.value }, onCancel: { worker.cancel() })
            guard let self, self.generation == token, !Task.isCancelled else { return }
            self.result = result
        }
    }
    func cancel() { generation = UUID(); task?.cancel(); task = nil; input = nil; result = nil }
}

/// This view observes only the slow persisted document. Live invalidations stay
/// inside the small UIKit arrow/tail view below, not in the static Canvas.
@MainActor private final class SpatialStructurePreviewCache: ObservableObject {
    @Published private(set) var result: SpatialStructureSnapshot?
    @Published private(set) var failure:String?
    private(set) var input:SpatialStructureInput?
    private var task:Task<Void,Never>?
    private var generation=UUID()
    func update(_ value:SpatialScanDocument) {
        let typedInput=SpatialStructureInput(document:value)
        guard input != typedInput else {return}
        input=typedInput;result=nil;failure=nil;generation=UUID();task?.cancel()
        let token=generation
        task=Task { [weak self] in
            do {
                let worker=Task.detached(priority:.utility) {
                    let snapshot=try await SpatialStructureSnapshotCache.shared.snapshot(for:value)
                    guard snapshot.matches(document:value) else {throw CocoaError(.fileReadCorruptFile)}
                    return snapshot
                }
                let snapshot=try await withTaskCancellationHandler(operation:{try await worker.value},onCancel:{worker.cancel()})
                guard let self,!Task.isCancelled,self.generation == token else {return}
                self.result=snapshot
            } catch {
                guard let self,!Task.isCancelled,self.generation == token else {return}
                self.failure="结构加载失败；未显示旧版本"
            }
        }
    }
    func cancel() {task?.cancel();task=nil;generation=UUID();input=nil;result=nil;failure=nil}
}

private struct SpatialTopView: View {
    let document: SpatialScanDocument
    let layer: SpatialSurfaceLayer
    let livePose: SpatialLivePoseState
    let isLive: Bool
    var compact = false
    var onSummary: (String) -> Void = { _ in }
    var structureMap:SpatialStructureMap? = nil
    var structureFailure:String? = nil
    @StateObject private var cache = SpatialTopPathCache()
    @StateObject private var sceneCache = SpatialTopSceneCache()
    var body: some View {
        let input = SpatialSceneInput(
            scanID: document.id, frames: document.keyframes,
            rows: document.indoorSemanticObservations ?? [], surfaces: document.surfaces)
        let sceneBatch = sceneCache.input == input ? sceneCache.result : nil
        let structureSummary = structureMap.map(SpatialStructurePresentation.summary) ?? structureFailure ?? "结构待核对；未显示旧版本"
        let epoch=structureMap?.epoch ?? document.keyframes.last?.pose.epoch ?? document.surfaces.last?.epoch
        let scoped: SpatialScanDocument = {
            var result=document
            result.surfaces=document.surfaces.filter { $0.epoch == epoch }
            result.poses=document.poses.filter { $0.epoch == epoch }
            return result
        }()
        GeometryReader { geometry in
            let walls = sceneBatch?.walls ?? []
        let structureExtras = SpatialStructurePresentation.points(structureMap)
        let projection = SpatialTopProjection(document: scoped, size: geometry.size, extra: structureExtras, layer: layer)
            ZStack {
                Canvas { context, size in
                    let unknown = (sceneBatch?.unknownHorizontals ?? scoped.surfaces.filter { $0.kind == "horizontal" })
                        + (sceneBatch?.unknownVerticals ?? scoped.surfaces.filter { $0.kind == "vertical" })
                    for surface in unknown where layer.includes(surface) && surface.observed && surface.vertices.count >= 3 && surface.vertices.allSatisfy(\.isFinite) {
                        var path=Path();path.move(to:projection.map(surface.vertices[0]))
                        for p in surface.vertices.dropFirst() {path.addLine(to:projection.map(p))};path.closeSubpath()
                        context.stroke(path,with:.color(.gray.opacity(0.6)),style:StrokeStyle(lineWidth:1,dash:[5,3]))
                    }
                    for surface in sceneBatch?.conflictSurfaces ?? [] where layer.includes(surface) && surface.vertices.count >= 3 {
                        var path=Path();path.move(to:projection.map(surface.vertices[0]))
                        for p in surface.vertices.dropFirst() {path.addLine(to:projection.map(p))};path.closeSubpath()
                        context.stroke(path,with:.color(.gray.opacity(0.6)),style:StrokeStyle(lineWidth:1,dash:[5,3]))
                    }
                    if layer != .vertical {
                        for region in structureMap?.floorRegions ?? [] {
                            for ring in [region.outerRing]+region.holes where ring.count>=3 {
                                var path=Path();path.move(to:projection.map(ring[0]))
                                for p in ring.dropFirst() {path.addLine(to:projection.map(p))};path.closeSubpath()
                                context.stroke(path,with:.color(.teal),style:StrokeStyle(lineWidth:2,dash:[7,4]))
                            }
                        }
                    }
                    if layer != .horizontal {
                        for line in walls + (sceneBatch?.objects ?? []) {
                            var path=Path();path.move(to:projection.map(line.start));path.addLine(to:projection.map(line.end))
                            context.stroke(path,with:.color(.gray.opacity(0.5)),style:StrokeStyle(lineWidth:1,dash:[5,3]))
                        }
                        for segment in structureMap?.wallSegments ?? [] {
                            var path = Path()
                            path.move(to: projection.map(segment.start))
                            path.addLine(to: projection.map(segment.end))
                            let observed=segment.startProvenance == .observed && segment.endProvenance == .observed
                            context.stroke(path, with: .color(observed ? .purple:.orange), style: StrokeStyle(lineWidth: 2.5, lineCap: .round, dash:observed ? []:[5,3]))
                        }
                        for corner in structureMap?.corners ?? [] {
                            let c = projection.map(corner.position)
                            let r: CGFloat = 4
                            context.fill(Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2)), with: .color(.orange))
                        }
                        for gap in structureMap?.gaps ?? [] {
                            var path=Path()
                            if SpatialScanGeometry.distance(gap.start,gap.end)<0.001 {
                                let p=projection.map(gap.start);path.addEllipse(in:CGRect(x:p.x-5,y:p.y-5,width:10,height:10))
                            } else {path.move(to:projection.map(gap.start));path.addLine(to:projection.map(gap.end))}
                            context.stroke(path,with:.color(.red),style:StrokeStyle(lineWidth:2,dash:[3,4]))
                        }
                    }
                    var world = context
                    world.translateBy(x: projection.offset.x, y: projection.offset.y)
                    world.scaleBy(x: projection.scale, y: projection.scale)
                    world.stroke(Path(cache.update(scoped)), with: .color(.orange), lineWidth: 3/max(projection.scale,0.001))
                    if !isLive, let pose = scoped.poses.last {
                        let p = projection.map(pose.position)
                        context.fill(Path(ellipseIn: CGRect(x:p.x-4,y:p.y-4,width:8,height:8)), with: .color(.orange))
                    }
                    if document.poses.isEmpty && document.surfaces.isEmpty {
                        context.draw(Text(compact ? "尚未扫描" : "移动后，轨迹和已观察结构会出现在这里").font(.caption).foregroundColor(.secondary), at: CGPoint(x:size.width/2,y:size.height/2))
                    }
                }
                if isLive { SpatialLiveTopLayer(livePose: livePose, projection: projection, historyPose: scoped.poses.last) }
            }
            .overlay(alignment:.bottomLeading) {
                Group {
                    Text(compact ? (structureMap.map {"R\($0.revision) · 未闭合\($0.gaps.count)"} ?? (structureFailure == nil ? "结构加载中":"结构加载失败")) : structureSummary)
                        .font(.caption2).foregroundStyle(.secondary).padding(5).background(.regularMaterial,in:RoundedRectangle(cornerRadius:6))
                        .lineLimit(2)
                        .padding(.leading,compact ? 8:12)
                        .padding(.bottom,compact ? 28:52)
                        .accessibilityIdentifier("spatial.structureOverlay.status")
                }
            }
        }
        .onReceive(sceneCache.$result) { result in
            guard sceneCache.input == input else { return }
            if let result {
                onSummary(result.publishedStatusLine + " · " + structureSummary)
            } else { onSummary("结构加载中 · 空白为未知 · " + structureSummary) }
        }
        .onChange(of:structureMap) {value in onSummary(value.map(SpatialStructurePresentation.summary) ?? structureSummary)}
        .onAppear { sceneCache.update(input) }
        .onChange(of:input) { value in sceneCache.update(value) }
        .onDisappear { sceneCache.cancel() }
        // The graph is one accessible element even when its Canvas has no shapes.
        .accessibilityElement(children: .ignore)
        .accessibilityValue(structureSummary)
        .accessibilityLabel("已观察空间与行走轨迹俯视图")
        .accessibilityIdentifier("spatial.topView")
    }
}

private struct SpatialLiveTopLayer: UIViewRepresentable {
    let livePose: SpatialLivePoseState
    let projection: SpatialTopProjection
    let historyPose: SpatialPose?
    final class Coordinator {
        var subscription: AnyCancellable?
        weak var state: SpatialLivePoseState?
        func bind(_ state: SpatialLivePoseState, view: SpatialLiveTopUIView) {
            guard self.state !== state else { return }
            self.state = state
            subscription = state.$pose.sink { [weak view, weak state] pose in view?.apply(pose, received: state?.receivedAt ?? 0) }
        }
    }
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeUIView(context: Context) -> SpatialLiveTopUIView {
        let view = SpatialLiveTopUIView(); context.coordinator.bind(livePose, view: view); return view
    }
    func updateUIView(_ view: SpatialLiveTopUIView, context: Context) {
        view.projection = projection; view.historyPose = historyPose
        context.coordinator.bind(livePose, view: view)
        view.apply(livePose.pose, received: livePose.receivedAt)
    }
}

/// Marker and arrow are sibling layers committed together; the observer reads
/// the composed ReplayKit pixels, not the CPU timestamp of this transaction.
private final class SpatialLiveTopUIView: UIView {
    var projection: SpatialTopProjection?
    var historyPose: SpatialPose?
    private let arrow = CAShapeLayer(), tail = CAShapeLayer(), marker = CAShapeLayer()
    private var previousSource = -Double.infinity
    private var currentID: UInt32?
    override init(frame: CGRect) {
        super.init(frame:frame)
        isUserInteractionEnabled = false
        arrow.fillColor = UIColor.systemOrange.cgColor
        tail.strokeColor = UIColor.systemOrange.cgColor; tail.fillColor = nil; tail.lineWidth = 3
        marker.fillColor = UIColor.white.cgColor; marker.backgroundColor = UIColor.black.cgColor
        layer.addSublayer(tail); layer.addSublayer(arrow); layer.addSublayer(marker)
        marker.isHidden = true
    }
    required init?(coder:NSCoder) { fatalError("init(coder:) is unavailable") }
    func apply(_ pose:SpatialPose?, received:Double) {
        CATransaction.begin(); CATransaction.setDisableActions(true)
        defer { CATransaction.commit() }
        guard let pose, let projection else {
            arrow.path = nil; tail.path = nil; marker.isHidden = true; previousSource = -.infinity; currentID = nil
            SpatialScreenPerformanceProbe.shared.hideMarker()
            return
        }
        let p = projection.map(pose.position), length = hypot(pose.forward.x,pose.forward.z)
        let path = CGMutablePath()
        if length > 0.001 {
            let dx = CGFloat(pose.forward.x/length), dy = CGFloat(pose.forward.z/length)
            path.move(to:CGPoint(x:p.x+dx*12,y:p.y+dy*12))
            path.addLine(to:CGPoint(x:p.x-dx*7-dy*7,y:p.y-dy*7+dx*7))
            path.addLine(to:CGPoint(x:p.x-dx*7+dy*7,y:p.y-dy*7-dx*7)); path.closeSubpath()
        } else { path.addEllipse(in:CGRect(x:p.x-4,y:p.y-4,width:8,height:8)) }
        arrow.path = path
        let tailPath = CGMutablePath()
        if let previous = historyPose, SpatialTrajectoryAppendCache.canConnect(previous,pose) {
            tailPath.move(to:projection.map(previous.position)); tailPath.addLine(to:p)
        }
        tail.path = tailPath
        marker.frame = CGRect(x:max(0,bounds.width-42),y:max(0,bounds.height-48),width:36,height:42)
        if pose.time > previousSource {
            previousSource = pose.time
            if let window {
                currentID = SpatialScreenPerformanceProbe.shared.registerApplied(pose:pose, received:received,
                    rect:convert(marker.frame,to:window), window:window.bounds.size)
            }
        }
        guard SpatialScreenPerformanceProbe.shared.isSharing, let id = currentID else { marker.isHidden = true; return }
        let code = CGMutablePath()
        for (index,white) in SpatialScreenMarker.bits(id).enumerated() where white {
            code.addRect(CGRect(x:(index%6)*6,y:(index/6)*6,width:6,height:6))
        }
        marker.path = code; marker.isHidden = false
    }
}

private struct SpatialSurfaceEditor: View {
    let surface: SpatialSurface
    let onSave: ([SpatialPoint]) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var values: [[String]] = []
    private var parsed: [SpatialPoint]? {
        var points = [SpatialPoint]()
        for row in values {
            guard row.count == 3, let x = Float(row[0]), let y = Float(row[1]), let z = Float(row[2]), x.isFinite, y.isFinite, z.isFinite else { return nil }
            points.append(SpatialPoint(x: x,y: y,z: z))
        }
        return points.count >= 3 ? points : nil
    }
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("人工修正已观察边界，单位为米。坐标相对本次扫描起点；修正不会补全未扫描区域。")
                    Text("保存后，请在结构视图检查形状，并重新保存扫描记录。")
                }
                ForEach(values.indices, id: \.self) { index in
                    Section("顶点 \(index + 1)") {
                        ForEach(0..<3, id: \.self) { axis in
                            HStack {
                                Text(["左右 X", "高度 Y", "前后 Z"][axis])
                                TextField("米", text: $values[index][axis]).keyboardType(.numbersAndPunctuation).multilineTextAlignment(.trailing)
                            }
                        }
                    }
                }
                if parsed == nil { Text("请输入至少三个有效顶点坐标。采用小数点表示小数。").foregroundStyle(.orange) }
            }.navigationTitle("修正结构")
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                    ToolbarItem(placement: .confirmationAction) { Button("应用修正") { if let points = parsed { onSave(points) } }.disabled(parsed == nil).accessibilityIdentifier("spatial.edit.apply") }
                }
        }.onAppear { values = surface.vertices.map { [String($0.x),String($0.y),String($0.z)] } }
    }
}

private enum SpatialSurfaceLayer: String, CaseIterable {
    case all, vertical, horizontal
    var title: String {
        switch self { case .all: return "全部观测"; case .vertical: return "垂直面"; case .horizontal: return "水平面" }
    }
    func includes(_ surface: SpatialSurface) -> Bool { self == .all || surface.kind == rawValue }
    static func name(_ surface: SpatialSurface) -> String {
        switch surface.kind { case "vertical": return "垂直面候选"; case "horizontal": return "水平面候选"; default: return "未分类面" }
    }
    static func uiColor(_ surface: SpatialSurface) -> UIColor { uiColor(surface.kind) }
    static func uiColor(_ kind: String) -> UIColor {
        switch kind { case "vertical": return .systemPurple; case "horizontal": return .systemTeal; default: return .systemGray }
    }
}
