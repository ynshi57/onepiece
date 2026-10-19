#if DEBUG
import Foundation
import UIKit
import ImageIO

/// Local visual inspection only. No source overwrite, upload, geometry mutation or accuracy claim.
@MainActor
enum SpatialIndoorSemanticVerificationRenderer {
    enum Failure: Error { case invalidSource, invalidObservation, invalidOutput }

    /// Draws native-axis wall (purple) / floor (cyan) over the original RGB background.
    /// K/T and the archived JPEG stay untouched; this inspection image keeps native orientation.
    static func render(frame: SpatialKeyframe, row: SpatialIndoorSemanticObservation,
                       directory: URL, outputURL: URL) throws {
        let sourceURL = directory.appendingPathComponent(frame.imageFile)
        guard sourceURL.resolvingSymlinksInPath().standardizedFileURL != outputURL.resolvingSymlinksInPath().standardizedFileURL,
              let source = CGImageSourceCreateWithURL(sourceURL as CFURL, nil),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue == frame.width,
              (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue == frame.height,
              frame.width > 0, frame.height > 0,
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { throw Failure.invalidSource }
        guard row.frameID == frame.id, row.frameTime == frame.time, row.epoch == frame.pose.epoch,
              row.status == .processed, row.failure == nil, row.width == 128, row.height == 128,
              row.labels.count == 128*128, row.confidence.count == 128*128,
              let taxonomy = row.taxonomy, taxonomy.wallLabelID != taxonomy.floorLabelID,
              (0..<150).contains(taxonomy.wallLabelID), (0..<150).contains(taxonomy.floorLabelID),
              row.labels.allSatisfy({ $0 < 150 }) else { throw Failure.invalidObservation }
        var rgba = [UInt8](repeating: 0, count: 128*128*4)
        for (index,label) in row.labels.enumerated() {
            let p = index*4
            if Int(label) == taxonomy.wallLabelID {
                rgba[p] = 176; rgba[p+1] = 64; rgba[p+2] = 224; rgba[p+3] = 110
            } else if Int(label) == taxonomy.floorLabelID {
                rgba[p] = 0; rgba[p+1] = 208; rgba[p+2] = 224; rgba[p+3] = 110
            }
        }
        guard let provider = CGDataProvider(data: Data(rgba) as CFData),
              let mask = CGImage(width: 128,height: 128,bitsPerComponent: 8,bitsPerPixel: 32,
                bytesPerRow: 128*4,space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue),
                provider: provider,decode: nil,shouldInterpolate: false,intent: .defaultIntent) else { throw Failure.invalidObservation }
        let scale = min(1,1280/Double(max(frame.width,frame.height)))
        let size = CGSize(width: max(1,Int((Double(frame.width)*scale).rounded())),
                          height: max(1,Int((Double(frame.height)*scale).rounded())))
        let format = UIGraphicsImageRendererFormat(); format.scale = 1; format.opaque = true
        let output = UIGraphicsImageRenderer(size: size,format: format).image { context in
            let rect = CGRect(origin: .zero,size: size)
            UIImage(cgImage: image).draw(in: rect)
            context.cgContext.interpolationQuality = .none
            UIImage(cgImage: mask).draw(in: rect)
        }
        guard let jpeg = output.jpegData(compressionQuality: 0.92) else { throw Failure.invalidOutput }
        try jpeg.write(to: outputURL,options: [.atomic,.completeFileProtection])
    }
}
#endif
