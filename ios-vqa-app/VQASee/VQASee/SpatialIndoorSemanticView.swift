import SwiftUI
import UIKit

struct SpatialIndoorSemanticView: View {
    @ObservedObject var controller: SpatialScanController
    @State private var expanded: Set<UUID> = []
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button("核对墙面与地面", systemImage: "square.3.layers.3d") { controller.analyzeIndoorSemantics() }
                .disabled(controller.isScanning || controller.isAnalyzingIndoorSemantics || controller.isAnalyzingObjects || controller.isLocatingObjects || controller.isArchivingObjects || controller.isSaving || controller.isCapturingMap || controller.isFinishingAndSaving || controller.document.keyframes.isEmpty)
                .accessibilityIdentifier("spatial.semantics.analyze")
            if controller.isAnalyzingIndoorSemantics {
                ProgressView("已核对 \(controller.indoorSemanticCompleted) / \(controller.document.keyframes.count) 张照片")
            }
            Text("紫色为墙面证据，青色为地面证据；灰色尚未确认，橙色存在冲突。家具遮挡不会删除原始面。")
                .font(.caption).foregroundStyle(.secondary)
            if let rows = controller.document.indoorSemanticObservations, !rows.isEmpty {
                DisclosureGroup("照片证据 · \(rows.count)张，失败\(rows.filter { $0.status == .failed }.count)张") {
                    ForEach(rows, id: \.attemptID) { row in
                        DisclosureGroup(row.status == .failed ? "核对失败" : String(format: "照片核对 · %.2f秒",row.elapsedSeconds), isExpanded: Binding(get: { expanded.contains(row.attemptID) }, set: { if $0 { expanded.insert(row.attemptID) } else { expanded.remove(row.attemptID) } })) {
                            if expanded.contains(row.attemptID) {
                                if let failure = row.failure { Text(failure).font(.caption).foregroundStyle(.orange) }
                                if let frame = controller.document.keyframes.first(where: { $0.id == row.frameID }) {
                                    SpatialIndoorSemanticPhoto(frame:frame,row:row,directory:controller.keyframeDirectory)
                                }
                                Text("照片类别可能出错；分割不提供深度。多张一致且几何相符后，才用于地图类别。")
                                    .font(.caption).foregroundStyle(.secondary)
                                ForEach(Array(row.records.filter { $0.failure != nil }.enumerated()), id: \.offset) { _, record in
                                    Text("观测面未能核对：\(record.failure ?? "未知原因")").font(.caption).foregroundStyle(.orange)
                                }
                                if let previous = row.previousAttempts {
                                    DisclosureGroup("历史核对记录") { history(previous) }
                                }
                            }
                        }
                    }
                }
            }
        }.padding(12).background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius:16))
    }
    private func history(_ rows: [SpatialIndoorSemanticObservation]) -> some View {
        var pending = rows, all: [SpatialIndoorSemanticObservation] = []
        while !pending.isEmpty {
            let row = pending.removeFirst(); all.append(row)
            pending.insert(contentsOf:row.previousAttempts ?? [],at:0)
        }
        return ForEach(all, id: \.attemptID) { row in
            VStack(alignment:.leading) {
                Text(row.failure ?? String(format:"历史核对 · %.2f秒",row.elapsedSeconds)).font(.caption)
                if let frame = controller.document.keyframes.first(where: { $0.id == row.frameID }) {
                    SpatialIndoorSemanticPhoto(frame:frame,row:row,directory:controller.keyframeDirectory)
                }
            }
        }
    }
}

private struct SpatialIndoorSemanticPhoto: View {
    let frame: SpatialKeyframe
    let row: SpatialIndoorSemanticObservation
    let directory: URL
    var body: some View {
        if let data = try? Data(contentsOf:directory.appendingPathComponent(frame.imageFile)), let raw = UIImage(data:data)?.cgImage {
            let orientation: UIImage.Orientation = [.up,.left,.down,.right][SpatialObjectCoordinates.turns(frame)]
            Image(uiImage:UIImage(cgImage:raw,scale:1,orientation:orientation)).resizable().scaledToFit().overlay {
                if let mask = maskImage() {
                    Image(uiImage:UIImage(cgImage:mask,scale:1,orientation:orientation)).resizable().interpolation(.none)
                }
            }.accessibilityLabel("本地原照片与墙地面类别候选；紫色墙面，青色地面，不代表三维深度")
        } else { Text("照片无法读取").font(.caption).foregroundStyle(.orange) }
    }
    private func maskImage() -> CGImage? {
        guard let mask = row.mask, mask.width > 0, mask.height > 0,
              mask.labels.count == mask.width*mask.height, mask.scores.count == mask.labels.count else { return nil }
        var rgba = [UInt8](repeating:0,count:mask.labels.count*4)
        for i in mask.labels.indices {
            let label = mask.labels[i]
            guard label == mask.taxonomy.wallLabelID || label == mask.taxonomy.floorLabelID else { continue }
            let color: [UInt8] = label == mask.taxonomy.wallLabelID ? [160,70,220] : [0,170,160]
            rgba[i*4] = color[0]; rgba[i*4+1] = color[1]; rgba[i*4+2] = color[2]
            rgba[i*4+3] = mask.scores[i] >= SpatialSurfaceSemanticEvidence.minimumScore ? 110 : 45
        }
        guard let provider = CGDataProvider(data:Data(rgba) as CFData) else { return nil }
        return CGImage(width:mask.width,height:mask.height,bitsPerComponent:8,bitsPerPixel:32,bytesPerRow:mask.width*4,space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGBitmapInfo(rawValue:CGImageAlphaInfo.last.rawValue),provider:provider,decode:nil,shouldInterpolate:false,intent:.defaultIntent)
    }
}
