import CoreLocation
import Foundation

// MARK: - Pure, testable helpers
//
// Extracted from ContentView.swift. These types are hit directly by VQASeeTests
// via `@testable import VQASee`, so their names/signatures/visibility must not
// change. Deliberately no `import SwiftUI` here so the file parses standalone.

struct StreamingConfigValidator {
    static func normalizeServerURL(_ input: String) -> URL? {
        let trimmed = input.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            return nil
        }

        if trimmed.hasPrefix("ws://") || trimmed.hasPrefix("wss://") {
            return URL(string: trimmed)
        }

        if trimmed.contains("://") {
            return nil
        }

        return URL(string: "ws://\(trimmed)")
    }

    static func isLoopbackHost(_ input: String) -> Bool {
        guard let url = normalizeServerURL(input), let host = url.host?.lowercased() else {
            return false
        }
        return host == "localhost" || host == "127.0.0.1" || host == "::1"
    }
}

/// Pure decision logic for auto-connect, factored out for swiftc unit tests.
/// The design (approved by the user): full-auto when a single backend is found,
/// present a selection list only when 2+ are found, and never clobber an address
/// the user pinned manually.
struct AutoConnectPolicy {
    static func decide(discovered: [DiscoveredServer], userPinned: Bool) -> AutoServerDecision {
        if userPinned {
            return .keepUserChoice
        }
        if discovered.isEmpty {
            return .searching
        }
        if discovered.count == 1 {
            return .autoFill(discovered[0].url)
        }
        return .choose(discovered)
    }
}

/// Parses a numeric IPv4 dotted-quad string from a `sockaddr` blob (as found in
/// `NetService.addresses`). Returns nil for non-IPv4 families or malformed data,
/// so callers can fall back to the `.local` hostname. Pure/testable.
enum SockaddrParser {
    static func ipv4String(fromSockaddr data: Data) -> String? {
        return data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) -> String? in
            guard
                let base = raw.baseAddress,
                raw.count >= MemoryLayout<sockaddr>.size
            else {
                return nil
            }
            let sa = base.assumingMemoryBound(to: sockaddr.self)
            guard sa.pointee.sa_family == sa_family_t(AF_INET) else {
                return nil
            }
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            let result = getnameinfo(
                sa,
                socklen_t(data.count),
                &host,
                socklen_t(host.count),
                nil,
                0,
                NI_NUMERICHOST
            )
            guard result == 0 else {
                return nil
            }
            return String(cString: host)
        }
    }
}

/// Plans the fallback host sweep used when Bonjour fails to resolve a Mac backend.
///
/// Auto-discovery relies on Bonjour (`_vqasee._tcp`), but that silently finds
/// nothing in common setups (e.g. a VPN interface on the Mac hijacking the
/// advertised address). The previous fallback only scanned the iPhone-hotspot
/// subnet `172.20.10.x`, so on a normal shared Wi-Fi the app never found the Mac
/// and rejected the loopback default with `invalid_server_url_for_device`.
///
/// Given the device's own IPv4 address, this derives the other host addresses on
/// the same /24 so we can probe them for a healthy backend. It naturally covers
/// the hotspot case too (there the phone sits on `172.20.10.x`, so its own /24 is
/// the hotspot subnet). Pure/testable — no networking here.
enum LocalSubnetPlanner {
    /// Candidate host IPv4 strings to probe on the device's own /24, ordered so the
    /// most likely gateway/host addresses (`.1`, low numbers) come first, and the
    /// device's own address is excluded. Returns [] if `deviceIPv4` is not a usable
    /// private dotted-quad (we never sweep a public/loopback/link-local range).
    static func candidateHosts(deviceIPv4: String) -> [String] {
        let trimmed = deviceIPv4.trimmingCharacters(in: .whitespacesAndNewlines)
        let parts = trimmed.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else {
            return []
        }
        var octets = [Int]()
        for part in parts {
            guard let value = Int(part), (0...255).contains(value) else {
                return []
            }
            octets.append(value)
        }
        guard isProbablePrivateHost(octets) else {
            return []
        }
        let selfHost = octets[3]
        let prefix = "\(octets[0]).\(octets[1]).\(octets[2])."
        // .1 first (typical gateway/host), then the rest of the usable range 2...254,
        // skipping the network (.0), broadcast (.255) and the device's own address.
        var ordered = [Int]()
        if selfHost != 1 {
            ordered.append(1)
        }
        for host in 2...254 where host != selfHost && host != 1 {
            ordered.append(host)
        }
        return ordered.map { prefix + "\($0)" }
    }

    /// Restrict sweeping to RFC-1918 private ranges; never scan loopback (127.x),
    /// link-local (169.254.x) or public addresses.
    private static func isProbablePrivateHost(_ octets: [Int]) -> Bool {
        switch (octets[0], octets[1]) {
        case (10, _):
            return true
        case (192, 168):
            return true
        case (172, let second) where (16...31).contains(second):
            return true
        default:
            return false
        }
    }
}

struct LocationTextFormatter {
    static func format(lat: Double, lon: Double) -> String {
        String(format: "%.5f, %.5f", lat, lon)
    }
}

/// Turns a reverse-geocoded placemark into a short Chinese place label used as a
/// physical anchor in the prompt context, e.g. "中关村南路附近" / "海淀区附近".
/// Pure (no I/O) so it can be unit-tested without CLGeocoder.
enum PlaceLabelFormatter {
    static func format(_ placemark: CLPlacemark?) -> String? {
        guard let placemark else {
            return nil
        }
        // Prefer the most specific meaningful component available.
        let candidates: [String?] = [
            placemark.name,
            placemark.thoroughfare,
            placemark.subLocality,
            placemark.locality,
            placemark.subAdministrativeArea,
            placemark.administrativeArea,
        ]
        for case let value? in candidates {
            let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty {
                return "\(trimmed)附近"
            }
        }
        return nil
    }
}

enum LatencyBreakdown {
    /// Pure computation so the segmentation is unit-testable without the camera/network stack.
    static func compute(
        sentAt: CFTimeInterval,
        receivedAt: CFTimeInterval,
        encodeMs: Double?,
        serverModelMs: Double?
    ) -> LatencySegments {
        let roundTripMs = max(0, (receivedAt - sentAt) * 1000.0)
        return LatencySegments(
            encodeMs: encodeMs.map { max(0, $0) },
            roundTripMs: roundTripMs,
            serverModelMs: serverModelMs.map { max(0, $0) }
        )
    }

    static func format(_ segments: LatencySegments) -> String {
        // End-to-end is what the user actually waits for: encode happens before the round trip.
        let endToEnd = (segments.encodeMs ?? 0) + segments.roundTripMs
        var parts: [String] = []
        if let encodeMs = segments.encodeMs {
            parts.append(String(format: "编码%.0f", encodeMs))
        }
        if let networkQueueMs = segments.networkQueueMs {
            parts.append(String(format: "网络+排队%.0f", networkQueueMs))
        }
        if let serverModelMs = segments.serverModelMs {
            parts.append(String(format: "模型%.0f", serverModelMs))
        }
        let head = String(format: "端到端 %.0f ms", endToEnd)
        if parts.isEmpty {
            return head
        }
        return "\(head)（\(parts.joined(separator: " "))）"
    }
}

/// Continuity context echoed back to the (stateless) backend on each frame so it
/// can report only important changes instead of repeating the same description.
/// A value type so it crosses the `Sendable` transport boundary cleanly.
struct FrameContext: Sendable, Equatable {
    let prevSummary: String
    let prevScene: String
    let prevObjects: [String]
    let placeLabel: String
    let elapsedMs: Double
    let localVisionSummary: String

    init(
        prevSummary: String,
        prevScene: String,
        prevObjects: [String],
        placeLabel: String,
        elapsedMs: Double,
        localVisionSummary: String = ""
    ) {
        self.prevSummary = prevSummary
        self.prevScene = prevScene
        self.prevObjects = prevObjects
        self.placeLabel = placeLabel
        self.elapsedMs = elapsedMs
        self.localVisionSummary = localVisionSummary
    }

    /// True when there is at least one piece of prior state worth sending.
    var hasContent: Bool {
        !prevSummary.isEmpty || !prevScene.isEmpty || !prevObjects.isEmpty || !placeLabel.isEmpty || !localVisionSummary.isEmpty
    }

    /// JSON-ready dictionary with only the non-empty fields populated.
    var payload: [String: Any] {
        var dict: [String: Any] = [:]
        if !prevSummary.isEmpty {
            dict["prev_summary"] = prevSummary
        }
        if !prevScene.isEmpty {
            dict["prev_scene"] = prevScene
        }
        if !prevObjects.isEmpty {
            dict["prev_objects"] = prevObjects
        }
        if !placeLabel.isEmpty {
            dict["place_label"] = placeLabel
        }
        if elapsedMs > 0 {
            dict["elapsed_ms"] = elapsedMs
        }
        if !localVisionSummary.isEmpty {
            dict["local_vision"] = localVisionSummary
        }
        return dict
    }
}

/// Pure decision for whether a new result should be spoken aloud. Keeps the
/// "don't repeat yourself while standing still" logic testable in isolation.
enum SpeechGate {
    private static let riskRank: [String: Int] = ["low": 0, "medium": 1, "high": 2]

    static func rank(_ riskLevel: String) -> Int {
        riskRank[riskLevel.lowercased(), default: 0]
    }

    /// Speak when: this is the first visual result, OR the model flags a major
    /// change, OR risk rose vs last time, OR we've been silent longer than
    /// `maxSilenceMs` (a safety heartbeat so we never go fully quiet).
    ///
    /// First-result speech is explicit because local fast-vision context can make
    /// the backend return `change_significance=none` even on the first frame; a
    /// low-vision user still needs to hear that VQASee is seeing something.
    static func shouldSpeak(
        changeSignificance: String,
        previousRiskLevel: String?,
        newRiskLevel: String,
        millisecondsSinceLastSpoken: Double?,
        maxSilenceMs: Double
    ) -> Bool {
        if millisecondsSinceLastSpoken == nil {
            return true
        }
        if changeSignificance.lowercased() == "major" {
            return true
        }
        if let previousRiskLevel, rank(newRiskLevel) > rank(previousRiskLevel) {
            return true
        }
        if let elapsed = millisecondsSinceLastSpoken, elapsed >= maxSilenceMs {
            return true
        }
        return false
    }
}


enum VoiceFeedbackDecision: Equatable {
    case speak(text: String, force: Bool, reason: String)
    case silent(reason: String)

    var reason: String {
        switch self {
        case .speak(_, _, let reason), .silent(let reason):
            return reason
        }
    }
}

enum VoiceFeedbackPolicy {
    static func decideForModelResult(
        answeringVoiceQuestion: Bool,
        hasOCROverride: Bool,
        ocrText: String,
        result: VqaDisplayResult,
        previousRiskLevel: String?,
        millisecondsSinceLastSpoken: Double?,
        maxSilenceMs: Double
    ) -> VoiceFeedbackDecision {
        if answeringVoiceQuestion {
            return .speak(text: hasOCROverride ? ReadTextPresentation.spokenText(for: ocrText) : result.spokenText, force: true, reason: "回答用户提问")
        }

        let shouldSpeak = SpeechGate.shouldSpeak(
            changeSignificance: result.changeSignificance,
            previousRiskLevel: previousRiskLevel,
            newRiskLevel: result.riskLevel,
            millisecondsSinceLastSpoken: millisecondsSinceLastSpoken,
            maxSilenceMs: maxSilenceMs
        )
        guard shouldSpeak else {
            return .silent(reason: "无重要变化")
        }

        if hasOCROverride {
            return .speak(text: ReadTextPresentation.spokenText(for: ocrText), force: false, reason: "读文字结果")
        }
        if result.changeSignificance.lowercased() != "major", !result.changes.isEmpty {
            return .speak(text: result.changes, force: false, reason: "重要变化")
        }
        if millisecondsSinceLastSpoken == nil {
            return .speak(text: result.spokenText, force: false, reason: "首次视觉反馈")
        }
        if let previousRiskLevel, SpeechGate.rank(result.riskLevel) > SpeechGate.rank(previousRiskLevel) {
            return .speak(text: result.spokenText, force: false, reason: "风险升高")
        }
        if let elapsed = millisecondsSinceLastSpoken, elapsed >= maxSilenceMs {
            return .speak(text: result.spokenText, force: false, reason: "安全心跳")
        }
        return .speak(text: result.spokenText, force: false, reason: "模型要求播报")
    }
}

enum WalkingImmediateFeedbackPolicy {
    static let cooldownMs: Double = 8_000

    static func decide(
        mode: AssistanceMode,
        signal: LocalVisionSignal,
        hasQuestion: Bool,
        millisecondsSinceLastImmediateSpeech: Double?
    ) -> VoiceFeedbackDecision {
        guard mode == .walking else {
            return .silent(reason: "非行走模式不做本地即时播报")
        }
        if hasQuestion {
            return .silent(reason: "用户提问中，等待回答")
        }
        if let elapsed = millisecondsSinceLastImmediateSpeech, elapsed < cooldownMs {
            return .silent(reason: "本地即时播报冷却中")
        }
        if signal.isLikelyCovered {
            return .speak(text: "镜头可能被挡住，请调整手机。", force: false, reason: "本地检测镜头遮挡")
        }
        if signal.isTooDark {
            return .speak(text: "画面有些暗，请先放慢。", force: false, reason: "本地检测画面偏暗")
        }
        if let object = signal.perception.primaryRiskObject {
            return .speak(
                text: "\(object.direction.chineseLabel)可能有\(object.kind.chineseLabel)，请放慢，我正在确认。",
                force: false,
                reason: "本地感知检测到\(object.kind.chineseLabel)"
            )
        }
        if signal.perception.hasRoadOrDepthCue {
            return .speak(
                text: "前方有疑似边界或道路标线，请放慢并自行确认。",
                force: false,
                reason: "本地感知道路线索"
            )
        }
        if signal.hasHuman {
            switch signal.humanDirection {
            case .left:
                return .speak(text: "左前方可能有人，我正在确认。", force: false, reason: "本地检测疑似人形")
            case .right:
                return .speak(text: "右前方可能有人，我正在确认。", force: false, reason: "本地检测疑似人形")
            case .center:
                return .speak(text: "正前方可能有人，我正在确认。", force: false, reason: "本地检测疑似人形")
            case .unknown:
                return .speak(text: "前方可能有人，我正在确认。", force: false, reason: "本地检测疑似人形")
            }
        }
        if signal.sceneChangeScore >= max(0.36, WalkingFrameSendPolicy.sceneChangeThreshold * 2) {
            return .speak(text: "前方画面变化明显，我正在确认。", force: false, reason: "本地检测明显变化")
        }
        return .silent(reason: "无本地即时播报条件")
    }
}

enum VoiceQuestionIntent: Equatable {
    case visualQuestion
    case readText
    case nonVisual

    static func classify(_ rawText: String) -> VoiceQuestionIntent {
        let text = rawText
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        guard !text.isEmpty else {
            return .visualQuestion
        }

        let readTextKeywords = ["读", "念", "文字", "写的", "写着", "说明书", "药", "标签", "菜单", "路牌", "sign", "text", "read"]
        if readTextKeywords.contains(where: { text.contains($0) }) {
            return .readText
        }

        let visualKeywords = ["前方", "前面", "左边", "右边", "周围", "这里", "这个", "那是什么", "有什么", "能不能走", "可以走", "障碍", "台阶", "门", "车", "人", "红灯", "绿灯", "看"]
        if visualKeywords.contains(where: { text.contains($0) }) {
            return .visualQuestion
        }

        let nonVisualKeywords = ["今天", "星期", "几点", "现在时间", "天气", "多少度", "新闻", "讲个笑话", "你是谁", "你知道", "日期", "日子", "time", "weather"]
        if nonVisualKeywords.contains(where: { text.contains($0) }) {
            return .nonVisual
        }

        return .visualQuestion
    }
}

enum ReadTextPresentation {
    static func summary(for ocrText: String) -> String {
        let cleaned = clean(ocrText)
        if cleaned.isEmpty {
            return String(localized: "没有读到清晰文字。")
        }
        return cleaned
    }

    static func action(for ocrText: String) -> String {
        clean(ocrText).isEmpty
            ? String(localized: "请把文字放到画面中央，靠近一点，保持光线充足。")
            : String(localized: "已优先读取画面文字。")
    }

    static func spokenText(for ocrText: String, maxChars: Int = 260) -> String {
        let cleaned = clean(ocrText)
        if cleaned.isEmpty {
            return String(localized: "没有读到清晰文字。请靠近一点并对准。")
        }
        if cleaned.count <= maxChars {
            return cleaned
        }
        let prefix = String(cleaned.prefix(maxChars))
        return String(localized: "我先读前面一部分：\(prefix)")
    }

    private static func clean(_ raw: String) -> String {
        raw
            .components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: "\n")
    }
}

// MARK: - Push-to-talk gesture interpretation
//
// The press-to-talk button drives its start/stop from a UIKit
// UILongPressGestureRecognizer (see PressToTalkButton.swift) because a SwiftUI
// DragGesture gets torn down when the parent republishes mid-press. This enum is
// a Foundation-only, testable mapping from a gesture phase to the action to take,
// so the start/stop decision can be unit-tested without a device or UIKit.

/// A device-independent abstraction of `UIGestureRecognizer.State`, so the
/// mapping below can be exercised in tests that don't link UIKit.
enum PressPhase {
    case possible
    case began
    case changed
    case ended
    case cancelled
    case failed
}

/// What the press-to-talk button should do for a given gesture phase.
enum PressAction {
    case start
    case stop
    case ignore
}

enum PressGestureInterpreter {
    /// Begin recording as soon as the press is recognized; stop on any terminal
    /// phase (finger up, or the system cancelling/failing the recognizer). The
    /// downstream speech controller is idempotent, so a stop with no active
    /// recording is a harmless no-op.
    static func action(for phase: PressPhase) -> PressAction {
        switch phase {
        case .began:
            return .start
        case .ended, .cancelled, .failed:
            return .stop
        case .possible, .changed:
            return .ignore
        }
    }
}
