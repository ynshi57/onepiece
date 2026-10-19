import Foundation
import simd
import CoreGraphics
import ImageIO
import Combine

/// Reads saved keyframes off the camera callback and keeps only a floor edge that touches a measured wall.
nonisolated enum SpatialWallFootFinder {
    private static let sampleStep: Float = 0.1
    private static let edgeContrast: Float = 18
    private static let lookAhead: Float = 6

    static func contacts(gray: [UInt8], width: Int, height: Int, camera: SpatialProjectiveTexturing.Camera, floorY: Float, walls: [SpatialPlaneGrouping.WallLine]) -> [SpatialPlaneGrouping.FloorContact] {
        guard width > 16, height > 16, gray.count == width * height else { return [] }
        let cameraPoint = SpatialPoint(x: camera.position.x, y: camera.position.y, z: camera.position.z)
        var found: [SpatialPlaneGrouping.FloorContact] = []
        for wall in walls where wall.role == "wall" {
            let origin = SIMD2(wall.start.x, wall.start.z)
            let delta = SIMD2(wall.end.x - wall.start.x, wall.end.z - wall.start.z)
            let span = simd_length(delta)
            guard span > 0.05 else { continue }
            let direction = delta / span
            var along = -lookAhead
            while along <= span + lookAhead {
                let xz = origin + direction * along
                let world = SIMD3(xz.x, floorY, xz.y)
                if hypot(world.x - camera.position.x, world.z - camera.position.z) <= lookAhead,
                   let projection = camera.project(world),
                   edgeOnFloorLine(gray, width: width, height: height, camera: camera, at: projection, world: world, direction: direction) {
                    found.append(SpatialPlaneGrouping.FloorContact(
                        point: SpatialPoint(x: world.x, y: floorY, z: world.z),
                        camera: cameraPoint))
                }
                along += sampleStep
            }
        }
        return found
    }

    static func grayPixels(from image: CGImage) -> (pixels: [UInt8], width: Int, height: Int)? {
        let width = image.width
        let height = image.height
        guard width > 0, height > 0 else { return nil }
        var pixels = [UInt8](repeating: 0, count: width * height)
        guard let context = CGContext(
            data: &pixels, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width,
            space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return nil }
        context.translateBy(x: 0, y: CGFloat(height))
        context.scaleBy(x: 1, y: -1)
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        return (pixels, width, height)
    }

    private static func edgeOnFloorLine(_ gray: [UInt8], width: Int, height: Int, camera: SpatialProjectiveTexturing.Camera, at projection: SpatialProjectiveTexturing.Camera.Projection, world: SIMD3<Float>, direction: SIMD2<Float>) -> Bool {
        guard let ahead = camera.project(world + SIMD3(direction.x, 0, direction.y) * 0.3) else { return false }
        let u = projection.x * Float(width)
        let v = projection.y * Float(height)
        var preferred = SIMD2((ahead.x - projection.x) * Float(width), (ahead.y - projection.y) * Float(height))
        let length = simd_length(preferred)
        guard length > 1, u >= 8, v >= 8, u < Float(width - 8), v < Float(height - 8) else { return false }
        preferred /= length
        let across = SIMD2(-preferred.y, preferred.x)
        guard let center = contrast(gray, width: width, height: height, u: u, v: v, across: across, shift: 0) else { return false }
        let side = max(
            contrast(gray, width: width, height: height, u: u, v: v, across: across, shift: 8) ?? 0,
            contrast(gray, width: width, height: height, u: u, v: v, across: across, shift: -8) ?? 0)
        return center >= edgeContrast && center >= side * 2
    }

    private static func contrast(_ gray: [UInt8], width: Int, height: Int, u: Float, v: Float, across: SIMD2<Float>, shift: Float) -> Float? {
        func side(_ sign: Float) -> Float? {
            var total: Float = 0
            var count: Float = 0
            for step in [Float(3), 6] {
                let sample = SIMD2(u, v) + across * (sign * step + shift)
                guard let pixel = pixel(gray, width: width, height: height, x: sample.x, y: sample.y) else { continue }
                total += Float(pixel)
                count += 1
            }
            guard count > 0 else { return nil }
            return total / count
        }
        guard let left = side(-1), let right = side(1) else { return nil }
        return abs(left - right)
    }

    private static func pixel(_ gray: [UInt8], width: Int, height: Int, x: Float, y: Float) -> UInt8? {
        let ix = Int(x.rounded())
        let iy = Int(y.rounded())
        guard ix >= 0, iy >= 0, ix < width, iy < height else { return nil }
        return gray[iy * width + ix]
    }
}

nonisolated struct SpatialWallFootResult {
    var lines: [SpatialPlaneGrouping.WallLine] = []
    var checkedPhotos = 0
    var unreadablePhotos = 0
    var invalidCameras = 0
    var hasReferenceSurfaces = true

    var caption: String {
        guard hasReferenceSurfaces else { return "还缺少墙面或水平面观测，暂不能核对墙根" }
        guard checkedPhotos > 0 else {
            if invalidCameras > 0 && unreadablePhotos == 0 { return "拍摄信息无法使用，请重新扫描" }
            if invalidCameras > 0 { return "照片或拍摄信息无法使用，请重试或重新扫描" }
            return "照片未能核对，请检查照片后重试"
        }
        let outcome = lines.isEmpty ? "已核对照片，未确认墙根延伸" : "照片支持 \(lines.count) 段墙根推测，以虚线显示"
        let failed = unreadablePhotos + invalidCameras
        return failed > 0 ? "\(outcome)；另有 \(failed) 张未能核对，可重试" : outcome
    }
}

@MainActor
final class SpatialWallFootModel: ObservableObject {
    @Published var lines: [SpatialPlaneGrouping.WallLine] = []
    @Published var caption = ""
    private var ticket = 0

    func rebuild(document: SpatialScanDocument, directory: URL, scanning: Bool) {
        ticket += 1
        let current = ticket
        guard !scanning, !document.keyframes.isEmpty else {
            lines = []
            caption = ""
            return
        }
        lines = []
        caption = "正在用照片核对墙根"
        let surfaces = document.surfaces
        let frames = document.keyframes
        Task.detached(priority: .utility) {
            let result = Self.readFeet(frames: frames, directory: directory, surfaces: surfaces)
            await MainActor.run {
                guard current == self.ticket else { return }
                self.lines = result.lines
                self.caption = result.caption
            }
        }
    }

    nonisolated static func readFeet(frames: [SpatialKeyframe], directory: URL, surfaces: [SpatialSurface]) -> SpatialWallFootResult {
        var result = SpatialWallFootResult()
        let walls = SpatialPlaneGrouping.wallLines(surfaces: surfaces).filter { $0.role == "wall" }
        guard !walls.isEmpty,
              let floor = surfaces.filter({ $0.observed && $0.kind == "horizontal" && $0.vertices.count >= 3 }).max(by: { xz($0) < xz($1) }) else {
            result.hasReferenceSurfaces = false
            return result
        }
        let ys = floor.vertices.map(\.y).sorted()
        let floorY = ys[ys.count / 2]
        var contacts: [SpatialPlaneGrouping.FloorContact] = []
        for frame in frames {
            guard let camera = SpatialProjectiveTexturing.Camera(frame) else {
                result.invalidCameras += 1
                continue
            }
            let url = directory.appendingPathComponent(frame.imageFile)
            guard let source = CGImageSourceCreateWithURL(url as CFURL, [kCGImageSourceShouldCache: false] as CFDictionary),
                  let image = CGImageSourceCreateImageAtIndex(source, 0, nil),
                  image.width == frame.width, image.height == frame.height,
                  let gray = SpatialWallFootFinder.grayPixels(from: image) else {
                result.unreadablePhotos += 1
                continue
            }
            result.checkedPhotos += 1
            contacts.append(contentsOf: SpatialWallFootFinder.contacts(
                gray: gray.pixels, width: gray.width, height: gray.height,
                camera: camera, floorY: floorY, walls: walls))
        }
        result.lines = SpatialPlaneGrouping.inferredWallFeet(surfaces: surfaces, contacts: contacts)
        return result
    }

    nonisolated private static func xz(_ surface: SpatialSurface) -> Float {
        let xs = surface.vertices.map(\.x)
        let zs = surface.vertices.map(\.z)
        return ((xs.max() ?? 0) - (xs.min() ?? 0)) * ((zs.max() ?? 0) - (zs.min() ?? 0))
    }
}
