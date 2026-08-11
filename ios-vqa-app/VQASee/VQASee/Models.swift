import CoreGraphics
import Foundation

// MARK: - Value types & UI-facing enums
//
// Pure model/value types extracted from ContentView.swift during the immersive-UI
// refactor. Behavior is unchanged — these are the same declarations, only relocated
// so the view layer can be split into focused files. Names/signatures are preserved
// because VQASeeTests references several of them via `@testable import VQASee`.

enum StreamStatus: Equatable {
    case idle
    case preparing
    case streaming
    case error(String)

    var title: String {
        switch self {
        case .idle:
            return String(localized: "未开始")
        case .preparing:
            return String(localized: "连接中")
        case .streaming:
            return String(localized: "运行中")
        case .error(let message):
            return String(localized: "异常：\(message)")
        }
    }
}

/// A Bonjour-discovered backend, identified by its resolved ws URL.
struct DiscoveredServer: Equatable, Identifiable {
    let name: String
    let url: URL

    var id: String { url.absoluteString }
    var host: String { url.host ?? url.absoluteString }
}

/// Outcome of the auto-connect decision, given the current discovery set.
/// Pure/testable: no UIKit, no side effects. See `AutoConnectPolicy`.
enum AutoServerDecision: Equatable {
    /// Nothing found yet — keep the "searching" hint, do not touch the address.
    case searching
    /// The user already picked/typed an address — never override it.
    case keepUserChoice
    /// Exactly one backend found — auto-fill its URL (but do NOT auto-connect).
    case autoFill(URL)
    /// Multiple backends found — the user must choose one.
    case choose([DiscoveredServer])
}

struct StreamingLimits {
    static let maxImageDimension: CGFloat = 448
    static let jpegQuality: CGFloat = 0.45
    static let maxJPEGBytes = 120_000
    static let minFrameInterval: CFTimeInterval = 2.0
}

struct FrameEncodingProfile: Equatable {
    let maxDimension: CGFloat
    let jpegQuality: CGFloat
    let maxJPEGBytes: Int
}

struct RelayAuthConfig: Sendable {
    let pairingToken: String
    let workerID: String
    let clientID: String

    var isConfigured: Bool {
        !pairingToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !workerID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !clientID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}

struct RuntimeStatus: Codable, Equatable {
    let status: String
    let apiBaseURL: String
    let configuredModel: String
    let resolvedModel: String
    let dynamicModelSelection: Bool
    let availableModels: [String]
    let routingReason: String?
    let message: String?

    enum CodingKeys: String, CodingKey {
        case status
        case apiBaseURL = "api_base_url"
        case configuredModel = "configured_model"
        case resolvedModel = "resolved_model"
        case dynamicModelSelection = "dynamic_model_selection"
        case availableModels = "available_models"
        case routingReason = "routing_reason"
        case message
    }
}

enum RuntimeModelPolicy {
    static func selectableOptions(for status: RuntimeStatus?) -> [VqaModelOption] {
        guard let status else {
            return VqaModelOption.allCases
        }
        guard status.status == "qwen" else {
            return []
        }
        if status.dynamicModelSelection {
            let available = Set(status.availableModels)
            var options: [VqaModelOption] = [.automatic]
            options.append(contentsOf: VqaModelOption.allCases.filter {
                $0 != .automatic && available.contains($0.rawValue)
            })
            return options
        }
        if let option = VqaModelOption.option(for: status.resolvedModel) {
            return [option]
        }
        return []
    }

    static func modelID(
        selectedModel: VqaModelOption,
        mode: AssistanceMode,
        status: RuntimeStatus?
    ) -> String {
        guard let status, status.status == "qwen" else {
            return selectedModel.resolvedModel(for: mode)
        }
        if status.dynamicModelSelection {
            return selectedModel.resolvedModel(for: mode)
        }
        return status.resolvedModel
    }
}


/// Internal route used after removing user-visible modes. The user sees one
/// "observe risks" experience; the app still routes frames narrowly for latency
/// and model quality. These raw values are backend route IDs, not UI labels.
enum ObservationRoute: String, Equatable {
    case riskObserve = "risk_observe"
    case readText = "readText"
    case question = "question"
    case detail = "detail"

    static func resolve(question: String, voiceIntent: VoiceQuestionIntent?) -> ObservationRoute {
        if voiceIntent == .readText {
            return .readText
        }
        let normalized = question.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if normalized.isEmpty {
            return .riskObserve
        }
        if normalized.contains("详细") || normalized.contains("多说") || normalized.contains("描述一下") {
            return .detail
        }
        return .question
    }

    var prompt: String {
        switch self {
        case .riskObserve:
            return "" // backend default risk_observe prompt; keep iOS prompt quiet.
        case .readText:
            return "模式=读文字。请优先读取画面文字，保持原文顺序。若文字不清楚，请说明应该更靠近、对准或增加光线。"
        case .question:
            return "模式=风险观察。请优先回答用户问题，并补充必要的风险、障碍和不确定性提醒。不要说可以走、可以开或安全通过。"
        case .detail:
            return "模式=详细。请在安全风险优先的前提下，描述当前画面的关键物体、空间关系、文字和不确定性。不要说可以走、可以开或安全通过。"
        }
    }

    var backendMode: String {
        switch self {
        case .riskObserve:
            return rawValue
        case .readText:
            return rawValue
        case .question:
            return "risk_observe"
        case .detail:
            return rawValue
        }
    }

    var encodingProfile: FrameEncodingProfile {
        switch self {
        case .riskObserve, .question:
            return FrameEncodingProfile(maxDimension: 448, jpegQuality: 0.45, maxJPEGBytes: 120_000)
        case .detail:
            return FrameEncodingProfile(maxDimension: 768, jpegQuality: 0.62, maxJPEGBytes: 320_000)
        case .readText:
            return FrameEncodingProfile(maxDimension: 1024, jpegQuality: 0.72, maxJPEGBytes: 520_000)
        }
    }

    var isSingleShotPreferred: Bool {
        switch self {
        case .readText, .detail, .question:
            return true
        case .riskObserve:
            return false
        }
    }

    var shouldSendPreviousFrame: Bool {
        self == .detail
    }

    var compatibilityMode: AssistanceMode {
        switch self {
        case .riskObserve, .question:
            return .walking
        case .readText:
            return .readText
        case .detail:
            return .detail
        }
    }
}

enum AssistanceMode: String, CaseIterable, Identifiable {
    case surroundings
    case walking
    case readText
    case detail

    var id: String { rawValue }

    var title: String {
        switch self {
        case .surroundings:
            return String(localized: "看周围")
        case .walking:
            return String(localized: "走路")
        case .readText:
            return String(localized: "读文字")
        case .detail:
            return String(localized: "详细看")
        }
    }

    /// NOTE: `prompt` is intentionally NOT localized. These are model-steering
    /// instructions sent to the VQA backend; they must stay in Chinese in every
    /// locale or the model's output quality degrades. Do not wrap in String(localized:).
    var prompt: String {
        switch self {
        case .surroundings:
            return "模式=周围。用中文描述整体场景和空间布局。请明确左侧、正前方、右侧分别有什么；先说场景类型，再说最重要的物体和位置。不要只列物体。"
        case .walking:
            return "模式=行走。只关注安全通行。请判断左前方、正前方、右前方是否有人、车辆、台阶、门、障碍物、边缘或可通行空间。输出简短风险等级、方向感和下一步行动建议。不要描述无关细节。"
        case .readText:
            return "模式=读文字。请优先读取画面文字，保持原文顺序。若文字不清楚，请说明应该更靠近、对准或增加光线。"
        case .detail:
            return "模式=详细。请用中文较详细描述画面：场景、左中右空间关系、近处和远处物体、文字、可能风险、以及建议行动。"
        }
    }

    var isSingleShotPreferred: Bool {
        self == .readText || self == .detail
    }

    var shouldSendPreviousFrame: Bool {
        // Dual-image input is expensive. Keep continuous modes fast and safe; use
        // text context for change detection there. Detailed single-shot can spend
        // the extra image budget when a previous frame exists.
        self == .detail
    }

    var encodingProfile: FrameEncodingProfile {
        switch self {
        case .walking:
            return FrameEncodingProfile(maxDimension: 448, jpegQuality: 0.45, maxJPEGBytes: 120_000)
        case .surroundings:
            return FrameEncodingProfile(maxDimension: 640, jpegQuality: 0.55, maxJPEGBytes: 220_000)
        case .detail:
            return FrameEncodingProfile(maxDimension: 768, jpegQuality: 0.62, maxJPEGBytes: 320_000)
        case .readText:
            return FrameEncodingProfile(maxDimension: 1024, jpegQuality: 0.72, maxJPEGBytes: 520_000)
        }
    }
}

enum VqaModelOption: String, CaseIterable, Identifiable {
    // NOTE: raw values are model IDs consumed by the backend — never localize them.
    case automatic = "auto"
    case fast3b = "qwen2.5vl:3b"
    case accurate7b = "qwen2.5vl:7b"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .automatic:
            return String(localized: "自动")
        case .fast3b:
            return String(localized: "快速 3B")
        case .accurate7b:
            return String(localized: "更准 7B")
        }
    }

    var hint: String {
        switch self {
        case .automatic:
            return String(localized: "按模式自动选择：行走和周围用 3B，读文字和详细用 7B。")
        case .fast3b:
            return String(localized: "速度优先，适合行走连续识别。")
        case .accurate7b:
            return String(localized: "准确度和场景理解更好，但延迟更高。")
        }
    }

    func resolvedModel(for mode: AssistanceMode) -> String {
        switch self {
        case .automatic:
            switch mode {
            case .walking, .surroundings:
                return VqaModelOption.fast3b.rawValue
            case .readText, .detail:
                return VqaModelOption.accurate7b.rawValue
            }
        case .fast3b, .accurate7b:
            return rawValue
        }
    }

    static func option(for modelID: String) -> VqaModelOption? {
        VqaModelOption.allCases.first { $0.rawValue == modelID }
    }
}

struct LatencySegments: Equatable {
    /// On-device JPEG encode time, if measured.
    let encodeMs: Double?
    /// iPhone send -> iPhone receive round trip (network + relay + queue + model).
    let roundTripMs: Double
    /// Server-reported pure model inference time, if provided.
    let serverModelMs: Double?

    /// Network + relay + queue time, derived by removing model time from the round trip.
    var networkQueueMs: Double? {
        guard let serverModelMs else {
            return nil
        }
        return max(0, roundTripMs - serverModelMs)
    }
}

struct VqaDisplayResult: Equatable {
    let scene: String
    let objects: [String]
    let description: String
    let summary: String
    let spatialDescription: String
    let riskLevel: String
    let riskMessage: String
    let suggestedAction: String
    let spokenText: String
    let ocrText: String
    let latencyMs: Double?
    /// Scene-continuity fields. `changeSignificance` is one of none|minor|major;
    /// defaults to "major" (speak it) for older backends that don't send it.
    let changeSignificance: String
    let changes: String
}
