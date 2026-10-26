import AVFoundation
import UIKit

/// Spatial-scan coach only. Does not share the outdoor walking speech gate.
@MainActor final class SpatialScanCoachSpeech {
    static let mutedKey = "spatial.scanCoach.muted"
    private let synthesizer = AVSpeechSynthesizer()
    private var lastSpokenAt: TimeInterval = -.infinity
    private var committedTurn: SpatialCaptureGuidance.State?
    private let cooldown: TimeInterval = 3.5

    var isMuted: Bool {
        get { UserDefaults.standard.bool(forKey: Self.mutedKey) }
        set { UserDefaults.standard.set(newValue, forKey: Self.mutedKey) }
    }

    func announce(_ state: SpatialCaptureGuidance.State, force: Bool = false) {
        if state == .turnLeft || state == .turnRight {
            if committedTurn == state, !force { return }
            committedTurn = state
        }
        if state == .circuitComplete { committedTurn = nil }
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        speak(state.message, force: force)
    }

    func announceCompletion(_ text: String) {
        speak(text, force: true)
    }

    func stop() {
        committedTurn = nil
        synthesizer.stopSpeaking(at: .immediate)
    }

    private func speak(_ text: String, force: Bool) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isMuted else { return }
        let now = ProcessInfo.processInfo.systemUptime
        if !force, now - lastSpokenAt < cooldown { return }
        lastSpokenAt = now
        synthesizer.stopSpeaking(at: .immediate)
        let utterance = AVSpeechUtterance(string: trimmed)
        utterance.voice = AVSpeechSynthesisVoice(language: "zh-CN")
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        synthesizer.speak(utterance)
    }
}
