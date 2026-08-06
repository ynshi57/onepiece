import AVFoundation
import Foundation
import Speech

/// Press-to-talk speech state, kept separate from the audio/speech frameworks so
/// the transitions can be unit-tested without a device.
enum SpeechInputState: Equatable {
    case idle
    case recording
    case finalizing
    case unavailable(String)
}

/// Pure helpers for speech input: no AVFoundation / Speech dependency, fully testable.
enum SpeechTextCleaner {
    /// Normalize a raw transcription into a question suitable for the VQA prompt.
    /// Collapses internal whitespace and trims. Returns nil if effectively empty.
    static func clean(_ raw: String) -> String? {
        let collapsed = raw
            .components(separatedBy: .whitespacesAndNewlines)
            .filter { !$0.isEmpty }
            .joined(separator: " ")
        let trimmed = collapsed.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

/// Maps authorization results into a user-facing state, isolated for testing.
enum SpeechAuthorizationEvaluator {
    static func state(
        speechAuthorized: Bool,
        micGranted: Bool,
        recognizerAvailable: Bool
    ) -> SpeechInputState {
        if !speechAuthorized {
            return .unavailable("未获得语音识别权限，请在设置中开启。")
        }
        if !micGranted {
            return .unavailable("未获得麦克风权限，请在设置中开启。")
        }
        if !recognizerAvailable {
            return .unavailable("当前设备暂不支持中文语音识别。")
        }
        return .idle
    }
}

/// Wraps SFSpeechRecognizer + AVAudioEngine for press-to-talk Chinese recognition.
///
/// For this short push-to-talk use case we use Apple's regular recognition path
/// instead of forcing on-device recognition: some devices report zh-CN on-device
/// support but return empty transcripts for short utterances. Camera frames are
/// still local; only the brief spoken question may use Apple's speech service.
@MainActor
final class SpeechRecognitionController: NSObject {
    /// Emitted continuously with partial text while recording, and once more when final.
    var onPartialText: ((String) -> Void)?
    /// Emitted once with the cleaned final question (nil if nothing usable was heard).
    var onFinalText: ((String?) -> Void)?
    /// Emitted while recording with a normalized 0...1 microphone level.
    var onAudioLevel: ((Double) -> Void)?
    /// Emitted on state changes (idle/recording/finalizing/unavailable).
    var onStateChanged: ((SpeechInputState) -> Void)?

    private let speechRecognizer: SFSpeechRecognizer?
    private let audioEngine = AVAudioEngine()
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var latestTranscript = ""
    private(set) var isRunning = false
    private var stopRequested = false
    private var recordingStartedAt: Date?
    private var deferredStopTask: Task<Void, Never>?
    private let minimumCaptureDuration: TimeInterval = 0.75

    override init() {
        self.speechRecognizer = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN"))
        super.init()
    }

    /// Request speech + microphone authorization and report the resulting state.
    func requestAuthorization(completion: @escaping (SpeechInputState) -> Void) {
        SFSpeechRecognizer.requestAuthorization { [weak self] speechAuth in
            let speechAuthorized = speechAuth == .authorized
            Self.requestMicPermission { micGranted in
                Task { @MainActor in
                    let recognizerAvailable = self?.speechRecognizer?.isAvailable ?? false
                    let state = SpeechAuthorizationEvaluator.state(
                        speechAuthorized: speechAuthorized,
                        micGranted: micGranted,
                        recognizerAvailable: recognizerAvailable
                    )
                    self?.onStateChanged?(state)
                    completion(state)
                }
            }
        }
    }

    private static func requestMicPermission(_ completion: @escaping (Bool) -> Void) {
        if #available(iOS 17.0, *) {
            AVAudioApplication.requestRecordPermission { granted in
                completion(granted)
            }
        } else {
            AVAudioSession.sharedInstance().requestRecordPermission { granted in
                completion(granted)
            }
        }
    }

    /// Begin capturing audio and streaming partial transcriptions.
    func startRecording() {
        guard !isRunning else {
            return
        }
        deferredStopTask?.cancel()
        deferredStopTask = nil
        stopRequested = false
        guard let speechRecognizer, speechRecognizer.isAvailable else {
            onStateChanged?(.unavailable("语音识别暂不可用。"))
            return
        }

        do {
            try configureAudioSessionForRecording()
        } catch {
            onStateChanged?(.unavailable("音频会话切换失败：\(error.localizedDescription)"))
            return
        }

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.taskHint = .dictation
        request.contextualStrings = [
            "前方有什么",
            "左边有什么",
            "右边有什么",
            "有障碍物吗",
            "红灯还是绿灯",
            "帮我读文字",
        ]
        if #available(iOS 16.0, *) {
            request.addsPunctuation = false
        }
        // Use Apple's regular recognition path by default. Some devices report
        // zh-CN on-device support but produce no transcript for short push-to-talk
        // utterances; the server-capable path is noticeably more reliable for this
        // "hold, ask a short question, release" interaction. This only sends the
        // user's brief voice question, not camera frames.
        request.requiresOnDeviceRecognition = false
        recognitionRequest = request
        latestTranscript = ""

        let inputNode = audioEngine.inputNode
        let recordingFormat = inputNode.outputFormat(forBus: 0)
        inputNode.removeTap(onBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: recordingFormat) { [weak self] buffer, _ in
            self?.recognitionRequest?.append(buffer)
            let level = Self.normalizedLevel(from: buffer)
            Task { @MainActor in
                self?.onAudioLevel?(level)
            }
        }

        audioEngine.prepare()
        do {
            try audioEngine.start()
        } catch {
            teardownAudio()
            onStateChanged?(.unavailable("无法开始录音：\(error.localizedDescription)"))
            return
        }

        isRunning = true
        recordingStartedAt = Date()
        onStateChanged?(.recording)

        recognitionTask = speechRecognizer.recognitionTask(with: request) { [weak self] result, error in
            guard let self else {
                return
            }
            Task { @MainActor in
                if let result {
                    self.latestTranscript = result.bestTranscription.formattedString
                    self.onPartialText?(self.latestTranscript)
                    if result.isFinal {
                        self.finish()
                    }
                } else if let error {
                    // Ending audio often completes the task through an error path.
                    // If the user explicitly released the button, finalize with
                    // whatever partial transcript we already have. Otherwise surface
                    // the real error instead of collapsing it into "didn't catch".
                    if self.stopRequested {
                        self.finish()
                    } else {
                        self.fail("语音识别失败：\(error.localizedDescription)")
                    }
                }
            }
        }
    }

    /// Stop capturing; the recognizer will emit a final result which triggers `finish()`.
    func stopRecording() {
        guard isRunning else {
            return
        }
        onStateChanged?(.finalizing)
        stopRequested = true
        let elapsed = recordingStartedAt.map { Date().timeIntervalSince($0) } ?? minimumCaptureDuration
        let remaining = max(0, minimumCaptureDuration - elapsed)
        if remaining > 0 {
            deferredStopTask?.cancel()
            deferredStopTask = Task { [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(remaining * 1_000_000_000))
                await MainActor.run {
                    self?.endAudioCapture()
                }
            }
        } else {
            endAudioCapture()
        }
    }

    private func endAudioCapture() {
        audioEngine.inputNode.removeTap(onBus: 0)
        if audioEngine.isRunning {
            audioEngine.stop()
        }
        recognitionRequest?.endAudio()
    }

    private func finish() {
        guard isRunning else {
            return
        }
        let cleaned = SpeechTextCleaner.clean(latestTranscript)
        teardownAudio()
        isRunning = false
        onStateChanged?(.idle)
        onFinalText?(cleaned)
    }

    private func fail(_ message: String) {
        teardownAudio()
        isRunning = false
        onStateChanged?(.unavailable(message))
    }

    private func teardownAudio() {
        onAudioLevel?(0)
        deferredStopTask?.cancel()
        deferredStopTask = nil
        recordingStartedAt = nil
        stopRequested = false
        audioEngine.inputNode.removeTap(onBus: 0)
        if audioEngine.isRunning {
            audioEngine.stop()
        }
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest = nil
    }

    private func configureAudioSessionForRecording() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .spokenAudio, options: [.duckOthers, .defaultToSpeaker])
        try session.setActive(true, options: .notifyOthersOnDeactivation)
    }

    private nonisolated static func normalizedLevel(from buffer: AVAudioPCMBuffer) -> Double {
        guard let channelData = buffer.floatChannelData else {
            return 0
        }
        let frameLength = Int(buffer.frameLength)
        guard frameLength > 0 else {
            return 0
        }
        let samples = channelData[0]
        var sum: Float = 0
        for index in 0..<frameLength {
            let sample = samples[index]
            sum += sample * sample
        }
        let rms = sqrt(sum / Float(frameLength))
        let db = 20 * log10(max(rms, 0.000_001))
        // Map roughly -55dB...-10dB to 0...1. Quiet rooms still show movement,
        // speech gets into the visible middle/high range.
        return min(1, max(0, Double((db + 55) / 45)))
    }
}
