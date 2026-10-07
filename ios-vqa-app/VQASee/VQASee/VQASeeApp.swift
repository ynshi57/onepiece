//
//  VQASeeApp.swift
//  VQASee
//
//  Created by Bayes on 2026/6/3.
//

import SwiftUI

@main
struct VQASeeApp: App {
    @Environment(\.scenePhase) private var scenePhase
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
        .onChange(of: scenePhase, initial: true) { _, phase in
            AppScreenWakePolicy.update(for: phase)
        }
    }
}
