import Foundation
import CoreGraphics
import ImageIO
import MetalKit

/// A cost-bounded cache for active material textures, not just an evictable
/// cache beside unbounded strong UIImage references in scene materials.
nonisolated final class SpatialDecodedTextureCache: @unchecked Sendable {
    enum Outcome: @unchecked Sendable { case ready(MTLTexture, cost: Int); case unknown(String) }
    typealias Completion = @Sendable (Outcome) -> Void
    private struct Entry { let image: MTLTexture; let cost: Int }
    private let device = MTLCreateSystemDefaultDevice()
    private let lock = NSLock()
    private let queue = DispatchQueue(label:"vqasee.spatial.texture.decode",qos:.userInitiated)
    let maximumDecodedBytes: Int
    let maximumPixelSize: Int
    private var scope = UUID(), desired = Set<String>()
    private var entries: [String:Entry] = [:], pending: [String:[Completion]] = [:]
    private var cost = 0, peakCost = 0, rejected = 0, failed = 0, superseded = 0
    private var decodeCount = 0, mainThreadDecodes = 0

    init(maximumDecodedBytes:Int=64*1_024*1_024, maximumPixelSize:Int=1024) {
        self.maximumDecodedBytes=maximumDecodedBytes; self.maximumPixelSize=maximumPixelSize
    }
    static func key(name:String,directory:URL) -> String { directory.appendingPathComponent(name).standardizedFileURL.path }
    @discardableResult func beginScope(names:Set<String>,directory:URL) -> UUID {
        let keys=Set(names.map {Self.key(name:$0,directory:directory)})
        lock.lock()
        let callbacks=pending.values.flatMap {$0};superseded += callbacks.count;pending.removeAll()
        scope=UUID();desired=keys
        var discarded:[Entry]=[]
        for key in Array(entries.keys) where !keys.contains(key) {
            if let entry=entries.removeValue(forKey:key) {cost -= entry.cost;discarded.append(entry)}
        }
        let generation=scope
        lock.unlock()
        // Release removed decoded images off the main thread.
        queue.async { _ = discarded.count }
        for callback in callbacks {callback(.unknown("request_superseded"))}
        return generation
    }
    func request(name:String,directory:URL,scope requestScope:UUID,completion:@escaping Completion) {
        let key=Self.key(name:name,directory:directory)
        lock.lock()
        guard requestScope==scope,desired.contains(key) else {lock.unlock();completion(.unknown("request_superseded"));return}
        if let entry=entries[key] {lock.unlock();completion(.ready(entry.image,cost:entry.cost));return}
        if pending[key] != nil {pending[key]?.append(completion);lock.unlock();return}
        pending[key]=[completion]
        lock.unlock()
        queue.async { [self] in
            autoreleasepool {
                lock.lock();let current=requestScope==scope && desired.contains(key);lock.unlock()
                guard current else {return}
                guard !name.contains("/"),!name.contains("\\"),name.lowercased().hasSuffix(".jpg"),
                      let source=CGImageSourceCreateWithURL(URL(fileURLWithPath:key) as CFURL,[kCGImageSourceShouldCache:false] as CFDictionary),
                      let props=CGImageSourceCopyPropertiesAtIndex(source,0,nil) as? [CFString:Any],
                      let width=props[kCGImagePropertyPixelWidth] as? Int,let height=props[kCGImagePropertyPixelHeight] as? Int,width>0,height>0 else {
                    finish(key:key,scope:requestScope,result:.unknown("image_unreadable"));return
                }
                let ratio=min(1,Double(maximumPixelSize)/Double(max(width,height)))
                let estimate=max(1,Int(ceil(Double(width)*ratio)))*max(1,Int(ceil(Double(height)*ratio)))*4
                lock.lock();let fits=estimate<=maximumDecodedBytes-cost;lock.unlock()
                guard fits else {finish(key:key,scope:requestScope,result:.unknown("decoded_texture_budget"));return}
                let options:[CFString:Any]=[kCGImageSourceCreateThumbnailFromImageAlways:true,kCGImageSourceThumbnailMaxPixelSize:maximumPixelSize,kCGImageSourceShouldCacheImmediately:true,kCGImageSourceCreateThumbnailWithTransform:false]
                guard let image=CGImageSourceCreateThumbnailAtIndex(source,0,options as CFDictionary) else {
                    finish(key:key,scope:requestScope,result:.unknown("image_decode_failed"));return
                }
                // JPEG decoders may expose RGB/CMYK layouts. Normalize once on the decode queue
                // before GPU upload; no decoded image is retained by the cache.
                guard let rgba=CGContext(data:nil,width:image.width,height:image.height,bitsPerComponent:8,bytesPerRow:image.width*4,space:CGColorSpace(name:CGColorSpace.sRGB)!,bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue) else {
                    finish(key:key,scope:requestScope,result:.unknown("rgba_conversion_failed"));return
                }
                rgba.draw(image,in:CGRect(x:0,y:0,width:image.width,height:image.height))
                guard let normalized=rgba.makeImage() else {finish(key:key,scope:requestScope,result:.unknown("rgba_conversion_failed"));return}
                guard let device else {finish(key:key,scope:requestScope,result:.unknown("metal_unavailable"));return}
                let texture: MTLTexture
                do {
                    texture = try MTKTextureLoader(device:device).newTexture(cgImage:normalized,options:[
                        .SRGB:true,.origin:MTKTextureLoader.Origin.topLeft,.generateMipmaps:false,
                        .textureUsage:NSNumber(value:MTLTextureUsage.shaderRead.rawValue)])
                } catch {finish(key:key,scope:requestScope,result:.unknown("gpu_texture_upload_failed"));return}
                // Account decoded RGBA and actual allocation conservatively.
                // Only the immutable GPU texture survives this autoreleasepool.
                let bytes=max(max(image.bytesPerRow,image.width*4)*image.height,texture.allocatedSize)
                lock.lock();decodeCount += 1;if Thread.isMainThread {mainThreadDecodes += 1};lock.unlock()
                finish(key:key,scope:requestScope,result:.ready(texture,cost:bytes))
            }
        }
    }
    private func finish(key:String,scope requestScope:UUID,result:Outcome) {
        lock.lock()
        guard requestScope==scope else {lock.unlock();return}
        let callbacks=pending.removeValue(forKey:key) ?? []
        let delivered:Outcome
        switch result {
        case let .ready(image,bytes):
            if bytes<=maximumDecodedBytes-cost {
                entries[key]=Entry(image:image,cost:bytes);cost += bytes;peakCost=max(peakCost,cost);delivered=result
            } else {rejected += 1;delivered = .unknown("decoded_texture_budget")}
        case let .unknown(reason):
            if reason=="decoded_texture_budget" {rejected += 1}else {failed += 1};delivered=result
        }
        lock.unlock()
        for callback in callbacks {callback(delivered)}
    }
    func snapshot() -> [String:Int] {
        lock.lock();defer {lock.unlock()}
        return ["budgetBytes":maximumDecodedBytes,"decodedBytes":cost,"peakDecodedBytes":peakCost,
                "maximumTextureDimension":maximumPixelSize,"entries":entries.count,"pending":pending.count,
                "budgetUnknown":rejected,"decodeFailures":failed,"supersededCallbacks":superseded,
                "decodeCount":decodeCount,"decodesOnMainThread":mainThreadDecodes]
    }
}
