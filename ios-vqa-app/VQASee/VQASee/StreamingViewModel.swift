import AVFoundation
import Combine
import CoreLocation
import Foundation
import SwiftUI
import UIKit

// MARK: - StreamingViewModel
//
// The app's core ObservableObject, extracted verbatim from ContentView.swift during
// the immersive-UI refactor. Networking, camera, Bonjour, speech and scene-continuity
// logic are UNCHANGED — this is a relocation, not a rewrite. User-facing Chinese
// string literals are localized via String(localized:) so they participate in the
// String Catalog; model-steering prompts (AssistanceMode.prompt) stay literal.

@MainActor
final class StreamingViewModel: NSObject, ObservableObject, CLLocationManagerDelegate {
    private static let serverURLDefaultsKey = "vqasee.server.url.input"
    private static let pairingTokenDefaultsKey = "vqasee.relay.pairing_token"
    private static let workerIDDefaultsKey = "vqasee.relay.worker_id"
    private static let clientIDDefaultsKey = "vqasee.relay.client_id"
    private static let modelDefaultsKey = "vqasee.model.option"
    private static let defaultServerURL = "localhost:9000/ws/signaling"
    private static let defaultWorkerID = "local-mac-worker"
    private static let defaultClientID = "bayes-iphone"

    @Published var streamStatus: StreamStatus = .idle
    @Published var serverURLInput: String {
        didSet {
            UserDefaults.standard.set(serverURLInput, forKey: Self.serverURLDefaultsKey)
            // A change that did NOT originate from auto-discovery means the user
            // typed/picked an address — pin it so discovery stops overriding it.
            if !isApplyingDiscoveredServer {
                userPinnedServer = true
            }
        }
    }
    /// True while we are programmatically applying a discovered address, so the
    /// `serverURLInput.didSet` above does not misread it as a manual user edit.
    private var isApplyingDiscoveredServer = false
    /// Set once the user picks a backend from the list or types one manually;
    /// auto-discovery then leaves the address alone (respects the user's choice).
    private var userPinnedServer = false
    @Published var pairingTokenInput: String {
        didSet {
            UserDefaults.standard.set(pairingTokenInput, forKey: Self.pairingTokenDefaultsKey)
        }
    }
    @Published var workerIDInput: String {
        didSet {
            UserDefaults.standard.set(workerIDInput, forKey: Self.workerIDDefaultsKey)
        }
    }
    @Published var clientIDInput: String {
        didSet {
            UserDefaults.standard.set(clientIDInput, forKey: Self.clientIDDefaultsKey)
        }
    }
    @Published var locationText: String = "unknown"
    @Published var errorText: String?
    @Published var summaryText: String = String(localized: "点击开始视觉辅助")
    @Published var spatialText: String = String(localized: "等待空间方向信息")
    @Published var riskText: String = String(localized: "等待识别")
    /// Raw backend risk level ("low"/"medium"/"high"), kept separate from the
    /// localized `riskText` so the UI can pick a semantic color without parsing
    /// display strings (which breaks once the text is localized).
    @Published var currentRiskLevel: String = "low"
    @Published var actionText: String = String(localized: "请让 Mac 连接 iPhone 热点，并启动本地后端。")
    @Published var debugText: String = "waiting"
    @Published var latencyText: String = "--"
    /// True while a frame is in flight; the UI keeps showing the previous latency
    /// value and just marks it as updating, instead of blanking it to "处理中…".
    @Published var isProcessing = false
    @Published var nearbyServerText: String = String(localized: "正在寻找 Mac 后端…")
    /// Backends currently discovered via Bonjour (de-duplicated). When 2+ are
    /// present the UI shows a picker; a single one is auto-filled.
    @Published var discoveredServers: [DiscoveredServer] = []
    /// True when multiple backends are discovered and the user must pick one.
    @Published var showServerPicker = false
    @Published var showAdvancedSettings = false
    @Published var selectedMode: AssistanceMode = .surroundings
    @Published var questionInput: String = ""
    @Published var selectedModel: VqaModelOption {
        didSet {
            UserDefaults.standard.set(selectedModel.rawValue, forKey: Self.modelDefaultsKey)
        }
    }
    @Published var runtimeStatusText = String(localized: "正在确认本地模型…")
    @Published var runtimeStatus: RuntimeStatus?
    @Published var isRefreshingRuntimeStatus = false
    @Published var isVoiceEnabled = true
    @Published var isRecording = false
    @Published var speechStatusText = ""
    @Published var speechInputLevel: Double = 0

    let captureSession = AVCaptureSession()

    private let transport: VideoTransporting
    private let speechSynthesizer = AVSpeechSynthesizer()
    private let speechController = SpeechRecognitionController()
    private var isSpeechAvailable = false
    private var isVoicePressHeld = false
    private var speechPeakLevel: Double = 0
    private let nearbyServerBrowser = NearbyServerBrowser()
    private let locationManager = CLLocationManager()
    private let videoOutput = AVCaptureVideoDataOutput()
    private let videoOutputQueue = DispatchQueue(label: "vqa.video.output.queue")
    private let frameCaptureProxy = FrameCaptureProxy()
    private var isSessionConfigured = false
    private var latestGPS: (lat: Double, lon: Double)?
    private var isStreamingActive = false
    private var isRequestInFlight = false
    private var pendingSingleShotOnly = false
    private var currentVoiceIntent: VoiceQuestionIntent?
    /// True while a one-off question (typically from voice) is awaiting its answer,
    /// so we can clear `questionInput` after a single reply instead of pinning it to every frame.
    private var clearQuestionAfterNextResult = false
    private var frameCounter: UInt64 = 0
    private var lastSpokenText = ""
    /// Continuity state for scene-memory / incremental reporting. Reset on stop so a
    /// new session starts fresh (first frame speaks, backend gets no stale context).
    private var lastResult: VqaDisplayResult?
    private var lastResultAt: CFTimeInterval?
    private var lastSpokenAt: CFTimeInterval?
    private var previousFrameBase64: String?
    /// Reverse-geocoded human-readable place label ("中关村南路附近"), used as a
    /// physical anchor in the prompt context. Refreshed only after moving enough.
    private var placeLabel = ""
    private var placeLabelCoordinate: CLLocationCoordinate2D?
    private let geocoder = CLGeocoder()
    private var isGeocoding = false
    /// Safety heartbeat: even with no "major" change, speak at least this often so
    /// the app never goes completely silent while streaming.
    private let maxSilenceMs: Double = 25_000
    /// Only re-run reverse geocoding after the user has moved at least this far.
    private let geocodeRefreshMeters: CLLocationDistance = 30
    private var inFlightSentAt: CFTimeInterval?
    private var inFlightEncodeMs: Double?
    private var inFlightOCRText: String?
    private var lastBackendFrameSentAt: CFTimeInterval?
    /// Fires if a sent frame gets no result/error back in time, so the UI can't hang
    /// silently on "处理中…". Slightly longer than the backend's own inference timeout.
    private var inFlightWatchdog: Task<Void, Never>?
    private let inFlightTimeoutSeconds: UInt64 = 50

    init(transport: VideoTransporting = WebSocketSignalingTransport()) {
        self.transport = transport
        let savedServerURL = UserDefaults.standard.string(forKey: Self.serverURLDefaultsKey)
        self.serverURLInput = savedServerURL ?? Self.defaultServerURL
        self.pairingTokenInput = UserDefaults.standard.string(forKey: Self.pairingTokenDefaultsKey) ?? ""
        self.workerIDInput = UserDefaults.standard.string(forKey: Self.workerIDDefaultsKey) ?? Self.defaultWorkerID
        self.clientIDInput = UserDefaults.standard.string(forKey: Self.clientIDDefaultsKey) ?? Self.defaultClientID
        let savedModel = UserDefaults.standard.string(forKey: Self.modelDefaultsKey)
        self.selectedModel = VqaModelOption(rawValue: savedModel ?? "") ?? .automatic
        super.init()
        nearbyServerBrowser.onServersChanged = { [weak self] servers in
            guard let self else {
                return
            }
            self.discoveredServers = servers
            self.applyDiscoveryDecision()
        }
        nearbyServerBrowser.onStatusChanged = { [weak self] statusText in
            guard let self else {
                return
            }
            if statusText.contains("无法搜索本地网络"),
               !StreamingConfigValidator.isLoopbackHost(self.serverURLInput) {
                return
            }
            // Don't let raw per-service status text stomp the "已发现/请选择"
            // summary once we actually have discovered backends.
            if self.discoveredServers.isEmpty || statusText.contains("无法搜索本地网络") {
                self.nearbyServerText = statusText
            }
        }
        nearbyServerBrowser.start()
        configureSpeechAudioSession()
        locationManager.delegate = self
        locationManager.desiredAccuracy = kCLLocationAccuracyBest
        frameCaptureProxy.onFrame = { [weak self] jpegData, encodeMs, localVisionSignal in
            guard let self else {
                return
            }
            Task {
                await self.sendFrame(
                    jpegData: jpegData,
                    encodeMs: encodeMs,
                    localVisionSignal: localVisionSignal
                )
            }
        }
        configureCameraSession()
        configureSpeechController()
    }

    var selectableModelOptions: [VqaModelOption] {
        RuntimeModelPolicy.selectableOptions(for: runtimeStatus)
    }

    deinit {
        nearbyServerBrowser.stop()
    }

    /// React to a change in the discovered-backend set. Pure policy lives in
    /// `AutoConnectPolicy.decide`; this method applies its side effects.
    /// Never starts streaming — the user still taps 开始视觉辅助.
    private func applyDiscoveryDecision() {
        let decision = AutoConnectPolicy.decide(
            discovered: discoveredServers,
            userPinned: userPinnedServer
        )
        switch decision {
        case .searching:
            showServerPicker = false
            nearbyServerText = String(localized: "正在寻找 Mac 后端…")
        case .keepUserChoice:
            // If the user's pinned backend is among those found, still surface a
            // picker when multiple exist so they can switch, but don't override.
            showServerPicker = discoveredServers.count >= 2
        case .autoFill(let url):
            showServerPicker = false
            applyDiscoveredURL(url)
            nearbyServerText = String(localized: "已发现 Mac 后端：\(url.host ?? "")（点开始连接）")
        case .choose(let servers):
            showServerPicker = true
            nearbyServerText = String(localized: "发现 \(servers.count) 个后端，请选择")
        }
    }

    /// Apply a discovered URL to `serverURLInput` without tripping the manual-edit
    /// pin logic in the `didSet`.
    private func applyDiscoveredURL(_ url: URL) {
        isApplyingDiscoveredServer = true
        serverURLInput = url.absoluteString
        isApplyingDiscoveredServer = false
    }

    /// The user explicitly picked a backend from the discovery list. Pins the
    /// choice (discovery stops overriding it) but does not start streaming.
    func selectServer(_ server: DiscoveredServer) {
        applyDiscoveredURL(server.url)
        userPinnedServer = true
        showServerPicker = false
        nearbyServerText = String(localized: "已选择 Mac 后端：\(server.host)（点开始连接）")
    }

    func requestPermissions() {
        AVCaptureDevice.requestAccess(for: .video) { granted in
            if !granted {
                Task { @MainActor in
                    self.errorText = String(localized: "相机权限被拒绝。")
                }
            } else {
                self.startCameraPreviewIfNeeded()
            }
        }
        locationManager.requestWhenInUseAuthorization()
        speechController.requestAuthorization { [weak self] state in
            self?.applySpeechState(state)
        }
    }

    private func configureSpeechController() {
        speechController.onStateChanged = { [weak self] state in
            self?.applySpeechState(state)
        }
        speechController.onPartialText = { [weak self] partial in
            self?.speechStatusText = String(localized: "识别中：\(partial)")
        }
        speechController.onAudioLevel = { [weak self] level in
            guard let self else {
                return
            }
            self.speechInputLevel = level
            self.speechPeakLevel = max(self.speechPeakLevel, level)
        }
        speechController.onFinalText = { [weak self] finalText in
            self?.handleRecognizedQuestion(finalText)
        }
    }

    private func applySpeechState(_ state: SpeechInputState) {
        switch state {
        case .idle:
            isRecording = false
            speechInputLevel = 0
            isSpeechAvailable = true
            // Recording switched the audio session to .playAndRecord; restore for TTS.
            configureSpeechAudioSession()
        case .recording:
            isRecording = true
            speechPeakLevel = 0
            speechStatusText = String(localized: "正在聆听，请说出你的问题…")
        case .finalizing:
            isRecording = false
            speechStatusText = String(localized: "识别中…")
        case .unavailable(let reason):
            isRecording = false
            speechInputLevel = 0
            isSpeechAvailable = false
            speechStatusText = reason
        }
    }

    /// Begin press-to-talk capture; mute any ongoing speech so it isn't recorded.
    func startVoiceQuestion() {
        isVoicePressHeld = true
        guard isSpeechAvailable else {
            speechController.requestAuthorization { [weak self] state in
                guard let self else {
                    return
                }
                self.applySpeechState(state)
                if case .idle = state, self.isVoicePressHeld {
                    self.speechController.startRecording()
                }
            }
            return
        }
        speechSynthesizer.stopSpeaking(at: .immediate)
        speechController.startRecording()
    }

    /// End press-to-talk capture; the final transcript arrives via onFinalText.
    func stopVoiceQuestion() {
        isVoicePressHeld = false
        speechController.stopRecording()
    }

    private func handleRecognizedQuestion(_ text: String?) {
        guard let text else {
            if speechPeakLevel < 0.08 {
                speechStatusText = String(localized: "没有检测到声音，请靠近麦克风再试。")
            } else {
                speechStatusText = String(localized: "没有听清，请按住 1 秒以上再试。")
            }
            return
        }
        let intent = VoiceQuestionIntent.classify(text)
        if intent == .nonVisual {
            let message = String(localized: "我主要帮你看画面。日期、时间和天气可以问 Siri 或系统。")
            questionInput = ""
            currentVoiceIntent = nil
            speechStatusText = message
            summaryText = message
            spatialText = String(localized: "这不是视觉问题。")
            riskText = String(localized: "安全：请继续按需提问画面内容。")
            actionText = String(localized: "你可以问：前方有什么、右边有什么、帮我读文字。")
            speak(message, force: true)
            return
        }
        if intent == .readText {
            selectMode(.readText)
        }
        currentVoiceIntent = intent
        questionInput = text
        speechStatusText = String(localized: "已识别：\(text)")
        // Single-turn semantics: this spoken question is answered once, then cleared
        // so it doesn't stick to every subsequent frame (one-shot Q&A, not a standing prompt).
        clearQuestionAfterNextResult = true
        // If a stream is live, ask immediately on the next frame; otherwise the text
        // sits in questionInput and is sent when the user starts assistance.
        if isStreamingActive {
            pendingSingleShotOnly = true
            isRequestInFlight = false
            // A spoken question must be answered even if the scene is unchanged.
            frameCaptureProxy.forceNextFrame()
        }
    }

    func selectMode(_ mode: AssistanceMode) {
        selectedMode = mode
        frameCaptureProxy.setEncodingProfile(mode.encodingProfile)
        previousFrameBase64 = nil
        lastResult = nil
        lastResultAt = nil
        lastBackendFrameSentAt = nil
        switch mode {
        case .surroundings:
            actionText = String(localized: "低频观察周围环境，并播报重要变化。")
        case .walking:
            actionText = String(localized: "行走模式只强调前方风险和避让建议。")
        case .readText:
            actionText = String(localized: "请把文字放在画面中央，点击开始后识别一次。")
        case .detail:
            actionText = String(localized: "点击开始后详细描述当前画面一次。")
        }
    }

    func refreshRuntimeStatus() {
        guard !isRefreshingRuntimeStatus else {
            return
        }
        guard let statusURL = runtimeStatusURL() else {
            runtimeStatusText = String(localized: "当前连接不支持模型状态查询。")
            return
        }
        isRefreshingRuntimeStatus = true
        runtimeStatusText = String(localized: "正在确认本地模型…")
        Task {
            defer {
                self.isRefreshingRuntimeStatus = false
            }
            do {
                var request = URLRequest(url: statusURL)
                request.timeoutInterval = 2.5
                let (data, response) = try await URLSession.shared.data(for: request)
                guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
                    throw URLError(.badServerResponse)
                }
                let status = try JSONDecoder().decode(RuntimeStatus.self, from: data)
                await MainActor.run {
                    self.applyRuntimeStatus(status)
                }
            } catch {
                await MainActor.run {
                    self.runtimeStatusText = String(localized: "无法确认本地模型：\(error.localizedDescription)")
                }
            }
        }
    }

    private func modelIDForCurrentFrame() -> String {
        RuntimeModelPolicy.modelID(
            selectedModel: selectedModel,
            mode: selectedMode,
            status: runtimeStatus
        )
    }

    private func runtimeStatusURL() -> URL? {
        guard let serverURL = StreamingConfigValidator.normalizeServerURL(serverURLInput) else {
            return nil
        }
        guard serverURL.path.contains("/ws/signaling") else {
            return nil
        }
        var components = URLComponents()
        components.scheme = serverURL.scheme == "wss" ? "https" : "http"
        components.host = serverURL.host
        components.port = serverURL.port
        components.path = "/runtime/status"
        return components.url
    }

    private func applyRuntimeStatus(_ status: RuntimeStatus) {
        runtimeStatus = status
        if status.status == "heuristic" {
            runtimeStatusText = String(localized: "本地模型未启用，仅使用测试规则。")
            return
        }
        if status.dynamicModelSelection {
            runtimeStatusText = String(localized: "可用模型：\(status.availableModels.joined(separator: "、"))")
        } else {
            runtimeStatusText = String(localized: "当前实际模型：\(displayName(forModelID: status.resolvedModel))")
        }
        let options = selectableModelOptions
        if !options.isEmpty && !options.contains(selectedModel) {
            selectedModel = options[0]
        }
    }

    private func displayName(forModelID modelID: String) -> String {
        VqaModelOption.option(for: modelID)?.title ?? modelID
    }

    func startStreaming() async {
        // Discovery always runs (started in init and re-armed here); it is no longer
        // gated on the address being loopback. This lets us re-resolve after a
        // network change (hotspot <-> Wi-Fi) even if a real IP was saved earlier.
        nearbyServerBrowser.start()
        await waitForNearbyServerIfNeeded()

#if !targetEnvironment(simulator)
        if StreamingConfigValidator.isLoopbackHost(serverURLInput) {
            streamStatus = .error("invalid_server_url_for_device")
            errorText = String(localized: "还没有自动发现 Mac 后端。请确认 iPhone 热点已开启、Mac 已连接该热点，并且 Mac 上已运行 bash ./start_backend.sh。")
            return
        }
#endif

        guard let normalizedURL = StreamingConfigValidator.normalizeServerURL(serverURLInput) else {
            streamStatus = .error("invalid_server_url")
            errorText = String(localized: "请输入有效的 ws/wss 地址。")
            return
        }

        streamStatus = .preparing
        errorText = nil
        // Fresh stream: first frame must always be sent (no stale duplicate hash).
        frameCaptureProxy.resetGateState()
        frameCaptureProxy.setEncodingProfile(selectedMode.encodingProfile)
        previousFrameBase64 = nil
        lastBackendFrameSentAt = nil
        pendingSingleShotOnly = selectedMode.isSingleShotPreferred
        summaryText = selectedMode.isSingleShotPreferred ? String(localized: "正在准备单次识别…") : String(localized: "正在准备连续观察…")
        riskText = String(localized: "连接中")
        currentRiskLevel = "low"

        startCameraPreviewIfNeeded()

        do {
            let relayConfig = RelayAuthConfig(
                pairingToken: pairingTokenInput.trimmingCharacters(in: .whitespacesAndNewlines),
                workerID: workerIDInput.trimmingCharacters(in: .whitespacesAndNewlines),
                clientID: clientIDInput.trimmingCharacters(in: .whitespacesAndNewlines)
            )
            try await transport.connect(serverURL: normalizedURL, relayConfig: relayConfig) { [weak self] event in
                guard let self else {
                    return
                }
                Task { @MainActor in
                    self.handleTransportEvent(event)
                }
            }
            locationManager.requestLocation()
            isStreamingActive = true
            streamStatus = .streaming
            if let host = normalizedURL.host {
                nearbyServerText = String(localized: "已连接 Mac 后端：\(host)")
            } else {
                nearbyServerText = String(localized: "已连接 Mac 后端")
            }
            refreshRuntimeStatus()
            if isVoiceEnabled {
                speak(String(localized: "已连接，开始\(selectedMode.title)模式。"), force: true)
            }
        } catch {
            streamStatus = .error("transport_connect_failed")
            errorText = String(localized: "连接失败：\(error.localizedDescription)")
        }
    }

    private func waitForNearbyServerIfNeeded() async {
        // "Address undetermined" == still the default loopback value. A resolved
        // single backend is auto-filled (see applyDiscoveryDecision), which flips
        // this off; a user-picked/typed address is likewise non-loopback. So the
        // loopback check doubles as "we don't yet have a usable address".
        guard StreamingConfigValidator.isLoopbackHost(serverURLInput) else {
            return
        }

        nearbyServerText = String(localized: "正在自动连接 Mac 后端…")
        for _ in 0..<12 {
            if !StreamingConfigValidator.isLoopbackHost(serverURLInput) {
                return
            }
            try? await Task.sleep(nanoseconds: 250_000_000)
        }
        await probeCommonHotspotHostsIfNeeded()
    }

    private func probeCommonHotspotHostsIfNeeded() async {
        guard StreamingConfigValidator.isLoopbackHost(serverURLInput) else {
            return
        }

        nearbyServerText = String(localized: "Bonjour 未发现，正在扫描局域网地址…")

        // Sweep the phone's own /24 first (covers same-Wi-Fi setups where Bonjour
        // failed to resolve, e.g. a VPN interface on the Mac). On an iPhone hotspot
        // the phone itself sits on 172.20.10.x, so this also covers that case. The
        // static hotspot list stays as a backstop for when we can't read the
        // device's own address for some reason.
        var candidateHosts = [String]()
        if let deviceIP = Self.deviceWiFiIPv4() {
            candidateHosts = LocalSubnetPlanner.candidateHosts(deviceIPv4: deviceIP)
        }
        if candidateHosts.isEmpty {
            candidateHosts = ["172.20.10.1"] + (2...15).map { "172.20.10.\($0)" }
        }

        if let host = await firstHealthyHost(candidateHosts, port: 9000) {
            serverURLInput = "ws://\(host):9000/ws/signaling"
            nearbyServerText = String(localized: "已连接 Mac 后端：\(host)")
        }
    }

    /// Probe candidate hosts concurrently (bounded), returning the first that
    /// answers a healthy /health. Concurrency keeps a full /24 sweep to ~seconds
    /// instead of 253 × timeout serially. Ties are broken toward the earliest host
    /// in `hosts` (most-likely addresses are ordered first by LocalSubnetPlanner).
    private func firstHealthyHost(_ hosts: [String], port: Int) async -> String? {
        guard !hosts.isEmpty else {
            return nil
        }
        let maxConcurrent = 24
        var bestIndex: Int?
        var index = 0
        while index < hosts.count {
            let batch = Array(hosts[index..<min(index + maxConcurrent, hosts.count)])
            let batchStart = index
            let found: Int? = await withTaskGroup(of: (Int, Bool).self) { group in
                for (offset, host) in batch.enumerated() {
                    group.addTask { [self] in
                        (batchStart + offset, await isBackendHealthy(host: host, port: port))
                    }
                }
                var localBest: Int?
                for await (hostIndex, healthy) in group {
                    if healthy {
                        localBest = min(localBest ?? hostIndex, hostIndex)
                    }
                }
                return localBest
            }
            if let found {
                bestIndex = found
                break
            }
            index += maxConcurrent
        }
        guard let bestIndex else {
            return nil
        }
        return hosts[bestIndex]
    }

    /// The device's own IPv4 address on the Wi-Fi interface (`en0`), or nil if not
    /// on Wi-Fi. Used to derive the /24 to sweep when Bonjour didn't resolve.
    nonisolated static func deviceWiFiIPv4() -> String? {
        var ifaddr: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&ifaddr) == 0, let first = ifaddr else {
            return nil
        }
        defer { freeifaddrs(ifaddr) }

        var result: String?
        var pointer: UnsafeMutablePointer<ifaddrs>? = first
        while let current = pointer {
            defer { pointer = current.pointee.ifa_next }
            let interface = current.pointee
            guard let addr = interface.ifa_addr, addr.pointee.sa_family == sa_family_t(AF_INET) else {
                continue
            }
            let name = String(cString: interface.ifa_name)
            // en0 is the Wi-Fi interface on iPhone; skip cellular (pdp_ip*) and others.
            guard name == "en0" else {
                continue
            }
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            let addrLen = socklen_t(interface.ifa_addr.pointee.sa_len)
            let status = getnameinfo(addr, addrLen, &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST)
            if status == 0 {
                result = String(cString: host)
                break
            }
        }
        return result
    }

    private func isBackendHealthy(host: String, port: Int) async -> Bool {
        guard let url = URL(string: "http://\(host):\(port)/health") else {
            return false
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 0.35

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
                return false
            }
            return String(data: data, encoding: .utf8)?.contains("\"ok\"") == true
        } catch {
            return false
        }
    }

    func stopStreaming() async {
        clearInFlightWatchdog()
        isStreamingActive = false
        isRequestInFlight = false
        isProcessing = false
        pendingSingleShotOnly = false
        currentVoiceIntent = nil
        clearQuestionAfterNextResult = false
        inFlightSentAt = nil
        inFlightEncodeMs = nil
        inFlightOCRText = nil
        lastBackendFrameSentAt = nil
        // Reset scene-continuity so the next session starts fresh (first frame
        // speaks a full description; backend receives no stale context).
        lastResult = nil
        lastResultAt = nil
        lastSpokenAt = nil
        lastBackendFrameSentAt = nil
        previousFrameBase64 = nil
        frameCaptureProxy.resetGateState()
        await transport.disconnect()
        speechSynthesizer.stopSpeaking(at: .immediate)
        if captureSession.isRunning {
            DispatchQueue.global(qos: .userInitiated).async { [captureSession] in
                captureSession.stopRunning()
            }
        }
        streamStatus = .idle
    }

    /// The backend socket dropped while we were streaming (server stopped, network lost).
    /// Update the visible status/connection text and try to reconnect automatically.
    private func handleConnectionClosed(reason: String) {
        clearInFlightWatchdog()
        isRequestInFlight = false
        isProcessing = false
        inFlightSentAt = nil
        inFlightEncodeMs = nil
        inFlightOCRText = nil

        // If the user already stopped, this is just the expected teardown; ignore.
        guard isStreamingActive else {
            return
        }

        streamStatus = .error("connection_lost")
        nearbyServerText = String(localized: "后端连接已断开，正在重连…")
        summaryText = String(localized: "与 Mac 后端的连接已断开。")
        actionText = String(localized: "请确认 Mac 后端仍在运行；App 会自动尝试重新连接。")
        latencyText = "--"
        errorText = String(localized: "连接已断开：\(reason)")

        // Tear down the dead transport, then retry from a clean state.
        Task { @MainActor in
            await transport.disconnect()
            guard isStreamingActive else {
                return
            }
            // The network may have changed (hotspot <-> Wi-Fi), so the previously
            // resolved IP can be stale. Unconditionally re-arm discovery and clear
            // the manual pin so a freshly resolved backend is adopted; startStreaming
            // then waits briefly for discovery before connecting.
            userPinnedServer = false
            discoveredServers = []
            nearbyServerBrowser.stop()
            nearbyServerBrowser.start()
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard isStreamingActive else {
                return
            }
            isStreamingActive = false
            await startStreaming()
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last else {
            return
        }
        locationText = LocationTextFormatter.format(lat: location.coordinate.latitude, lon: location.coordinate.longitude)
        latestGPS = (lat: location.coordinate.latitude, lon: location.coordinate.longitude)
        refreshPlaceLabelIfNeeded(for: location)
        Task {
            await transport.sendLocationUpdate(
                lat: location.coordinate.latitude,
                lon: location.coordinate.longitude
            )
        }
    }

    /// Reverse-geocode the current location into a short place label used as a
    /// physical anchor in the prompt context. Throttled: only re-runs after the
    /// user has moved `geocodeRefreshMeters`, and never blocks the frame path.
    /// Failures are surfaced in `speechStatusText`/`debugText`, never swallowed.
    private func refreshPlaceLabelIfNeeded(for location: CLLocation) {
        guard !isGeocoding else {
            return
        }
        if let last = placeLabelCoordinate {
            let previous = CLLocation(latitude: last.latitude, longitude: last.longitude)
            if location.distance(from: previous) < geocodeRefreshMeters && !placeLabel.isEmpty {
                return
            }
        }
        isGeocoding = true
        let coordinate = location.coordinate
        geocoder.reverseGeocodeLocation(location) { [weak self] placemarks, error in
            Task { @MainActor in
                guard let self else {
                    return
                }
                self.isGeocoding = false
                if let error {
                    // Non-fatal: keep the last known label; just note it.
                    self.debugText = String(localized: "地点反查失败：\(error.localizedDescription)")
                    return
                }
                guard let label = PlaceLabelFormatter.format(placemarks?.first) else {
                    return
                }
                self.placeLabel = label
                self.placeLabelCoordinate = coordinate
            }
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        errorText = String(localized: "定位更新失败：\(error.localizedDescription)")
    }

    private func configureCameraSession() {
        guard !isSessionConfigured else {
            return
        }

        captureSession.beginConfiguration()
        captureSession.sessionPreset = .hd1280x720
        defer {
            captureSession.commitConfiguration()
            isSessionConfigured = true
        }

        guard let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
            errorText = String(localized: "未找到后置摄像头。")
            return
        }

        do {
            let input = try AVCaptureDeviceInput(device: camera)
            if captureSession.canAddInput(input) {
                captureSession.addInput(input)
            } else {
                errorText = String(localized: "无法将摄像头输入加入会话。")
            }
        } catch {
            errorText = String(localized: "摄像头初始化失败：\(error.localizedDescription)")
        }

        videoOutput.alwaysDiscardsLateVideoFrames = true
        videoOutput.videoSettings = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
        ]
        videoOutput.setSampleBufferDelegate(frameCaptureProxy, queue: videoOutputQueue)
        if captureSession.canAddOutput(videoOutput) {
            captureSession.addOutput(videoOutput)
        }
    }

    private func handleTransportEvent(_ event: SignalingResponse) {
        switch event {
        case .serverReady:
            break
        case .clientRegistered(let workerOnline):
            debugText = workerOnline ? "relay connected, worker online" : "relay connected, waiting for worker"
        case .workerOffline(let workerID):
            clearInFlightWatchdog()
            isRequestInFlight = false
            isProcessing = false
            inFlightSentAt = nil
            inFlightEncodeMs = nil
        inFlightOCRText = nil
            errorText = String(localized: "Worker 离线：\(workerID)")
        case .streamAck(let frameID):
            debugText = "stream ack: \(frameID), waiting for frame result..."
        case .vqaResult(let result):
            clearInFlightWatchdog()
            isRequestInFlight = false
            isProcessing = false
            let ocrOverride = selectedMode == .readText ? (inFlightOCRText ?? "") : ""
            let hasOCROverride = !ocrOverride.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            summaryText = hasOCROverride ? ReadTextPresentation.summary(for: ocrOverride) : result.summary
            spatialText = hasOCROverride ? String(localized: "已从画面中读取文字。") : result.spatialDescription
            riskText = hasOCROverride ? String(localized: "安全：正在读文字") : "\(riskTitle(for: result.riskLevel))：\(result.riskMessage)"
            currentRiskLevel = result.riskLevel
            actionText = hasOCROverride ? ReadTextPresentation.action(for: ocrOverride) : result.suggestedAction
            debugText = "scene: \(result.scene), objects: \(result.objects.joined(separator: ",")), desc: \(result.description)"
            if let sentAt = inFlightSentAt {
                let segments = LatencyBreakdown.compute(
                    sentAt: sentAt,
                    receivedAt: CACurrentMediaTime(),
                    encodeMs: inFlightEncodeMs,
                    serverModelMs: result.latencyMs
                )
                latencyText = LatencyBreakdown.format(segments)
            } else if let latencyMs = result.latencyMs {
                latencyText = String(format: "模型 %.0f ms", latencyMs)
            } else {
                latencyText = "--"
            }
            inFlightSentAt = nil
            inFlightEncodeMs = nil
        inFlightOCRText = nil
            notifyForRisk(level: result.riskLevel)

            // Speak-gating: a direct answer to a voice question is always spoken;
            // otherwise only speak on an important change / risk increase / after a
            // long silence, so standing still doesn't repeat the same description.
            let answeringVoiceQuestion = clearQuestionAfterNextResult
            let now = CACurrentMediaTime()
            let msSinceLastSpoken = lastSpokenAt.map { (now - $0) * 1000.0 }
            let shouldSpeak = answeringVoiceQuestion || SpeechGate.shouldSpeak(
                changeSignificance: result.changeSignificance,
                previousRiskLevel: lastResult?.riskLevel,
                newRiskLevel: result.riskLevel,
                millisecondsSinceLastSpoken: msSinceLastSpoken,
                maxSilenceMs: maxSilenceMs
            )
            if shouldSpeak {
                // Prefer the concise change delta when it exists and we're not
                // answering a specific question.
                let phrase: String
                if !answeringVoiceQuestion,
                   result.changeSignificance.lowercased() != "major",
                   !result.changes.isEmpty {
                    phrase = result.changes
                } else if hasOCROverride {
                    phrase = ReadTextPresentation.spokenText(for: ocrOverride)
                } else {
                    phrase = result.spokenText
                }
                speak(phrase)
                lastSpokenAt = now
            } else {
                debugText += " [静默：\(result.changeSignificance)]"
            }

            // Record continuity state for the next frame's context.
            lastResult = result
            lastResultAt = now

            if clearQuestionAfterNextResult {
                questionInput = ""
                clearQuestionAfterNextResult = false
            }
            if pendingSingleShotOnly {
                isStreamingActive = false
                pendingSingleShotOnly = false
            }
            currentVoiceIntent = nil
        case .error(let reason):
            clearInFlightWatchdog()
            isRequestInFlight = false
            isProcessing = false
            inFlightSentAt = nil
            inFlightEncodeMs = nil
        inFlightOCRText = nil
            errorText = String(localized: "信令错误：\(reason)")
        case .connectionClosed(let reason):
            handleConnectionClosed(reason: reason)
        case .unsupported:
            break
        }
    }

    private func sendFrame(
        jpegData: Data,
        encodeMs: Double,
        localVisionSignal: LocalVisionSignal
    ) async {
        guard isStreamingActive else {
            return
        }
        guard !isRequestInFlight else {
            return
        }
        let currentQuestion = questionInput
        let millisecondsSinceLastBackendFrame = lastBackendFrameSentAt.map {
            (CACurrentMediaTime() - $0) * 1000.0
        }
        let sendDecision = WalkingFrameSendPolicy.decide(
            mode: selectedMode,
            signal: localVisionSignal,
            hasQuestion: !currentQuestion.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            pendingSingleShot: pendingSingleShotOnly,
            millisecondsSinceLastBackendFrame: millisecondsSinceLastBackendFrame
        )
        if case .skip(let reason) = sendDecision {
            debugText = "local vision: \(reason); \(localVisionSignal.backendContext)"
            return
        }
        guard jpegData.count <= selectedMode.encodingProfile.maxJPEGBytes else {
            errorText = String(localized: "跳过一帧：JPEG 太大（\(jpegData.count) 字节）。")
            return
        }
        isRequestInFlight = true
        isProcessing = true
        let ocrText = await OCRRecognition.recognizeText(
            from: jpegData,
            mode: selectedMode,
            question: currentQuestion
        )
        let hasOCRText = !ocrText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        if selectedMode == .readText {
            summaryText = ReadTextPresentation.summary(for: ocrText)
            spatialText = hasOCRText ? String(localized: "已从画面中读取文字。") : String(localized: "文字不够清晰。")
            riskText = String(localized: "安全：正在读文字")
            actionText = ReadTextPresentation.action(for: ocrText)
            if hasOCRText && (currentVoiceIntent == .readText || pendingSingleShotOnly) {
                speak(ReadTextPresentation.spokenText(for: ocrText), force: true)
            }
        }
        frameCounter += 1
        let frameID = "frame-\(frameCounter)"
        let sentAt = CACurrentMediaTime()
        inFlightSentAt = sentAt
        lastBackendFrameSentAt = sentAt
        inFlightEncodeMs = encodeMs
        inFlightOCRText = ocrText
        // Keep the previous latencyText visible; the UI shows an "更新中" hint via isProcessing.
        armInFlightWatchdog(for: frameID)
        await transport.sendFrame(
            frameID: frameID,
            prompt: selectedMode.prompt,
            model: modelIDForCurrentFrame(),
            jpegData: jpegData,
            gps: latestGPS,
            mode: selectedMode.rawValue,
            question: currentQuestion,
            context: currentFrameContext(localVisionSignal: localVisionSignal),
            previousImageBase64: selectedMode.shouldSendPreviousFrame ? previousFrameBase64 : nil,
            ocrText: ocrText
        )
        previousFrameBase64 = jpegData.base64EncodedString()
    }

    /// Assemble the continuity context from the previous result + place label so
    /// the stateless backend can report only important changes. Returns nil on the
    /// first frame of a session (no prior state -> backend does a full description).
    private func currentFrameContext(localVisionSignal: LocalVisionSignal?) -> FrameContext? {
        let localVisionSummary = localVisionSignal?.backendContext ?? ""
        guard let lastResult else {
            return (placeLabel.isEmpty && localVisionSummary.isEmpty) ? nil : FrameContext(
                prevSummary: "",
                prevScene: "",
                prevObjects: [],
                placeLabel: placeLabel,
                elapsedMs: 0,
                localVisionSummary: localVisionSummary
            )
        }
        let elapsedMs: Double
        if let lastResultAt {
            elapsedMs = max(0, (CACurrentMediaTime() - lastResultAt) * 1000.0)
        } else {
            elapsedMs = 0
        }
        return FrameContext(
            prevSummary: lastResult.summary,
            prevScene: lastResult.scene,
            prevObjects: lastResult.objects,
            placeLabel: placeLabel,
            elapsedMs: elapsedMs,
            localVisionSummary: localVisionSummary
        )
    }

    /// Arm a one-shot timer for the in-flight frame. If no result/error arrives in
    /// `inFlightTimeoutSeconds`, surface it and release the in-flight lock so the
    /// stream recovers instead of hanging on "处理中…" forever.
    private func armInFlightWatchdog(for frameID: String) {
        inFlightWatchdog?.cancel()
        inFlightWatchdog = Task { [weak self] in
            guard let self else {
                return
            }
            try? await Task.sleep(nanoseconds: self.inFlightTimeoutSeconds * 1_000_000_000)
            if Task.isCancelled {
                return
            }
            await MainActor.run {
                self.handleInFlightTimeout(frameID: frameID)
            }
        }
    }

    private func clearInFlightWatchdog() {
        inFlightWatchdog?.cancel()
        inFlightWatchdog = nil
    }

    private func handleInFlightTimeout(frameID: String) {
        // Only act if we're still waiting on this exact frame.
        guard isRequestInFlight else {
            return
        }
        isRequestInFlight = false
        isProcessing = false
        inFlightSentAt = nil
        inFlightEncodeMs = nil
        inFlightOCRText = nil
        latencyText = String(localized: "超时")
        debugText = "timeout waiting for \(frameID)"
        errorText = String(localized: "等待结果超时（\(inFlightTimeoutSeconds)s）：可能是模型太慢或连接中断。可重试或切换到更快的 3B 模型。")
        if pendingSingleShotOnly {
            isStreamingActive = false
            pendingSingleShotOnly = false
        }
        currentVoiceIntent = nil
    }

    private func riskTitle(for level: String) -> String {
        switch level.lowercased() {
        case "high":
            return String(localized: "高风险")
        case "medium":
            return String(localized: "注意")
        default:
            return String(localized: "安全")
        }
    }

    private func notifyForRisk(level: String) {
        switch level.lowercased() {
        case "high":
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        case "medium":
            UINotificationFeedbackGenerator().notificationOccurred(.warning)
        default:
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        }
    }

    private func speak(_ text: String, force: Bool = false) {
        guard isVoiceEnabled else {
            return
        }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return
        }
        guard force || trimmed != lastSpokenText else {
            return
        }
        lastSpokenText = trimmed
        if speechSynthesizer.isSpeaking {
            speechSynthesizer.stopSpeaking(at: .word)
        }
        let utterance = AVSpeechUtterance(string: trimmed)
        utterance.voice = AVSpeechSynthesisVoice(language: "zh-CN")
        utterance.rate = 0.48
        utterance.pitchMultiplier = 1.0
        speechSynthesizer.speak(utterance)
    }

    private func configureSpeechAudioSession() {
        do {
            let audioSession = AVAudioSession.sharedInstance()
            try audioSession.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
            try audioSession.setActive(true)
        } catch {
            errorText = String(localized: "语音会话初始化失败：\(error.localizedDescription)")
        }
    }

    private func startCameraPreviewIfNeeded() {
        guard !captureSession.isRunning else {
            return
        }
        DispatchQueue.global(qos: .userInitiated).async { [captureSession] in
            captureSession.startRunning()
        }
    }
}
