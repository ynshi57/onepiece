import Foundation

/// A bounded two-view experiment. ROI membership does not establish object membership.
actor SpatialObjectSupportAnalyzer {
    private let correspondence = SpatialLocalCorrespondence()

    func analyze(source: SpatialKeyframe, observation: SpatialObjectObservation,
                 frames: [SpatialKeyframe], directory: URL) async -> [SpatialObjectSupportAttempt] {
        guard let attemptID = observation.attemptID else { return [] }
        let began = ProcessInfo.processInfo.systemUptime
        let target = frames.filter { other in
            let a = source.pose.position, b = other.pose.position
            let distance = sqrt(pow(a.x-b.x,2)+pow(a.y-b.y,2)+pow(a.z-b.z,2))
            return other.id != source.id && other.pose.epoch == source.pose.epoch
                && other.width == source.width && other.height == source.height
                && abs(other.time-source.time) <= 4 && distance >= 0.08 && distance <= 1.5
        }.min { abs($0.time-source.time) < abs($1.time-source.time) }
        var gridCount = 0
        var matches: [SpatialImageMatch] = []
        var supports: [SpatialSurfaceSupport] = []
        var status = SpatialObjectSupportAttempt.Status.unavailable
        var reason = "没有同一坐标系、具有视差的邻近照片；物品位置未知"
        if let target {
            do {
                matches = try await correspondence.matches(source: source, target: target, directory: directory)
                gridCount = 150
                supports = matches.compactMap { SpatialObservationGeometry.triangulate($0, source: source, target: target) }
                reason = "双向对应或几何核对未通过；物品位置未知"
            } catch {
                status = .failed
                reason = "本地跨照片核对失败：\(error.localizedDescription)"
            }
        }
        let elapsed = ProcessInfo.processInfo.systemUptime-began
        return observation.detections.enumerated().map { index, detection in
            let box = detection.boundingBox
            let contained: ([Float]) -> Bool = { p in
                p.count == 2 && p[0] >= box[0] && p[0] <= box[2] && p[1] >= box[1] && p[1] <= box[3]
            }
            let samples = supports.filter { contained($0.sourcePixel) }
            return SpatialObjectSupportAttempt(sourceFrameID: source.id, sourceAttemptID: attemptID,
                detectionIndex: index, targetFrameID: target?.id, epoch: source.pose.epoch,
                status: samples.isEmpty ? status : .measured,
                reason: samples.isEmpty ? reason : "区域内表面点，可能来自背景；物品位置仍未确认",
                elapsedSeconds: elapsed, pairGridCount: gridCount, pairMatchCount: matches.count, sampledMatchCount: matches.filter { contained($0.sourcePixel) }.count,
                supports: samples)
        }
    }
}
