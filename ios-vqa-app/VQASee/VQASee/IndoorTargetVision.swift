import Foundation
import CoreGraphics
import CoreImage
import CoreVideo
import ImageIO
import UniformTypeIdentifiers
import Vision

/// All points and rectangles use the upright source image, normalized, top-left origin.
/// Feature archives are local evidence, not calibrated object identity embeddings.
nonisolated struct IndoorTargetEvidence: Codable, Sendable {
    let maskPNG: Data
    let normalizedBounds: CGRect
    let featurePrintData: Data
    let featureRevision: Int
    let imageWidth: Int
    let imageHeight: Int
}

nonisolated enum IndoorTargetVisionError: Error, LocalizedError {
    case invalidPoint, noForeground, unsupportedMask, emptyMask, imageEncoding, noFeature
    var errorDescription: String? {
        switch self {
        case .invalidPoint: return "请点画面内的物品。"
        case .noForeground: return "没有分出所点物品的轮廓，请换个角度重试。"
        case .unsupportedMask, .emptyMask: return "暂时无法读取物品轮廓，请重试。"
        case .imageEncoding: return "保存物品轮廓失败，请重试。"
        case .noFeature: return "暂时无法提取物品外观，请重试。"
        }
    }
}

nonisolated struct IndoorTargetComparisonContext: Sendable {
    /// These are evidence supplied by the caller, never assumed by the matcher.
    var comparableView = false
    var originalRegionVisible = false
    var imageQualityAcceptable = false
    var occlusionRuledOut = false
}

nonisolated enum IndoorTargetAssessment: Equatable, Sendable {
    case appearanceConsistent
    case originalLocationNotObserved
    case unableToConfirm(String)
}

nonisolated struct IndoorTargetComparison: Sendable {
    let assessment: IndoorTargetAssessment
    let featureDistance: Float?
}

/// No production defaults: callers must provide thresholds supported by a held-out
/// same-object / similar-other-object evaluation, not a guessed feature distance.
nonisolated struct IndoorTargetCalibration: Sendable {
    let featureRevision: Int
    let maximumSameAppearanceDistance: Float
    let minimumDifferentAppearanceDistance: Float
    let evaluationID: String

    var isValid: Bool {
        !evaluationID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && maximumSameAppearanceDistance.isFinite && maximumSameAppearanceDistance >= 0
            && minimumDifferentAppearanceDistance.isFinite
            && minimumDifferentAppearanceDistance > maximumSameAppearanceDistance
    }
}

nonisolated enum IndoorTargetComparisonPolicy {
    static func assess(distance: Float?, hasCurrentTarget: Bool,
                       context: IndoorTargetComparisonContext,
                       calibration: IndoorTargetCalibration?) -> IndoorTargetAssessment {
        guard context.comparableView, context.originalRegionVisible,
              context.imageQualityAcceptable, context.occlusionRuledOut else {
            return .unableToConfirm("视角、遮挡或画面质量不足，请回到记录时的角度再观察。")
        }
        // Segmentation failure or a missing foreground is NOT evidence of absence.
        guard hasCurrentTarget, let distance, distance.isFinite, distance >= 0 else {
            return .unableToConfirm("本次没有取得可比较的物品轮廓，不能判断是否移走。")
        }
        guard let calibration, calibration.isValid else {
            return .unableToConfirm("已取得对比画面；外观匹配尚未完成实测校准。")
        }
        if distance <= calibration.maximumSameAppearanceDistance { return .appearanceConsistent }
        if distance >= calibration.minimumDifferentAppearanceDistance {
            return .unableToConfirm("外观有差异，但不能据此判断原物品已经消失。")
        }
        return .unableToConfirm("物品外观不够确定，请查看前后照片。")
    }
}

/// Synchronous worker API: perform on a serial background queue, never the UI/AR callback.
@available(iOS 17.0, macOS 14.0, *)
nonisolated final class IndoorTargetVision {
    private let context = CIContext()

    func extract(image: CGImage, normalizedTap: CGPoint, requireFeaturePrint: Bool = true) throws -> IndoorTargetEvidence {
        guard normalizedTap.x.isFinite, normalizedTap.y.isFinite,
              (0..<1).contains(normalizedTap.x), (0..<1).contains(normalizedTap.y) else {
            throw IndoorTargetVisionError.invalidPoint
        }
        let handler = VNImageRequestHandler(cgImage: image, orientation: .up)
        let request = VNGenerateForegroundInstanceMaskRequest()
        try handler.perform([request])
        guard let observation = request.results?.first else { throw IndoorTargetVisionError.noForeground }
        let labels = observation.instanceMask
        guard CVPixelBufferGetPixelFormatType(labels) == kCVPixelFormatType_OneComponent8 else {
            throw IndoorTargetVisionError.unsupportedMask
        }
        CVPixelBufferLockBaseAddress(labels, .readOnly)
        let instance: Int
        if let base = CVPixelBufferGetBaseAddress(labels) {
            let x = min(CVPixelBufferGetWidth(labels) - 1, Int(normalizedTap.x * CGFloat(CVPixelBufferGetWidth(labels))))
            let y = min(CVPixelBufferGetHeight(labels) - 1, Int(normalizedTap.y * CGFloat(CVPixelBufferGetHeight(labels))))
            instance = Int(base.assumingMemoryBound(to: UInt8.self)[y * CVPixelBufferGetBytesPerRow(labels) + x])
        } else { instance = 0 }
        CVPixelBufferUnlockBaseAddress(labels, .readOnly)
        guard instance != 0, observation.allInstances.contains(instance) else {
            throw IndoorTargetVisionError.noForeground
        }
        let selected = IndexSet(integer: instance)
        let mask = try observation.generateMask(forInstances: selected)
        let bounds = try Self.bounds(mask: mask)
        let scaledMask = try observation.generateScaledMaskForImage(forInstances: selected, from: handler)
        let maskImage = CIImage(cvPixelBuffer: scaledMask)
        guard let maskCG = context.createCGImage(maskImage, from: maskImage.extent) else {
            throw IndoorTargetVisionError.imageEncoding
        }
        let masked = try observation.generateMaskedImage(ofInstances: selected, from: handler, croppedToInstancesExtent: true)
        let featureRequest = VNGenerateImageFeaturePrintRequest()
        do { try VNImageRequestHandler(cvPixelBuffer: masked, orientation: .up).perform([featureRequest]) }
        catch { if requireFeaturePrint { throw error } }
        let feature = featureRequest.results?.first
        if requireFeaturePrint && feature == nil { throw IndoorTargetVisionError.noFeature }
        return IndoorTargetEvidence(maskPNG: try Self.png(maskCG), normalizedBounds: bounds,
                                    featurePrintData: try feature.map { try NSKeyedArchiver.archivedData(withRootObject: $0, requiringSecureCoding: true) } ?? Data(),
                                    featureRevision: feature?.requestRevision ?? 0,
                                    imageWidth: image.width, imageHeight: image.height)
    }

    func compare(reference: IndoorTargetEvidence, current: IndoorTargetEvidence?,
                 context: IndoorTargetComparisonContext,
                 calibration: IndoorTargetCalibration? = nil) throws -> IndoorTargetComparison {
        guard let current else {
            return IndoorTargetComparison(assessment: IndoorTargetComparisonPolicy.assess(distance: nil, hasCurrentTarget: false, context: context, calibration: calibration), featureDistance: nil)
        }
        guard reference.featureRevision == current.featureRevision,
              calibration == nil || calibration?.featureRevision == reference.featureRevision else {
            return IndoorTargetComparison(assessment: .unableToConfirm("两次记录使用了不同的外观特征版本，请重新记录。"), featureDistance: nil)
        }
        guard let lhs = try NSKeyedUnarchiver.unarchivedObject(ofClass: VNFeaturePrintObservation.self, from: reference.featurePrintData),
              let rhs = try NSKeyedUnarchiver.unarchivedObject(ofClass: VNFeaturePrintObservation.self, from: current.featurePrintData) else {
            throw IndoorTargetVisionError.noFeature
        }
        var distance: Float = 0
        try lhs.computeDistance(&distance, to: rhs)
        return IndoorTargetComparison(assessment: IndoorTargetComparisonPolicy.assess(distance: distance, hasCurrentTarget: true, context: context, calibration: calibration), featureDistance: distance)
    }

    private static func png(_ image: CGImage) throws -> Data {
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else {
            throw IndoorTargetVisionError.imageEncoding
        }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { throw IndoorTargetVisionError.imageEncoding }
        return data as Data
    }

    private static func bounds(mask: CVPixelBuffer) throws -> CGRect {
        guard CVPixelBufferGetPixelFormatType(mask) == kCVPixelFormatType_OneComponent32Float else {
            throw IndoorTargetVisionError.unsupportedMask
        }
        CVPixelBufferLockBaseAddress(mask, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(mask, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(mask) else { throw IndoorTargetVisionError.emptyMask }
        let width = CVPixelBufferGetWidth(mask), height = CVPixelBufferGetHeight(mask)
        var minX = width, minY = height, maxX = -1, maxY = -1
        for y in 0..<height {
            let row = base.advanced(by: y * CVPixelBufferGetBytesPerRow(mask)).assumingMemoryBound(to: Float.self)
            for x in 0..<width where row[x] > 0.5 {
                minX = min(minX, x); maxX = max(maxX, x)
                minY = min(minY, y); maxY = max(maxY, y)
            }
        }
        guard maxX >= minX, maxY >= minY else { throw IndoorTargetVisionError.emptyMask }
        return CGRect(x: CGFloat(minX) / CGFloat(width), y: CGFloat(minY) / CGFloat(height),
                      width: CGFloat(maxX - minX + 1) / CGFloat(width), height: CGFloat(maxY - minY + 1) / CGFloat(height))
    }
}
