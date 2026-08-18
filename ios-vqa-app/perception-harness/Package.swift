// swift-tools-version:5.9
import PackageDescription

// Offline macOS evaluation harness for the VQASee on-device perception layer.
//
// It reuses the EXACT shipping Swift perception sources (symlinked from
// ../VQASee/VQASee) so results reflect the real iPhone stack (YOLO11n Core ML +
// LocalPathGuidanceEngine), not a reimplementation. ARKit/LiDAR depth is absent
// on macOS, so the harness runs the camera-only branch (documented limitation).
let package = Package(
    name: "PerceptionHarness",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "PerceptionHarness",
            path: "Sources/PerceptionHarness"
        )
    ]
)
