import Foundation
import UIKit

/// A frame's evidence, independently of whether temporal fusion publishes it.
/// "raw" means pre-fusion output, NOT untouched model logits/detection labels.
enum FieldDiagnosticTelemetry {
    static func metadata(
        raw: LocalVisionSignal,
        fused: LocalVisionSignal,
        delivery: LocalPerceptionDelivery,
        disposition: String,
        publishedAt: Double,
        location: [String: Any]?
    ) -> Data? {
        var timings: [String: Any] = [:]
        if let data = try? JSONEncoder().encode(delivery.timings),
           let encoded = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            timings = encoded
        }
        timings["diagnosticEncodeMs"] = delivery.diagnosticEncodeMs
        var payload: [String: Any] = [
            "schema_version": 1,
            "frame_id": delivery.frameID,
            "wall_time_iso8601": ISO8601DateFormatter().string(from: Date()),
            "timestamp_scope": "capture_callback_to_view_model_publish_not_sensor_to_display",
            "callback_entered_at_monotonic": delivery.capturedAt,
            "processing_completed_at_monotonic": delivery.completedAt,
            "published_at_monotonic": publishedAt,
            "callback_to_completion_ms": delivery.resultAgeMs,
            "callback_to_publish_ms": (publishedAt - delivery.capturedAt) * 1000,
            "completion_to_publish_ms": (publishedAt - delivery.completedAt) * 1000,
            "replaced_frame_count": delivery.replacedFrameCount,
            "disposition": disposition,
            "road_status": delivery.roadStatus,
            "timings_ms": timings,
            "raw": signal(raw),
            "processed": signal(fused),
            "raw_scope": "pre_temporal_fusion_after_detector_postprocessing",
            "raw_detector_labels_available": false,
            "raw_logits_recorded": false,
            "diagnostic_jpeg_available": delivery.diagnosticJPEG != nil,
            "image_orientation": "right",
            "grid_and_lane_origin": "top_left",
            "objects_and_guidance_origin": "bottom_left",
            "thermal_state": thermalState,
            "build": buildInfo
        ]
        if let start = delivery.processingStartedAt {
            payload["processing_started_at_monotonic"] = start
            payload["queue_wait_ms"] = (start - delivery.capturedAt) * 1000
        }
        if let reason = delivery.roadReason { payload["road_reason"] = reason }
        if let version = delivery.configVersion { payload["config_version"] = version }
        if let width = delivery.imageWidth { payload["pixel_buffer_width"] = width }
        if let height = delivery.imageHeight { payload["pixel_buffer_height"] = height }
        if let location { payload["location"] = location }
        guard JSONSerialization.isValidJSONObject(payload) else { return nil }
        return try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    }

    private static func signal(_ value: LocalVisionSignal) -> [String: Any] {
        let perception = value.perception
        var result: [String: Any] = [
            "analyzer_failed": value.analyzerFailed,
            "brightness": value.brightness,
            "scene_change_score": value.sceneChangeScore,
            "is_too_dark": value.isTooDark,
            "is_likely_covered": value.isLikelyCovered,
            "objects": perception.objects.map { object -> [String: Any] in
                var item: [String: Any] = [
                    "kind": object.kind.rawValue,
                    "direction": object.direction.rawValue,
                    "confidence": object.confidence
                ]
                if let box = object.normalizedBoundingBox {
                    item["box"] = rect(box)
                }
                return item
            },
            "lane_polylines": perception.lanePolylines.map { $0.toWire() },
            "path_reasons": perception.pathGuidance.reasons.map { $0.rawValue },
            "segmentation_capability": perception.pathGuidance.segmentationCapability.rawValue,
            "depth_capability": perception.pathGuidance.depthCapability.rawValue,
            "blocked_regions": perception.pathGuidance.blockedRegions.map(rect),
            "uncertain_regions": perception.pathGuidance.uncertainRegions.map(rect)
        ]
        if let grid = perception.traversableGrid { result["traversable_grid"] = grid.toWire() }
        if let grid = perception.laneGrid { result["lane_grid"] = grid.toWire() }
        if let guidance = perception.guidancePath { result["guidance_path"] = guidance.toWire() }
        if let corridor = perception.pathGuidance.guidanceCorridor { result["guidance_corridor"] = rect(corridor) }
        return result
    }

    private static func rect(_ value: CGRect) -> [String: Double] {
        ["x": Double(value.minX), "y": Double(value.minY), "width": Double(value.width), "height": Double(value.height)]
    }

    private static var thermalState: String {
        switch ProcessInfo.processInfo.thermalState {
        case .nominal: return "nominal"
        case .fair: return "fair"
        case .serious: return "serious"
        case .critical: return "critical"
        @unknown default: return "unknown"
        }
    }

    private static var buildInfo: [String: Any] {
        var result: [String: Any] = ["os_version": ProcessInfo.processInfo.operatingSystemVersionString]
        #if DEBUG
        result["configuration"] = "debug"
        #else
        result["configuration"] = "non_debug"
        #endif
        if let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String {
            result["app_version"] = version
        }
        if let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String {
            result["app_build"] = build
        }
        return result
    }
}
