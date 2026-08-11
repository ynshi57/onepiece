import AVFoundation
import CoreImage
import Foundation

// MARK: - Signaling & transport
//
// WebSocket signaling / frame transport extracted verbatim from ContentView.swift.
// `SignalingResponseParser` and `FrameMessageBuilder` are referenced by VQASeeTests
// via `@testable import VQASee`; their names/signatures must not change.

enum StreamTransportError: Error {
    case invalidURL
    case connectionFailed(String)
    case messageEncodingFailed
    case missingRelayConfig
}

enum SignalingResponse: Equatable {
    case serverReady(sessionID: String)
    case clientRegistered(workerOnline: Bool)
    case workerOffline(workerID: String)
    case streamAck(frameID: String)
    case vqaResult(VqaDisplayResult)
    case error(reason: String)
    /// The websocket to the backend dropped (server stopped, network lost, etc.).
    case connectionClosed(reason: String)
    case unsupported
}

struct SignalingResponseParser {
    static func parse(from payload: [String: Any]) -> SignalingResponse {
        guard let type = payload["type"] as? String else {
            return .unsupported
        }

        switch type {
        case "server_ready":
            let sessionID = payload["session_id"] as? String ?? "unknown"
            return .serverReady(sessionID: sessionID)
        case "client_registered":
            let workerOnline = payload["worker_online"] as? Bool ?? false
            return .clientRegistered(workerOnline: workerOnline)
        case "worker_offline":
            let workerID = payload["worker_id"] as? String ?? "unknown"
            return .workerOffline(workerID: workerID)
        case "stream_ack":
            guard let frameID = payload["frame_id"] as? String else {
                return .unsupported
            }
            return .streamAck(frameID: frameID)
        case "vqa_result":
            let scene = payload["scene"] as? String ?? "unknown"
            let objects = payload["objects"] as? [String] ?? []
            let description = payload["description"] as? String ?? "no description"
            let summary = payload["summary"] as? String ?? description
            let spatialDescription = payload["spatial_description"] as? String ?? "空间方向信息不足。"
            let riskLevel = payload["risk_level"] as? String ?? "low"
            let riskMessage = payload["risk_message"] as? String ?? "暂未发现明显危险。"
            let suggestedAction = payload["suggested_action"] as? String ?? "保持手机朝向前方，缓慢移动以获取更多信息。"
            let spokenText = payload["spoken_text"] as? String ?? "\(summary) \(riskMessage)"
            let ocrText = payload["ocr_text"] as? String ?? ""
            let changeSignificance = payload["change_significance"] as? String ?? "major"
            let changes = payload["changes"] as? String ?? ""
            let latencyMs: Double?
            if let rawLatency = payload["latency_ms"] as? Double {
                latencyMs = rawLatency
            } else if let rawLatency = payload["latency_ms"] as? Int {
                latencyMs = Double(rawLatency)
            } else {
                latencyMs = nil
            }
            return .vqaResult(
                VqaDisplayResult(
                    scene: scene,
                    objects: objects,
                    description: description,
                    summary: summary,
                    spatialDescription: spatialDescription,
                    riskLevel: riskLevel,
                    riskMessage: riskMessage,
                    suggestedAction: suggestedAction,
                    spokenText: spokenText,
                    ocrText: ocrText,
                    latencyMs: latencyMs,
                    changeSignificance: changeSignificance,
                    changes: changes
                )
            )
        case "error", "inference_error", "dropped":
            return .error(reason: payload["reason"] as? String ?? "unknown")
        default:
            return .unsupported
        }
    }
}

struct FrameMessageBuilder {
    static func build(
        frameID: String,
        prompt: String,
        model: String,
        jpegData: Data,
        gps: (lat: Double, lon: Double)?,
        mode: String = "",
        question: String = "",
        context: FrameContext? = nil,
        previousImageBase64: String? = nil,
        ocrText: String = ""
    ) -> [String: Any] {
        var payload: [String: Any] = [
            "type": "frame",
            "frame_id": frameID,
            // `prompt` stays for backward compatibility with older backends;
            // newer backends prefer `mode` + `question` and rebuild the prompt.
            "prompt": prompt,
            "model": model,
            "image_base64": jpegData.base64EncodedString(),
        ]

        let trimmedMode = mode.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedMode.isEmpty {
            payload["mode"] = trimmedMode
        }
        let trimmedQuestion = question.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedQuestion.isEmpty {
            payload["question"] = trimmedQuestion
        }

        if let context, context.hasContent {
            payload["context"] = context.payload
        }
        if let previousImageBase64, !previousImageBase64.isEmpty {
            payload["previous_image_base64"] = previousImageBase64
        }
        let trimmedOcrText = ocrText.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedOcrText.isEmpty {
            payload["client_ocr_text"] = trimmedOcrText
        }

        if let gps {
            payload["gps"] = [
                "lat": gps.lat,
                "lon": gps.lon,
            ]
        }

        return payload
    }
}

protocol VideoTransporting: Sendable {
    func connect(
        serverURL: URL,
        relayConfig: RelayAuthConfig,
        onEvent: @escaping @Sendable (SignalingResponse) -> Void
    ) async throws
    func sendLocationUpdate(lat: Double, lon: Double) async
    func sendFrame(
        frameID: String,
        prompt: String,
        model: String,
        jpegData: Data,
        gps: (lat: Double, lon: Double)?,
        mode: String,
        question: String,
        context: FrameContext?,
        previousImageBase64: String?,
        ocrText: String
    ) async
    func sendDiagnosticFrame(
        jpegData: Data,
        metadataJSON: String
    ) async -> Bool
    func disconnect() async
}

actor MockWebRTCTransport: VideoTransporting {
    private var isConnected = false

    func connect(
        serverURL: URL,
        relayConfig: RelayAuthConfig,
        onEvent: @escaping @Sendable (SignalingResponse) -> Void
    ) async throws {
        guard let scheme = serverURL.scheme, scheme == "ws" || scheme == "wss" else {
            throw StreamTransportError.invalidURL
        }
        try await Task.sleep(nanoseconds: 150_000_000)
        isConnected = true
        onEvent(.streamAck(frameID: "mock-frame"))
        onEvent(
            .vqaResult(
                VqaDisplayResult(
                    scene: "mock-scene",
                    objects: ["person"],
                    description: "mock transport result",
                    summary: "前方有人。",
                    spatialDescription: "正前方近处可能有人，左右两侧未发现明显障碍。",
                    riskLevel: "low",
                    riskMessage: "暂未发现明显危险。",
                    suggestedAction: "保持手机朝向前方。",
                    spokenText: "前方有人。暂未发现明显危险。",
                    ocrText: "",
                    latencyMs: 5.0,
                    changeSignificance: "major",
                    changes: ""
                )
            )
        )
    }

    func sendLocationUpdate(lat: Double, lon: Double) async {
        guard isConnected else {
            return
        }
    }

    func sendFrame(
        frameID: String,
        prompt: String,
        model: String,
        jpegData: Data,
        gps: (lat: Double, lon: Double)?,
        mode: String,
        question: String,
        context: FrameContext?,
        previousImageBase64: String?,
        ocrText: String
    ) async {
        guard isConnected else {
            return
        }
    }

    func sendDiagnosticFrame(jpegData: Data, metadataJSON: String) async -> Bool {
        isConnected
    }

    func disconnect() async {
        isConnected = false
    }
}

actor WebSocketSignalingTransport: VideoTransporting {
    private var session: URLSession?
    private var webSocketTask: URLSessionWebSocketTask?
    private var receiveTask: Task<Void, Never>?
    private var frameID: String?
    private var usesRelayProtocol = false

    func connect(
        serverURL: URL,
        relayConfig: RelayAuthConfig,
        onEvent: @escaping @Sendable (SignalingResponse) -> Void
    ) async throws {
        guard let scheme = serverURL.scheme, scheme == "ws" || scheme == "wss" else {
            throw StreamTransportError.invalidURL
        }

        usesRelayProtocol = serverURL.path.contains("/ws/client")
        if usesRelayProtocol && !relayConfig.isConfigured {
            throw StreamTransportError.missingRelayConfig
        }

        let urlSession = URLSession(configuration: .default)
        let socketTask = urlSession.webSocketTask(with: serverURL)
        socketTask.resume()

        session = urlSession
        webSocketTask = socketTask
        frameID = "frame-\(UUID().uuidString)"

        receiveTask = Task {
            await self.receiveLoop(onEvent: onEvent)
        }

        if usesRelayProtocol {
            try await sendJSON(
                [
                    "type": "client_register",
                    "client_id": relayConfig.clientID,
                    "worker_id": relayConfig.workerID,
                    "pairing_token": relayConfig.pairingToken,
                ]
            )
        } else {
            try await sendJSON(
                [
                    "type": "stream_start",
                    "frame_id": frameID ?? "frame-unknown",
                    "prompt": "Describe the scene in this camera frame.",
                ]
            )
        }
    }

    func sendLocationUpdate(lat: Double, lon: Double) async {
        do {
            try await sendJSON(
                [
                    "type": "location_update",
                    "gps": [
                        "lat": lat,
                        "lon": lon,
                    ],
                ]
            )
        } catch {
            return
        }
    }

    func sendFrame(
        frameID: String,
        prompt: String,
        model: String,
        jpegData: Data,
        gps: (lat: Double, lon: Double)?,
        mode: String,
        question: String,
        context: FrameContext?,
        previousImageBase64: String?,
        ocrText: String
    ) async {
        var payload = FrameMessageBuilder.build(
            frameID: frameID,
            prompt: prompt,
            model: model,
            jpegData: jpegData,
            gps: gps,
            mode: mode,
            question: question,
            context: context,
            previousImageBase64: previousImageBase64,
            ocrText: ocrText
        )
        if usesRelayProtocol {
            payload["type"] = "frame_request"
            payload["request_id"] = frameID
        }
        do {
            try await sendJSON(payload)
        } catch {
            return
        }
    }

    func sendDiagnosticFrame(jpegData: Data, metadataJSON: String) async -> Bool {
        do {
            try await sendJSON(
                [
                    "type": "diagnostic_frame",
                    "image_base64": jpegData.base64EncodedString(),
                    "metadata_json": metadataJSON,
                ]
            )
            return true
        } catch {
            return false
        }
    }

    func disconnect() async {
        if let frameID {
            try? await sendJSON(["type": "stop", "frame_id": frameID])
        }
        receiveTask?.cancel()
        receiveTask = nil
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        webSocketTask = nil
        session?.invalidateAndCancel()
        session = nil
        frameID = nil
        usesRelayProtocol = false
    }

    private func sendJSON(_ payload: [String: Any]) async throws {
        guard let webSocketTask else {
            throw StreamTransportError.connectionFailed("socket_not_connected")
        }
        guard JSONSerialization.isValidJSONObject(payload) else {
            throw StreamTransportError.messageEncodingFailed
        }
        let data = try JSONSerialization.data(withJSONObject: payload, options: [])
        guard let string = String(data: data, encoding: .utf8) else {
            throw StreamTransportError.messageEncodingFailed
        }
        try await webSocketTask.send(.string(string))
    }

    private func receiveLoop(onEvent: @escaping @Sendable (SignalingResponse) -> Void) async {
        guard let webSocketTask else {
            return
        }

        while !Task.isCancelled {
            do {
                let message = try await webSocketTask.receive()
                let parsed = parseWebSocketMessage(message)
                onEvent(parsed)
            } catch {
                // receive() throwing means the socket is gone (server stopped, network
                // dropped). Surface it as a distinct closed event, not a generic error,
                // unless we cancelled the task ourselves (normal stop).
                if !Task.isCancelled {
                    onEvent(.connectionClosed(reason: error.localizedDescription))
                }
                break
            }
        }
    }

    private func parseWebSocketMessage(_ message: URLSessionWebSocketTask.Message) -> SignalingResponse {
        let data: Data
        switch message {
        case .string(let string):
            guard let stringData = string.data(using: .utf8) else {
                return .unsupported
            }
            data = stringData
        case .data(let rawData):
            data = rawData
        @unknown default:
            return .unsupported
        }

        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let payload = object as? [String: Any]
        else {
            return .unsupported
        }
        return SignalingResponseParser.parse(from: payload)
    }
}

enum FrameJPEGEncoder {
    private static let context = CIContext()

    static func encode(
        sampleBuffer: CMSampleBuffer,
        maxDimension: CGFloat = StreamingLimits.maxImageDimension,
        quality: CGFloat = StreamingLimits.jpegQuality,
        maxBytes: Int = StreamingLimits.maxJPEGBytes
    ) -> Data? {
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
            return nil
        }
        // AVCaptureVideoDataOutput delivers camera buffers in sensor orientation.
        // Vision requests below use `.right`, so the JPEG sent to Qwen and saved in
        // diagnostics must be oriented the same way. Otherwise the local detector
        // sees an upright image while the backend/model sees a sideways frame,
        // which causes poor VQA and confusing diagnostic thumbnails.
        let orientedImage = CIImage(cvPixelBuffer: pixelBuffer).oriented(.right)
        let width = orientedImage.extent.width
        let height = orientedImage.extent.height
        let scale = min(1.0, maxDimension / max(width, height))
        let ciImage = orientedImage
            .transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        for candidateQuality in [quality, 0.45, 0.35] {
            guard let data = context.jpegRepresentation(
                of: ciImage,
                colorSpace: colorSpace,
                options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: candidateQuality]
            ) else {
                continue
            }
            if data.count <= maxBytes {
                return data
            }
        }
        return nil
    }
}
