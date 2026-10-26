import Foundation
import simd

/// Samples SegFormer along a wall–floor line. Whole-surface votes stay unknown when a plane mixes wall and furniture;
/// this publishes only the intervals that still pass the same 0.7 / two-frame gate locally.
nonisolated enum SpatialStructureWallLineSupport {
    static let liftMeters: Float = 0.15
    static let binMeters: Float = SpatialPlaneGrouping.offsetLimit
    static let minimumScore = SpatialSurfaceSemanticEvidence.minimumScore
    static let minimumAgreement = SpatialSurfaceSemanticEvidence.minimumAgreement
    static let minimumFrames = 2

    struct Interval: Sendable, Equatable {
        var lo: Float
        var hi: Float
        var frameIDs: [String]
    }

    struct View: Sendable {
        var frameID: String
        var camera: SpatialObservationGeometry.PreparedCamera
        var wallLabel: UInt8
        var floorLabel: UInt8
        var width: Int
        var height: Int
        var labels: Data
        var confidence: Data
    }

    static func views(frames: [SpatialKeyframe], rows: [SpatialIndoorSemanticObservation]) -> [View] {
        let byID = Dictionary(uniqueKeysWithValues: frames.map {($0.id,$0)})
        return rows.compactMap { row in
            guard let frame = byID[row.frameID], SpatialVisibleFloorProjection.validFrame(frame),
                  let camera = SpatialObservationGeometry.prepareCamera(frame:frame),
                  let taxonomy = row.taxonomy, let mask = row.mask,
                  mask.labels.count == row.width*row.height, row.labels.count == row.width*row.height,
                  row.confidence.count == row.width*row.height,
                  taxonomy.wallLabelID>=0, taxonomy.wallLabelID<256,
                  taxonomy.floorLabelID>=0, taxonomy.floorLabelID<256 else {return nil}
            return View(frameID:row.frameID,camera:camera,wallLabel:UInt8(taxonomy.wallLabelID),floorLabel:UInt8(taxonomy.floorLabelID),
                        width:row.width,height:row.height,labels:row.labels,confidence:row.confidence)
        }
    }

    /// `lo`/`hi` are parameters on `origin + t * direction`. Empty means no locally confirmed wall interval.
    static func intervals(origin: SIMD3<Float>, direction: SIMD3<Float>, up: SIMD3<Float>, lo: Float, hi: Float, views: [View]) -> [Interval] {
        guard hi-lo>binMeters, simd_length(direction)>0.000001, simd_length(up)>0.000001, origin.x.isFinite else {return []}
        let step=binMeters, dir=simd_normalize(direction), lift=simd_normalize(up)*liftMeters
        var bins:[(lo:Float,hi:Float,wall:Set<String>,other:Set<String>)]=[]
        var t=lo
        while t<hi {
            let t1=min(hi,t+step)
            if t1-t>0.02 {
                let point=origin+dir*((t+t1)/2)+lift
                var wall=Set<String>(), other=Set<String>()
                for view in views {
                    guard let projection=SpatialObservationGeometry.project(position:SpatialPoint(x:point.x,y:point.y,z:point.z),camera:view.camera),
                          projection.pixel.count==2, projection.pixel.allSatisfy({$0.isFinite && $0>=0 && $0<=1}),
                          projection.depth>0, projection.depth<=SpatialObservationGeometry.maximumDepthMeters else {continue}
                    let x=min(view.width-1,max(0,Int(projection.pixel[0]*Float(view.width))))
                    let y=min(view.height-1,max(0,Int(projection.pixel[1]*Float(view.height))))
                    let index=y*view.width+x
                    guard index<view.labels.count, index<view.confidence.count else {continue}
                    if Float(view.confidence[index])/255<minimumScore {continue}
                    let label=view.labels[index]
                    if label==view.wallLabel {wall.insert(view.frameID)}
                    else if label != view.floorLabel {other.insert(view.frameID)}
                }
                bins.append((t,t1,wall,other))
            }
            t=t1
        }
        var result:[Interval]=[]
        var current:Interval?
        func flush() {if let value=current,value.hi-value.lo>binMeters {result.append(value)};current=nil}
        for bin in bins {
            let decisive=bin.wall.count+bin.other.count
            let isWall=bin.wall.count>=minimumFrames && decisive>0 && Float(bin.wall.count)/Float(decisive)>=minimumAgreement
            if isWall {
                let ids=bin.wall.sorted()
                if var open=current {
                    open.hi=bin.hi; open.frameIDs=Array(Set(open.frameIDs+ids)).sorted(); current=open
                } else {current=Interval(lo:bin.lo,hi:bin.hi,frameIDs:ids)}
            } else {flush()}
        }
        flush()
        return result
    }
}
