import SwiftUI
import ARKit
import SceneKit
import Combine

/// Observed geometry only: no inferred rooms or connections across tracking resets.
struct SpatialScanScreen: View {
    @ObservedObject var controller: SpatialScanController
    let onOutdoor: () -> Void
    let onLegacy: () -> Void
    let onDebug: () -> Void
    @Environment(\.scenePhase) private var scenePhase
    @State private var showingHistory = false
    @State private var confirmingClear = false
    @State private var confirmingNew = false
    @State private var pendingHistory: SpatialScanDocument?
    @State private var editingSurface: String?
    @State private var surfaceLayer = SpatialSurfaceLayer.all
    @StateObject private var textureEvidence = SpatialTextureEvidence()

    private var observedSpan: String {
        let points = controller.document.surfaces.filter(\.observed).flatMap(\.vertices)
        guard !points.isEmpty else { return "尚未观察到空间结构" }
        let xs = points.map(\.x), zs = points.map(\.z)
        let width = (xs.max() ?? 0) - (xs.min() ?? 0)
        let depth = (zs.max() ?? 0) - (zs.min() ?? 0)
        return String(format: "已观测跨度 %.1f × %.1f 米 · 非完整房间尺寸", width, depth)
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
                    Menu {
                        Button("切换户外", action: onOutdoor)
                        Button("物品记录", action: onLegacy)
                        Button("真机调试", action: onDebug)
                    } label: { Image(systemName: "ellipsis.circle").font(.title2) }
                    .accessibilityLabel("更多功能")
                    .accessibilityIdentifier("spatial.more")
                }
                ZStack(alignment: .bottomLeading) {
                    if controller.isScanning {
                        SpatialCameraView(controller: controller, layer: surfaceLayer, evidence: textureEvidence)
                    } else {
                        SpatialStructureView(document: controller.document, layer: surfaceLayer,
                                            imageDirectory: controller.keyframeDirectory, evidence: textureEvidence).id(controller.document.id)
                    }
                    Text(controller.isScanning ? controller.captureGuidance.message : (controller.document.surfaces.isEmpty && controller.document.poses.isEmpty ? "开始扫描建立空间" : "拖动旋转 · 双指缩放"))
                        .font(.caption).padding(10).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
                        .padding(12)
                }
                .aspectRatio(3 / 4, contentMode: .fit)
                .frame(maxHeight: 460)
                .clipShape(RoundedRectangle(cornerRadius: 20))
                .accessibilityIdentifier("spatial.structure")
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

                Button {
                    if controller.isScanning { controller.stopAndSave() } else if !controller.document.surfaces.isEmpty || !controller.document.poses.isEmpty { confirmingNew = true } else { controller.start() }
                } label: {
                    Label(controller.isScanning ? "结束并保存" : "开始扫描", systemImage: controller.isScanning ? "stop.fill" : "viewfinder")
                        .frame(maxWidth: .infinity).padding(.vertical, 8)
                }.buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("spatial.scan")
                    .disabled(controller.isSaving || controller.isFinishingAndSaving || controller.isCapturingMap)
                    #if targetEnvironment(simulator)
                    .disabled(true)
                    #endif

                if !controller.isScanning, !controller.document.poses.isEmpty {
                    Button("恢复这个空间", systemImage: "arrow.triangle.2.circlepath") { controller.restore() }
                        .buttonStyle(.bordered).disabled(controller.document.worldMap == nil || controller.isSaving || controller.isFinishingAndSaving || controller.isCapturingMap)
                        .accessibilityIdentifier("spatial.restore")
                    Text(controller.document.worldMap == nil ? "此记录没有定位地图，不能与当前相机对齐" : "回到原墙角后恢复；新观测核对通过才记录轨迹")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if controller.isCapturingMap || controller.isFinishingAndSaving { ProgressView("正在结束并保存，请稍候") }
                VStack(alignment: .leading, spacing: 8) {
                    Text("俯视图").font(.headline)
                    Picker("显示观测层", selection: $surfaceLayer) {
                        ForEach(SpatialSurfaceLayer.allCases, id: \.self) { layer in Text(layer.title).tag(layer) }
                    }.pickerStyle(.segmented).accessibilityIdentifier("spatial.layers")
                    Text("\(surfaceLayer.title) · 显示 \(controller.document.surfaces.filter { $0.observed && surfaceLayer.includes($0) }.count) / \(controller.document.surfaces.count) 个原始观测，保存与导出保留全部")
                        .font(.caption).foregroundStyle(.secondary)
                    HStack(spacing: 12) {
                        Label("垂直面候选", systemImage: "rectangle.portrait").foregroundStyle(.purple)
                        Label("水平面候选", systemImage: "rectangle").foregroundStyle(.teal)
                    }.font(.caption)
                    Text("方向不等于类别：家具也可能形成平面，尚未确认墙面或地面。")
                        .font(.caption).foregroundStyle(.secondary)
                    Text(observedSpan).font(.caption).foregroundStyle(.secondary)
                    Text("能对上照片的观测面会贴上颜色；线框是结构。家具还没有单独剪出立牌。斜视会拉长。")
                        .font(.caption).foregroundStyle(.secondary)
                    Text("已采集 \(controller.document.keyframes.count) 张关键帧").font(.caption).foregroundStyle(.secondary)
                    SpatialTopView(document: controller.document, layer: surfaceLayer, livePose: controller.livePose, isLive: controller.isScanning).frame(height: 150)
                    Text("只显示已观察结构。空白处未扫描，不代表可通行。")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(16).background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 20))

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
                        .disabled(controller.isSaving || controller.isFinishingAndSaving || controller.isCapturingMap || controller.isScanning || (controller.document.surfaces.isEmpty && controller.document.poses.isEmpty))
                        .accessibilityIdentifier("spatial.save")
                    Spacer()
                    Button("历史", systemImage: "clock") { showingHistory = true }
                        .accessibilityIdentifier("spatial.history")
                        .disabled(controller.isSaving || controller.isFinishingAndSaving || controller.isCapturingMap)
                    Spacer()
                    Button("清除本次", role: .destructive) { confirmingClear = true }
                        .accessibilityIdentifier("spatial.clear")
                        .disabled(controller.isSaving || controller.isFinishingAndSaving || controller.isCapturingMap)
                }.buttonStyle(.bordered)
                if !controller.isScanning && !controller.document.surfaces.isEmpty {
                    Menu("检查与修正结构") {
                        ForEach(controller.document.surfaces, id: \.id) { surface in
                            Button("\(SpatialSurfaceLayer.name(surface)) · 观测跨度 \(String(format: "%.2f", SpatialScanGeometry.observedBoundarySpan(surface) ?? 0)) 米") { editingSurface = surface.id }
                        }
                    }.accessibilityIdentifier("spatial.edit")
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
                    .toolbar { Button("完成") { showingHistory = false } }
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
        .confirmationDialog("开始新的扫描？当前结果将被替换，请先保存需要保留的扫描。", isPresented: $confirmingNew, titleVisibility: .visible) {
            Button("开始新的扫描", role: .destructive) { controller.start() }
        }
        .confirmationDialog("清除本次扫描？请先保存需要保留的结果。已保存的记录会保留。", isPresented: $confirmingClear, titleVisibility: .visible) {
            Button("清除本次", role: .destructive) { controller.clear() }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background || (phase != .active && controller.isScanning) { controller.stop() }
        }
        .onDisappear { controller.stop() }
    }
}

private struct SpatialCameraView: UIViewRepresentable {
    let controller: SpatialScanController
    let layer: SpatialSurfaceLayer
    let evidence: SpatialTextureEvidence
    func makeCoordinator() -> SpatialSceneCache { SpatialSceneCache() }
    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView()
        view.session = controller.session
        view.automaticallyUpdatesLighting = false
        view.scene = SCNScene()
        return view
    }
    func updateUIView(_ view: ARSCNView, context: Context) {
        let scene = view.scene
        context.coordinator.bindLive(controller.livePose)
        context.coordinator.populate(scene, document: controller.document, layer: layer,
                                     imageDirectory: controller.keyframeDirectory, liveOverlay: true, evidence: evidence)
    }
}

private struct SpatialStructureView: UIViewRepresentable {
    let document: SpatialScanDocument
    let layer: SpatialSurfaceLayer
    let imageDirectory: URL
    let evidence: SpatialTextureEvidence
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
                                     imageDirectory: imageDirectory, liveOverlay: false, evidence: evidence)
        if scene.rootNode.childNode(withName: "camera", recursively: false) == nil {
            let points = document.surfaces.flatMap(\.vertices) + document.poses.map(\.position)
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
    func populate(_ scene: SCNScene, document: SpatialScanDocument, layer: SpatialSurfaceLayer,
                  imageDirectory: URL, liveOverlay: Bool, evidence: SpatialTextureEvidence) {
        let nextKey = document.id.uuidString + ":" + document.keyframes.map(\.imageFile).joined(separator: ",")
        let sameGeometry = surfaces.count==document.surfaces.count && zip(surfaces,document.surfaces).allSatisfy { a,b in
            a.id==b.id && a.kind==b.kind && a.vertices==b.vertices && a.observed==b.observed && a.epoch==b.epoch
        }
        if !sameGeometry || activeLayer != layer || textureKey != nextKey {
            activeLayer = layer
            surfaces = document.surfaces
            textureKey = nextKey
            textureGeneration = UUID()
            let generation=textureGeneration
            var requests:[(SpatialSurface,SpatialProjectiveTexturing.Selection,SCNGeometry)] = []
            var rows:[SpatialTextureEvidence.Row] = []
            scene.rootNode.childNode(withName: "surfaces", recursively: false)?.removeFromParentNode()
            let root = SCNNode(); root.name = "surfaces"
            var edgesByKind: [String: [SCNVector3]] = [:]
            for surface in surfaces where layer.includes(surface) && surface.observed && surface.vertices.count >= 3 && surface.vertices.allSatisfy(\.isFinite) {
                let vertices = surface.vertices.map { SCNVector3($0.x, $0.y, $0.z) }
                var indices = [Int32]()
                for index in 1..<(vertices.count - 1) { indices += [0, Int32(index), Int32(index + 1)] }
                let selection=SpatialProjectiveTexturing.selection(surface:surface,keyframes:document.keyframes)
                let fill=SCNMaterial();fill.isDoubleSided=true;fill.lightingModel = .constant
                fill.transparency=1;fill.blendMode = .alpha
                fill.diffuse.contents=SpatialSurfaceLayer.uiColor(surface).withAlphaComponent(surface.kind=="vertical" ? 0.14:0.08)
                let geometry=SCNGeometry(sources:[SCNGeometrySource(vertices:vertices)],elements:[SCNGeometryElement(indices:indices,primitiveType:.triangles)])
                requests.append((surface,selection,geometry))
                rows.append(SpatialTextureEvidence.Row(id:surface.id,status:selection.mapping==nil ? "暂无覆盖照片，保留未知底色":"正在读取照片，保留未知底色",imageFile:selection.mapping?.imageFile,candidates:selection.candidates))
                geometry.materials = [fill]; root.addChildNode(SCNNode(geometry: geometry))
                for index in vertices.indices { edgesByKind[surface.kind, default: []] += [vertices[index], vertices[(index + 1) % vertices.count]] }
            }
            for (kind, edges) in edgesByKind {
                let node = lines(edges, color: SpatialSurfaceLayer.uiColor(kind))
                node.renderingOrder = 10
                root.addChildNode(node)
            }
            scene.rootNode.addChildNode(root)
            for surface in document.surfaces where !surface.observed || !layer.includes(surface) || surface.vertices.count<3 || !surface.vertices.allSatisfy(\.isFinite) {
                let state = !surface.observed ? "未确认观测，原记录保留" : !layer.includes(surface) ? "当前图层未显示，原观测保留" : "几何数据无效，原记录与失败保留"
                rows.append(SpatialTextureEvidence.Row(id:surface.id,status:state,imageFile:nil,candidates:[]))
            }
            let scope=textures.beginScope(names:Set(requests.compactMap {$0.1.mapping?.imageFile}),directory:imageDirectory)
            evidence.reset(generation:generation,rows:rows,stats:self.textures.snapshot())
            for (surface,selection,geometry) in requests {
                guard let mapping=selection.mapping else {continue}
                textures.request(name:mapping.imageFile,directory:imageDirectory,scope:scope) { [weak self,weak evidence] result in
                    guard let self,let evidence else {return}
                    Task { @MainActor [self,evidence] in
                        guard self.textureGeneration==generation else {return}
                        switch result {
                        case let .ready(texture,_):
                            geometry.materials=[SpatialProjectiveTexturing.material(mapping:mapping,texture:texture,kind:surface.kind,liveOverlay:liveOverlay)]
                            evidence.update(generation:generation,id:surface.id,status:"已投影；照片外区域与遮挡仍未知",stats:self.textures.snapshot())
                        case let .unknown(reason):
                            let message=reason=="decoded_texture_budget" ? "纹理容量已满，保留未知底色与边界":"照片读取未完成或失败，保留未知底色与边界（"+reason+"）"
                            evidence.update(generation:generation,id:surface.id,status:message,stats:self.textures.snapshot())
                        }
                    }
                }
            }
        }
        currentScene = scene
        lastHistoryPose = document.poses.last
        let change = trajectoryCache.consume(document)
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

private struct SpatialTopProjection {
    let scale: CGFloat, offset: CGPoint
    init(document: SpatialScanDocument, size: CGSize) {
        let points = document.surfaces.filter(\.observed).flatMap(\.vertices) + document.poses.map(\.position)
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

/// This view observes only the slow persisted document. Live invalidations stay
/// inside the small UIKit arrow/tail view below, not in the static Canvas.
private struct SpatialTopView: View {
    let document: SpatialScanDocument
    let layer: SpatialSurfaceLayer
    let livePose: SpatialLivePoseState
    let isLive: Bool
    @StateObject private var cache = SpatialTopPathCache()
    var body: some View {
        GeometryReader { geometry in
            let projection = SpatialTopProjection(document: document, size: geometry.size)
            ZStack {
                Canvas { context, size in
                    for surface in document.surfaces where surface.observed && layer.includes(surface) && surface.vertices.count >= 3 {
                        var path = Path(); path.move(to: projection.map(surface.vertices[0]))
                        for p in surface.vertices.dropFirst() { path.addLine(to: projection.map(p)) }; path.closeSubpath()
                        let color = Color(uiColor: SpatialSurfaceLayer.uiColor(surface))
                        context.fill(path, with: .color(color.opacity(surface.kind == "vertical" ? 0.10 : 0.04)))
                        context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: surface.kind == "vertical" ? 2 : 1, dash: surface.kind == "horizontal" ? [4,3] : []))
                    }
                    var world = context
                    world.translateBy(x: projection.offset.x, y: projection.offset.y)
                    world.scaleBy(x: projection.scale, y: projection.scale)
                    world.stroke(Path(cache.update(document)), with: .color(.orange), lineWidth: 3/max(projection.scale,0.001))
                    if !isLive, let pose = document.poses.last {
                        let p = projection.map(pose.position)
                        context.fill(Path(ellipseIn: CGRect(x:p.x-4,y:p.y-4,width:8,height:8)), with: .color(.orange))
                    }
                    if document.poses.isEmpty && document.surfaces.isEmpty {
                        context.draw(Text("移动后，轨迹和已观察结构会出现在这里").font(.caption).foregroundColor(.secondary), at: CGPoint(x:size.width/2,y:size.height/2))
                    }
                }
                if isLive { SpatialLiveTopLayer(livePose: livePose, projection: projection, historyPose: document.poses.last) }
            }
        }
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
