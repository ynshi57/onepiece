#if DEBUG
import simd
import UIKit

/// Derived inspection images only. Native photo coordinates match archived intrinsics.
@MainActor enum SpatialStructureP0Renderer {
    static func top(map: SpatialStructureMap, document:SpatialScanDocument, output:URL) throws {
        let surfaces=document.surfaces.filter { $0.observed && $0.epoch == map.epoch }
        let regionPoints=map.floorRegions.flatMap {$0.outerRing + $0.holes.flatMap {$0}}
        let points=surfaces.flatMap(\.vertices).filter(\.isFinite)+map.corners.map(\.position)+regionPoints
        let minX=points.map(\.x).min() ?? -1,maxX=points.map(\.x).max() ?? 1
        let minZ=points.map(\.z).min() ?? -1,maxZ=points.map(\.z).max() ?? 1
        let size=CGSize(width:1024,height:768)
        let scale=min(880/CGFloat(max(1,maxX-minX)),540/CGFloat(max(1,maxZ-minZ)))
        func point(_ p:SpatialPoint)->CGPoint {CGPoint(x:512+CGFloat(p.x-(minX+maxX)/2)*scale,y:410-CGFloat(p.z-(minZ+maxZ)/2)*scale)}
        let format=UIGraphicsImageRendererFormat();format.scale=1;format.opaque=true
        let image=UIGraphicsImageRenderer(size:size,format:format).image { c in
            UIColor.systemBackground.setFill();c.fill(CGRect(origin:.zero,size:size))
            let circuit = map.quality.observedCoverageLine
            let text = "结构未完成 · revision \(map.revision) · 尚未独立测量\n墙段 \(map.wallSegments.count) · 地面闭环 \(map.floorRegions.count) · \(circuit) · 灰线仅原始面片，非房间轮廓"
            (text as NSString).draw(in:CGRect(x:24,y:18,width:976,height:78),withAttributes:[.font:UIFont.systemFont(ofSize:21),.foregroundColor:UIColor.label])
            c.cgContext.setStrokeColor(UIColor.systemGray.cgColor);c.cgContext.setLineWidth(1);c.cgContext.setLineDash(phase:0,lengths:[5,5])
            for s in surfaces where s.vertices.count>=3 {
                c.cgContext.beginPath();c.cgContext.move(to:point(s.vertices[0]));for p in s.vertices.dropFirst(){c.cgContext.addLine(to:point(p))};c.cgContext.closePath();c.cgContext.strokePath()
            }
            c.cgContext.setStrokeColor(UIColor.systemTeal.cgColor);c.cgContext.setLineWidth(3)
            c.cgContext.setLineDash(phase:0,lengths:[7,5])
            for region in map.floorRegions {
                for ring in [region.outerRing]+region.holes where ring.count>=3 {
                    c.cgContext.beginPath();c.cgContext.move(to:point(ring[0]))
                    for vertex in ring.dropFirst() {c.cgContext.addLine(to:point(vertex))}
                    c.cgContext.closePath();c.cgContext.strokePath()
                }
            }
            c.cgContext.setStrokeColor(UIColor.systemOrange.cgColor);c.cgContext.setLineWidth(3)
            for wall in map.wallSegments {
                c.cgContext.move(to:point(wall.start));c.cgContext.addLine(to:point(wall.end));c.cgContext.strokePath()
                let middle=SpatialPoint(x:(wall.start.x+wall.end.x)/2,y:(wall.start.y+wall.end.y)/2,z:(wall.start.z+wall.end.z)/2)
                let p=point(middle)
                (String(format:"候选跨度 %.2fm",wall.lengthMeters) as NSString).draw(at:p,withAttributes:[.font:UIFont.systemFont(ofSize:15),.foregroundColor:UIColor.systemOrange])
            }
            c.cgContext.setStrokeColor(UIColor.systemRed.cgColor);c.cgContext.setLineWidth(3)
            c.cgContext.setLineDash(phase:0,lengths:[3,4])
            for gap in map.gaps where simd_distance(SIMD3(gap.start.x,0,gap.start.z),SIMD3(gap.end.x,0,gap.end.z))>0.001 {
                c.cgContext.move(to:point(gap.start));c.cgContext.addLine(to:point(gap.end));c.cgContext.strokePath()
            }
            c.cgContext.setLineDash(phase:0,lengths:[])
            for corner in map.corners {
                let p=point(corner.position);c.cgContext.strokeEllipse(in:CGRect(x:p.x-7,y:p.y-7,width:14,height:14))
            }
            (map.quality.message as NSString).draw(in:CGRect(x:24,y:708,width:976,height:54),withAttributes:[.font:UIFont.systemFont(ofSize:16),.foregroundColor:UIColor.secondaryLabel])
        }
        guard let data=image.pngData() else {throw CocoaError(.fileWriteUnknown)}
        try data.write(to:output,options:.atomic)
    }
}
#endif
