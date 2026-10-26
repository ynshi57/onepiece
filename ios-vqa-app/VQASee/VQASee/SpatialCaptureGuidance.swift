import Foundation
import simd

/// Live scan coach. Uses tracking, motion and ARKit plane growth. Never gates capture or structure.
nonisolated struct SpatialCaptureGuidance {
    enum Tracking: Equatable {
        case normal, unavailable, excessiveMotion, insufficientFeatures, initializing, relocalizing
        var isNormal: Bool { self == .normal }
    }

    enum State: Equatable {
        case start, slowDown, holdWall, wallSettled, turnLeft, turnRight, avoidFurniture, rescanWall
        case circuitComplete
        case trackingLimited(Tracking), trackingUnavailable
        var message: String {
            switch self {
            case .start: return "对着一面墙停一下，然后沿墙向左转"
            case .slowDown: return "慢一点"
            case .holdWall: return "停一下"
            case .wallSettled: return "可以了，继续向左转"
            case .turnLeft: return "继续向左转"
            case .turnRight: return "继续向右转"
            case .avoidFurniture: return "这是家具，继续转"
            case .rescanWall: return "继续沿墙转"
            case .circuitComplete: return "已经转过一圈；图上是目前看到的墙，开口表示还没看全"
            case .trackingLimited(.excessiveMotion): return "移动过快，请放慢"
            case .trackingLimited(.insufficientFeatures): return "对准墙角或有花纹的地方"
            case .trackingLimited(.initializing): return "正在建立定位，请缓慢移动"
            case .trackingLimited(.relocalizing): return "定位丢了，对准有花纹的墙角"
            case .trackingLimited, .trackingUnavailable: return "等待定位恢复"
            }
        }
        var eventName: String {
            switch self {
            case .trackingLimited(let reason): return "guidance:trackingLimited:\(reason)"
            default: return "guidance:\(self)"
            }
        }
    }

    struct Plane: Equatable {
        var id: String
        var kind: String
        var height: Float
        var centroidX: Float
        var centroidZ: Float
        var extent: Float
        var inView: Bool
    }

    struct Observation: Equatable {
        var time: Double
        var pose: SpatialPose
        var tracking: Tracking
        var planes: [Plane]
    }

    private var previous: SpatialPose?
    private var lastTime: Double?
    private var extents: [String: Float] = [:]
    private var holdID: String?
    private var holdQuietSince: Double?
    private var settledIDs = Set<String>()
    private var settledSectors = Set<Int>()
    private var originYaw: Float?
    private var integratedYaw: Float = 0
    /// -1 = left, +1 = right. Locked after the first settled wall; never reverses.
    private var sweepSign: Float?
    private var state: State = .start

    mutating func reset() {
        previous = nil; lastTime = nil; extents = [:]; holdID = nil; holdQuietSince = nil
        settledIDs = []; settledSectors = []
        originYaw = nil; integratedYaw = 0; sweepSign = nil; state = .start
    }

    mutating func update(_ observation: Observation) -> State {
        let values = [observation.pose.position.x, observation.pose.position.y, observation.pose.position.z,
                      observation.pose.forward.x, observation.pose.forward.y, observation.pose.forward.z]
        guard observation.time.isFinite, values.allSatisfy(\.isFinite) else {
            state = .trackingUnavailable
            return state
        }
        if !observation.tracking.isNormal {
            remember(observation, accumulateYaw: false)
            state = observation.tracking == .unavailable ? .trackingUnavailable : .trackingLimited(observation.tracking)
            return state
        }
        if let last = previous, let t0 = lastTime, observation.pose.epoch == last.epoch,
           observation.time > t0, observation.time - t0 <= 1 {
            let dt = Float(observation.time - t0)
            if dt > 0 {
                let step = SpatialScanGeometry.distance(last.position, observation.pose.position)
                if step / dt > 0.6 {
                    remember(observation, accumulateYaw: true)
                    updateExtents(observation.planes)
                    state = .slowDown
                    return state
                }
                if abs(Self.yawDelta(Self.yaw(last.forward), Self.yaw(observation.pose.forward))) / dt > 1.2 {
                    remember(observation, accumulateYaw: true)
                    updateExtents(observation.planes)
                    state = .slowDown
                    return state
                }
            }
        }
        remember(observation, accumulateYaw: true)
        defer { updateExtents(observation.planes) }
        let inView = observation.planes.filter(\.inView)
        let tall = inView.filter { $0.kind == "vertical" && $0.height >= SpatialPlaneGrouping.tallHeight }
        if let wall = tall.max(by: { $0.extent < $1.extent }) {
            let growing = isGrowing(wall)
            let alreadySettled = settledIDs.contains(wall.id)
            if !alreadySettled || growing {
                if growing {
                    holdID = wall.id; holdQuietSince = nil; state = .holdWall; return state
                }
                if holdID != wall.id { holdID = wall.id; holdQuietSince = observation.time }
                else if holdQuietSince == nil { holdQuietSince = observation.time }
                if let started = holdQuietSince, observation.time - started >= 2 {
                    markSettled(wall, pose: observation.pose)
                    holdID = nil; holdQuietSince = nil
                    if hasCompletedCircuit(observation.pose) { state = .circuitComplete; return state }
                    state = continueSweep()
                    return state
                }
                state = .holdWall
                return state
            }
        }
        holdID = nil; holdQuietSince = nil
        if hasCompletedCircuit(observation.pose) { state = .circuitComplete; return state }
        let shortVertical = inView.contains { $0.kind == "vertical" && $0.height < SpatialPlaneGrouping.tallHeight }
        let furnitureHorizontal = inView.contains { $0.kind == "horizontal" && $0.height < 0.2 }
        if tall.isEmpty, shortVertical || furnitureHorizontal, sweepSign != nil {
            state = .avoidFurniture
            return state
        }
        if !settledIDs.isEmpty { state = continueSweep(); return state }
        state = .start
        return state
    }

    func completionMessage() -> String {
        if hasCompletedCircuit(previous) { return "已经转过一圈；图上是目前看到的墙，开口表示还没看全" }
        let n = max(settledIDs.count, settledSectors.count)
        if n == 0 { return "这次还没有停够的墙，线框仅表示已观测范围" }
        return "大约 \(n) 面墙停够了，请继续沿墙转完一圈"
    }

    private mutating func remember(_ observation: Observation, accumulateYaw: Bool) {
        if accumulateYaw, let last = previous, observation.pose.epoch == last.epoch {
            var delta = Self.yawDelta(Self.yaw(last.forward), Self.yaw(observation.pose.forward))
            if abs(delta) > 0.8 { delta = 0 }
            integratedYaw += delta
        }
        previous = observation.pose
        lastTime = observation.time
    }

    private mutating func updateExtents(_ planes: [Plane]) {
        let present = Set(planes.map(\.id))
        for plane in planes { extents[plane.id] = plane.extent }
        extents = extents.filter { present.contains($0.key) }
    }

    private func isGrowing(_ plane: Plane) -> Bool {
        guard let old = extents[plane.id] else { return true }
        return plane.extent - old > 0.05
    }

    private mutating func markSettled(_ wall: Plane, pose: SpatialPose) {
        settledIDs.insert(wall.id)
        settledSectors.insert(Self.sector(Self.yaw(pose.forward)))
        if originYaw == nil { originYaw = Self.yaw(pose.forward) }
        if sweepSign == nil { sweepSign = -1 }
    }

    private func hasCompletedCircuit(_ pose: SpatialPose?) -> Bool {
        guard let pose, let origin = originYaw, settledIDs.count >= 2 else { return false }
        let home = abs(Self.yawDelta(Self.yaw(pose.forward), origin)) < 0.5
        return abs(integratedYaw) >= 5.2 && home
    }

    private func continueSweep() -> State {
        (sweepSign ?? -1) < 0 ? .turnLeft : .turnRight
    }

    static func yaw(_ forward: SpatialPoint) -> Float { atan2(forward.x, forward.z) }

    static func yawDelta(_ from: Float, _ to: Float) -> Float {
        var value = to - from
        let turn = Float.pi * 2
        while value > Float.pi { value -= turn }
        while value < -Float.pi { value += turn }
        return value
    }

    static func sector(_ yaw: Float) -> Int {
        var value = yaw + Float.pi
        let turn = Float.pi * 2
        if value < 0 { value += turn }
        if value >= turn { value -= turn }
        return min(7, Int(value / (turn / 8)))
    }
}
