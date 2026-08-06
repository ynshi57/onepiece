import Foundation
import Vision

/// Lightweight on-device OCR used to boost `读文字` and voice questions about text.
/// It runs on the already-encoded JPEG so it adds no extra camera capture path.
enum OCRRecognition {
    static func recognizeText(from jpegData: Data, mode: AssistanceMode, question: String) async -> String {
        let questionHintsText = question.lowercased()
        let shouldRun = mode == .readText
            || mode == .detail
            || questionHintsText.contains("字")
            || questionHintsText.contains("文字")
            || questionHintsText.contains("读")
            || questionHintsText.contains("sign")
            || questionHintsText.contains("text")
        guard shouldRun else {
            return ""
        }

        return await Task.detached(priority: .userInitiated) {
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = .accurate
            request.usesLanguageCorrection = true
            request.recognitionLanguages = ["zh-Hans", "en-US"]
            request.minimumTextHeight = 0.015

            let handler = VNImageRequestHandler(data: jpegData, options: [:])
            do {
                try handler.perform([request])
            } catch {
                return ""
            }

            let lines = (request.results ?? [])
                .compactMap { $0.topCandidates(1).first?.string.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }

            let joined = lines.prefix(12).joined(separator: "\n")
            if joined.count > 500 {
                return String(joined.prefix(500))
            }
            return joined
        }.value
    }
}
