import SwiftUI
import UIKit

struct SpatialObjectObservationView: View {
    @ObservedObject var controller: SpatialScanController
    @State private var expandedFrames: Set<String> = []
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("照片中的物品").font(.headline)
            Text("在此 iPhone 本地识别，结果随扫描保存在本机。类别与框仍可能出错；位置、同一物品和完整清单尚未确认。")
                .font(.caption).foregroundStyle(.secondary)
            Button("识别已采集照片", systemImage: "viewfinder") { controller.analyzeObjects() }
                .disabled(controller.isArchivingObjects || controller.isScanning || controller.isAnalyzingObjects || controller.isLocatingObjects || controller.isSaving || controller.isCapturingMap || controller.isFinishingAndSaving || controller.document.keyframes.isEmpty)
                .accessibilityIdentifier("spatial.objects.analyze")
            Button("核对空间位置", systemImage: "point.3.connected.trianglepath.dotted") { controller.locateObjectSupports() }
                .disabled(controller.isArchivingObjects || controller.isScanning || controller.isAnalyzingObjects || controller.isLocatingObjects || controller.isSaving || controller.isCapturingMap || controller.isFinishingAndSaving || !(controller.document.objectObservations ?? []).contains(where: { $0.attemptID != nil && !$0.detections.isEmpty }))
                .accessibilityIdentifier("spatial.objects.supports")
            if controller.isLocatingObjects {
                ProgressView("已核对 \(controller.objectLocationCompleted) 张照片；正在检查照片间的表面证据")
            }
            Text("核对得到的是区域内表面点，可能来自背景；物品位置仍未确认。")
                .font(.caption).foregroundStyle(.secondary)
            if controller.isArchivingObjects {
                ProgressView("正在整理已采集照片中的物品")
            }
            if controller.isAnalyzingObjects {
                ProgressView("本次已识别 \(controller.objectAnalysisCompleted) / \(controller.document.keyframes.count) 张；其余仍保留上次记录")
            }
            Text("检测类别有限，不包含纸箱和画架；没有检测框不代表不存在。")
                .font(.caption).foregroundStyle(.orange)
            if let rows = controller.document.objectObservations {
                Text("已有 \(rows.count) / \(controller.document.keyframes.count) 张观察，失败 \(rows.filter { $0.status == .failed }.count) 张，部分失败 \(rows.filter { $0.status == .partial }.count) 张")
                    .font(.caption)
                ForEach(rows, id: \.frameID) { row in
                    if let frame = controller.document.keyframes.first(where: { $0.id == row.frameID }) {
                        DisclosureGroup(observationTitle(row), isExpanded: Binding(get: { expandedFrames.contains(row.frameID) }, set: { expanded in
                            if expanded { expandedFrames.insert(row.frameID) } else { expandedFrames.remove(row.frameID) }
                        })) {
                            if expandedFrames.contains(row.frameID) {
                            if let failure = row.failure {
                                Text("有候选未能处理，可重新识别；已保留失败记录。").font(.caption).foregroundStyle(.orange)
                                DisclosureGroup("失败详情") {
                                    Text(failure).font(.caption)
                                    ForEach(Array((row.detectionFailures ?? []).enumerated()), id: \.offset) { _, value in Text(value).font(.caption) }
                                }
                            }
                            if row.detections.contains(where: \.isClipped) {
                                Text("越出照片的原框已保留，显示照片内的可见部分。").font(.caption).foregroundStyle(.secondary)
                            }
                            SpatialObjectPhoto(frame: frame, row: row, directory: controller.keyframeDirectory, supports: supports(for: row))
                            if !supports(for: row).isEmpty {
                                Text("蓝点是核对过的区域表面，可能是背景；不是物品中心。").font(.caption).foregroundStyle(.secondary)
                            }
                            supportRecords(for: row)
                            Text(row.detections.map { SpatialObjectLabels.title($0.label) }.joined(separator: "、")).font(.caption)
                            Text("物品位置未知；拍摄位置不是物品位置。类别与分数未经核实。")
                                .font(.caption).foregroundStyle(.secondary)
                            if let previous = row.previousAttempts {
                                DisclosureGroup("上次识别记录") {
                                    ForEach(Array(history(previous).enumerated()), id: \.offset) { _, old in
                                        VStack(alignment: .leading, spacing: 6) {
                                            Text(observationTitle(old)).font(.caption)
                                            Text(old.failure ?? old.detections.map { SpatialObjectLabels.title($0.label) }.joined(separator: "、")).font(.caption)
                                            ForEach(Array((old.detectionFailures ?? []).enumerated()), id: \.offset) { _, failure in Text(failure).font(.caption) }
                                            supportRecords(for: old)
                                        }
                                    }
                                }
                            }
                            }
                        }
                    }
                }
            }
        }.padding(16).background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 20))
    }
    private func supports(for row: SpatialObjectObservation) -> [SpatialSurfaceSupport] {
        guard let attemptID = row.attemptID else { return [] }
        return (controller.document.objectSupportAttempts ?? []).filter {
            $0.sourceFrameID == row.frameID && $0.sourceAttemptID == attemptID
        }.flatMap(\.supports)
    }

    private func history(_ observations: [SpatialObjectObservation]) -> [SpatialObjectObservation] {
        var rows: [SpatialObjectObservation] = []
        var pending = observations
        while !pending.isEmpty {
            let row = pending.removeFirst()
            rows.append(row)
            pending.insert(contentsOf: row.previousAttempts ?? [], at: 0)
        }
        return rows
    }

    @ViewBuilder private func supportRecords(for row: SpatialObjectObservation) -> some View {
        if let attemptID = row.attemptID {
            let attempts = (controller.document.objectSupportAttempts ?? []).filter {
                $0.sourceFrameID == row.frameID && $0.sourceAttemptID == attemptID
            }
            if attempts.isEmpty {
                Text("本次识别尚无空间核对记录").font(.caption).foregroundStyle(.secondary)
            } else {
                DisclosureGroup("空间核对记录（\(attempts.count)次）") {
                    ForEach(attempts) { attempt in
                        VStack(alignment: .leading, spacing: 3) {
                            let name = row.detections.indices.contains(attempt.detectionIndex) ? SpatialObjectLabels.title(row.detections[attempt.detectionIndex].label) : "候选"
                            Text("\(name) · \(supportTitle(attempt))").font(.caption)
                            Text(attempt.reason).font(.caption).foregroundStyle(attempt.status == .measured ? Color.secondary : Color.orange)
                            Text("\(attempt.supports.count)个表面点 · 区域内\(attempt.sampledMatchCount)个有效匹配 · \(attempt.elapsedSeconds, specifier: "%.2f")秒").font(.caption2).foregroundStyle(.secondary)
                            Text("整对照片：\(attempt.pairMatchCount) / \(attempt.pairGridCount)个格点通过双向核对；未通过者不参与测量。")
                                .font(.caption2).foregroundStyle(.secondary)
                            if let target = attempt.targetFrameID {
                                Text("对照照片 \(target.prefix(8))").font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                    }
                    Text("区域内表面点可能来自背景；物品位置仍未确认。").font(.caption).foregroundStyle(.secondary)
                }
            }
        } else {
            Text("旧识别记录没有核对标识；请重新识别后核对。").font(.caption).foregroundStyle(.secondary)
        }
    }

    private func supportTitle(_ attempt: SpatialObjectSupportAttempt) -> String {
        switch attempt.status {
        case .measured: return "获得表面点"
        case .unavailable: return "暂无法核对"
        case .failed: return "核对失败"
        }
    }

    private func observationTitle(_ row: SpatialObjectObservation) -> String {
        if row.status == .failed { return "照片识别失败" }
        let suffix = row.status == .partial ? " · 部分失败" : ""
        return "\(row.detections.count) 个物品候选 · " + String(format: "%.2f 秒", row.elapsedSeconds) + suffix
    }

}

/// Support pixels use native pixel-index / image-size coordinates, unlike box edges.
nonisolated enum SpatialObjectSupportPresentation {
    static func displayPixel(_ pixel: [Float], width: Int, height: Int, turns: Int) -> [Float]? {
        guard pixel.count == 2, pixel.allSatisfy(\.isFinite), width > 0, height > 0 else { return nil }
        let x = pixel[0] + 0.5 / Float(width), y = pixel[1] + 0.5 / Float(height)
        guard (0...1).contains(x), (0...1).contains(y) else { return nil }
        switch (turns % 4 + 4) % 4 {
        case 1: return [y, 1-x]
        case 2: return [1-x, 1-y]
        case 3: return [1-y, x]
        default: return [x, y]
        }
    }
}

private struct SpatialObjectPhoto: View {
    let frame: SpatialKeyframe
    let row: SpatialObjectObservation
    let directory: URL
    var supports: [SpatialSurfaceSupport] = []
    var body: some View {
        if let data = try? Data(contentsOf: directory.appendingPathComponent(frame.imageFile)), let raw = UIImage(data: data), let cg = raw.cgImage {
            let turns = SpatialObjectCoordinates.turns(frame)
            let orientation: UIImage.Orientation = [.up,.left,.down,.right][turns]
            let image = UIImage(cgImage: cg, scale: 1, orientation: orientation)
            Image(uiImage: image).resizable().scaledToFit().overlay {
                GeometryReader { geometry in
                    ForEach(Array(row.detections.enumerated()), id: \.offset) { _, detection in
                        let b = SpatialObjectCoordinates.nativeBox(detection.boundingBox, turns: (4-turns)%4)
                        Rectangle().stroke(.orange, lineWidth: 2)
                            .frame(width: CGFloat(b[2]-b[0])*geometry.size.width, height: CGFloat(b[3]-b[1])*geometry.size.height)
                            .position(x: CGFloat((b[0]+b[2])/2)*geometry.size.width, y: CGFloat((b[1]+b[3])/2)*geometry.size.height)
                    }
                    ForEach(Array(supports.enumerated()), id: \.offset) { _, support in
                        if let pixel = SpatialObjectSupportPresentation.displayPixel(support.sourcePixel, width: frame.width, height: frame.height, turns: turns) {
                            Circle().fill(.blue).overlay(Circle().stroke(.white, lineWidth: 0.7))
                                .frame(width: 5, height: 5)
                                .position(x: CGFloat(pixel[0]) * geometry.size.width, y: CGFloat(pixel[1]) * geometry.size.height)
                        }
                    }
                }
            }.accessibilityLabel(supports.isEmpty ? "原照片与未经核实的物品候选框" : "原照片、候选框与蓝色区域表面点；表面点可能来自背景")
        } else { Text("照片无法读取，请检查本地扫描文件").foregroundStyle(.orange) }
    }
}

enum SpatialObjectLabels {
    static func title(_ label: String) -> String {
        let english = "person,bicycle,car,motorcycle,airplane,bus,train,truck,boat,traffic light,fire hydrant,stop sign,parking meter,bench,bird,cat,dog,horse,sheep,cow,elephant,bear,zebra,giraffe,backpack,umbrella,handbag,tie,suitcase,frisbee,skis,snowboard,sports ball,kite,baseball bat,baseball glove,skateboard,surfboard,tennis racket,bottle,wine glass,cup,fork,knife,spoon,bowl,banana,apple,sandwich,orange,broccoli,carrot,hot dog,pizza,donut,cake,chair,couch,potted plant,bed,dining table,toilet,tv,laptop,mouse,remote,keyboard,cell phone,microwave,oven,toaster,sink,refrigerator,book,clock,vase,scissors,teddy bear,hair drier,toothbrush".components(separatedBy: ",")
        let chinese = "人,自行车,汽车,摩托车,飞机,公交车,火车,卡车,船,交通灯,消防栓,停车标志,停车计时器,长椅,鸟,猫,狗,马,羊,牛,大象,熊,斑马,长颈鹿,背包,雨伞,手提包,领带,行李箱,飞盘,滑雪板,单板滑雪板,球,风筝,棒球棒,棒球手套,滑板,冲浪板,网球拍,瓶子,酒杯,杯子,叉子,刀,勺子,碗,香蕉,苹果,三明治,橙子,西兰花,胡萝卜,热狗,披萨,甜甜圈,蛋糕,椅子,沙发,盆栽,床,餐桌,马桶,电视或屏幕,笔记本电脑,鼠标,遥控器,键盘,手机,微波炉,烤箱,烤面包机,水槽,冰箱,书,时钟,花瓶,剪刀,玩具熊,吹风机,牙刷".components(separatedBy: ",")
        guard let index = english.firstIndex(of: label), index < chinese.count else { return label }
        return chinese[index]
    }
}

#if DEBUG
/// Render the actual photo component on the physical device after store reload.
/// Only the explicit developer selfcheck invokes this; ordinary saves produce no image.
@MainActor enum SpatialObjectVerificationRenderer {
    static func save(frame: SpatialKeyframe, row: SpatialObjectObservation, directory: URL, output: URL) throws {
        let content = VStack(alignment: .leading, spacing: 8) {
            Text("iPhone 本地观察 · 未核实候选").font(.headline)
            SpatialObjectPhoto(frame: frame, row: row, directory: directory).frame(width: 360, height: 480)
            Text(row.detections.map { SpatialObjectLabels.title($0.label) }.joined(separator: "、")).font(.caption)
            Text("原图与实际候选框；位置未知，分数不代表准确率。").font(.caption)
        }.padding(12).frame(width: 384).background(Color.white).environment(\.colorScheme, .light)
        let renderer = ImageRenderer(content: content)
        renderer.scale = 2
        guard let image = renderer.uiImage, let data = image.jpegData(compressionQuality: 0.9) else {
            throw NSError(domain: "SpatialObjectVerification", code: 1)
        }
        try data.write(to: output, options: [.atomic, .completeFileProtection])
    }
}
#endif
