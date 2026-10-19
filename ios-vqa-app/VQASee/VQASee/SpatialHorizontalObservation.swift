import Foundation

/// Raw observed horizontal boundaries; never inferred room bounds or confirmed floor.
nonisolated enum SpatialHorizontalObservation {
    static func surfaces(_ surfaces: [SpatialSurface]) -> [SpatialSurface] {
        surfaces.filter { $0.observed && $0.kind == "horizontal" && $0.vertices.count >= 3 && $0.vertices.allSatisfy(\.isFinite) }
    }

    /// Ear clipping in XZ preserves concave boundaries instead of filling their convex hull.
    /// Failure leaves the raw boundary available to both renderers.
    static func triangles(_ vertices: [SpatialPoint]) -> [Int32]? {
        guard vertices.count >= 3, vertices.allSatisfy(\.isFinite) else { return nil }
        var ring = Array(vertices.indices)
        func cross(_ a: Int, _ b: Int, _ c: Int) -> Double {
            let p = vertices[a], q = vertices[b], r = vertices[c]
            return (Double(q.x)-Double(p.x))*(Double(r.z)-Double(p.z)) - (Double(q.z)-Double(p.z))*(Double(r.x)-Double(p.x))
        }
        // A repeated closing vertex and straight boundary samples do not change the polygon.
        var changed = true
        while changed && ring.count > 3 {
            changed = false
            for i in ring.indices {
                let a = ring[(i+ring.count-1)%ring.count], b = ring[i], c = ring[(i+1)%ring.count]
                if abs(cross(a,b,c)) < 1e-10 {
                    let p = vertices[a], q = vertices[b], r = vertices[c]
                    let between = (Double(q.x)-Double(p.x))*(Double(q.x)-Double(r.x)) + (Double(q.z)-Double(p.z))*(Double(q.z)-Double(r.z)) <= 1e-10
                    if between { ring.remove(at: i); changed = true; break }
                }
            }
        }
        func onSegment(_ a: Int, _ b: Int, _ p: Int) -> Bool {
            let x = Double(vertices[p].x), z = Double(vertices[p].z)
            return x >= min(Double(vertices[a].x),Double(vertices[b].x))-1e-10 && x <= max(Double(vertices[a].x),Double(vertices[b].x))+1e-10
                && z >= min(Double(vertices[a].z),Double(vertices[b].z))-1e-10 && z <= max(Double(vertices[a].z),Double(vertices[b].z))+1e-10
        }
        for i in ring.indices {
            for j in ring.indices where j > i {
                if j == i+1 || (i == 0 && j == ring.count-1) { continue }
                let a = ring[i], b = ring[(i+1)%ring.count], c = ring[j], d = ring[(j+1)%ring.count]
                let abC = cross(a,b,c), abD = cross(a,b,d), cdA = cross(c,d,a), cdB = cross(c,d,b)
                if (abC*abD < 0 && cdA*cdB < 0)
                    || (abs(abC) <= 1e-10 && onSegment(a,b,c)) || (abs(abD) <= 1e-10 && onSegment(a,b,d))
                    || (abs(cdA) <= 1e-10 && onSegment(c,d,a)) || (abs(cdB) <= 1e-10 && onSegment(c,d,b)) { return nil }
            }
        }
        let area = ring.indices.reduce(0.0) { sum, i in
            let a = vertices[ring[i]], b = vertices[ring[(i+1)%ring.count]]
            return sum + Double(a.x)*Double(b.z)-Double(b.x)*Double(a.z)
        }
        guard abs(area) > 1e-10 else { return nil }
        let sign = area > 0 ? 1.0 : -1.0
        var output: [Int32] = []
        while ring.count > 3 {
            var clipped = false
            for i in ring.indices {
                let a = ring[(i+ring.count-1)%ring.count], b = ring[i], c = ring[(i+1)%ring.count]
                guard cross(a,b,c)*sign > 1e-10 else { continue }
                let occupied = ring.contains { p in
                    p != a && p != b && p != c && cross(a,b,p)*sign >= -1e-10 && cross(b,c,p)*sign >= -1e-10 && cross(c,a,p)*sign >= -1e-10
                }
                if !occupied {
                    output += [Int32(a),Int32(b),Int32(c)]
                    ring.remove(at: i); clipped = true; break
                }
            }
            guard clipped else { return nil }
        }
        guard cross(ring[0],ring[1],ring[2])*sign > 1e-10 else { return nil }
        return output + ring.map(Int32.init)
    }
}
