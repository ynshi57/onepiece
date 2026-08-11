import Foundation
import CoreGraphics

/// User-enabled diagnostic upload session state. This class does not persist
/// images on the iPhone; frames are uploaded to the currently connected Mac
/// backend. It only builds metadata JSON and tracks upload counters for UI.
final class DiagnosticCaptureRecorder {
    private(set) var sessionID = ""
    private var queuedCount = 0
    private var uploadedCount = 0
    private var failedCount = 0

    var isRecording: Bool {
        !sessionID.isEmpty
    }

    func start() -> String {
        if isRecording {
            return statusText(prefix: "诊断上传中")
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withDashSeparatorInDate, .withColonSeparatorInTime]
        let safeTimestamp = formatter.string(from: Date())
            .replacingOccurrences(of: ":", with: "-")
        sessionID = "ios-\(safeTimestamp)-\(UUID().uuidString.prefix(8))"
        queuedCount = 0
        uploadedCount = 0
        failedCount = 0
        return statusText(prefix: "诊断上传已开启")
    }

    func stop() -> String {
        guard isRecording else {
            return "诊断上传已关闭"
        }
        let text = statusText(prefix: "诊断上传已停止")
        sessionID = ""
        return text
    }

    func markQueued() -> String {
        queuedCount += 1
        return statusText(prefix: "诊断上传中")
    }

    func recordUpload(success: Bool) -> String {
        if success {
            uploadedCount += 1
        } else {
            failedCount += 1
        }
        return statusText(prefix: "诊断上传中")
    }

    func metadataJSON(
        frameName: String,
        mode: AssistanceMode,
        question: String,
        localVisionSignal: LocalVisionSignal,
        encodeMs: Double,
        event: String,
        reason: String
    ) -> String? {
        guard isRecording else {
            return nil
        }
        let record = Self.buildRecord(
            diagnosticSessionID: sessionID,
            frameName: frameName,
            mode: mode,
            question: question,
            localVisionSignal: localVisionSignal,
            encodeMs: encodeMs,
            event: event,
            reason: reason
        )
        guard let data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]) else {
            return nil
        }
        return String(data: data, encoding: .utf8)
    }

    private func statusText(prefix: String) -> String {
        guard isRecording else {
            return "诊断上传已关闭"
        }
        return "\(prefix)：已排队 \(queuedCount) 帧，已上传 \(uploadedCount) 帧，失败 \(failedCount) 帧"
    }

    private static func buildRecord(
        diagnosticSessionID: String,
        frameName: String,
        mode: AssistanceMode,
        question: String,
        localVisionSignal: LocalVisionSignal,
        encodeMs: Double,
        event: String,
        reason: String
    ) -> [String: Any] {
        [
            "diagnostic_session_id": diagnosticSessionID,
            "timestamp": ISO8601DateFormatter().string(from: Date()),
            "frame": frameName,
            "mode": mode.rawValue,
            "question": question,
            "event": event,
            "reason": reason,
            "encode_ms": encodeMs,
            "local_vision": [
                "has_human": localVisionSignal.hasHuman,
                "human_direction": localVisionSignal.humanDirection.rawValue,
                "brightness": localVisionSignal.brightness,
                "scene_change_score": localVisionSignal.sceneChangeScore,
                "is_too_dark": localVisionSignal.isTooDark,
                "is_likely_covered": localVisionSignal.isLikelyCovered,
                "analyzer_failed": localVisionSignal.analyzerFailed,
                "backend_context": localVisionSignal.backendContext,
            ],
            "perception": perceptionPayload(localVisionSignal.perception),
        ]
    }

    private static func perceptionPayload(_ signal: LocalPerceptionSignal) -> [String: Any] {
        [
            "model_status": signal.modelStatus.rawValue,
            "objects": signal.objects.map(objectPayload),
            "road_cues": [
                "crosswalk": signal.roadCues.crosswalk.rawValue,
                "lane_marking": signal.roadCues.laneMarking.rawValue,
                "curb": signal.roadCues.curb.rawValue,
            ],
            "depth_cues": [
                "near_drop": signal.depthCues.nearDrop.rawValue,
                "nearest_obstacle_direction": signal.depthCues.nearestObstacleDirection.rawValue,
            ],
            "backend_context": signal.backendContext,
        ]
    }

    private static func objectPayload(_ object: LocalPerceptionObject) -> [String: Any] {
        var payload: [String: Any] = [
            "kind": object.kind.rawValue,
            "label": object.kind.chineseLabel,
            "direction": object.direction.rawValue,
            "confidence": object.confidence,
        ]
        if let box = object.normalizedBoundingBox {
            payload["bbox"] = [
                "x": box.minX,
                "y": box.minY,
                "width": box.width,
                "height": box.height,
            ]
        }
        return payload
    }
}
