import Foundation

// Compilation shim.
//
// The harness reuses the app's real perception files (LocalPerception.swift,
// LocalVisionAnalyzer.swift, ...). LocalVisionAnalyzer.swift also contains
// `WalkingFrameSendPolicy`, which references `AssistanceMode` (defined in the
// app's Models.swift, a large SwiftUI-coupled file we deliberately do NOT pull
// into the harness module). The harness never invokes the walking send policy;
// this minimal stand-in only lets the shared source compile in isolation.
//
// Keep the `.walking` case name in sync with the app so the policy compiles.
enum AssistanceMode: String {
    case walking
    case readText
    case detail
}
