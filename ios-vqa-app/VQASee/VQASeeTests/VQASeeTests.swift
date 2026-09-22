//
//  VQASeeTests.swift
//  VQASeeTests
//
//  Created by Bayes on 2026/6/3.
//

import XCTest
@testable import VQASee

final class VQASeeTests: XCTestCase {

    override func setUpWithError() throws {
        // Put setup code here. This method is called before the invocation of each test method in the class.
    }

    override func tearDownWithError() throws {
        // Put teardown code here. This method is called after the invocation of each test method in the class.
    }

    func testNormalizeServerURLAddsWebSocketSchemeWhenMissing() {
        let normalized = StreamingConfigValidator.normalizeServerURL("localhost:8080/signaling")
        XCTAssertEqual(normalized?.absoluteString, "ws://localhost:8080/signaling")
    }

    func testNormalizeServerURLRejectsUnsupportedScheme() {
        let normalized = StreamingConfigValidator.normalizeServerURL("ftp://localhost:8080/signaling")
        XCTAssertNil(normalized)
    }

    func testIsLoopbackHostDetectsLocalhostAddress() {
        XCTAssertTrue(StreamingConfigValidator.isLoopbackHost("localhost:9000/ws/signaling"))
        XCTAssertTrue(StreamingConfigValidator.isLoopbackHost("ws://127.0.0.1:9000/ws/signaling"))
    }

    func testIsLoopbackHostIgnoresLANAddress() {
        XCTAssertFalse(StreamingConfigValidator.isLoopbackHost("ws://192.168.1.10:9000/ws/signaling"))
    }

    func testLocationTextFormatter() {
        let text = LocationTextFormatter.format(lat: 39.9042, lon: 116.4074)
        XCTAssertEqual(text, "39.90420, 116.40740")
    }

    func testParseStreamAckFromSignalingResponse() {
        let response = SignalingResponseParser.parse(
            from: ["type": "stream_ack", "frame_id": "frame-001"]
        )
        XCTAssertEqual(response, .streamAck(frameID: "frame-001"))
    }

    func testParseSignalingResponseRejectsUnsupportedType() {
        let response = SignalingResponseParser.parse(
            from: ["type": "unknown"]
        )
        XCTAssertEqual(response, .unsupported)
    }

    func testParseVQAResultIncludesLatencyAndDescription() {
        let response = SignalingResponseParser.parse(
            from: [
                "type": "vqa_result",
                "frame_id": "frame-123",
                "scene": "city street",
                "objects": ["car", "traffic_light"],
                "description": "cars on road",
                "latency_ms": 123.4
            ]
        )

        guard case let .vqaResult(result) = response else {
            return XCTFail("expected vqaResult, got \(response)")
        }
        XCTAssertEqual(result.frameID, "frame-123")
        XCTAssertEqual(result.scene, "city street")
        XCTAssertEqual(result.objects, ["car", "traffic_light"])
        XCTAssertEqual(result.description, "cars on road")
        XCTAssertEqual(result.latencyMs, 123.4)
    }


    func testParseVQAResultIncludesFrameIDFromRequestID() {
        let response = SignalingResponseParser.parse(
            from: [
                "type": "vqa_result",
                "request_id": "relay-frame-9",
                "summary": "ok"
            ]
        )

        guard case let .vqaResult(result) = response else {
            return XCTFail("expected vqaResult, got \(response)")
        }
        XCTAssertEqual(result.frameID, "relay-frame-9")
    }
    func testFrameMessageBuilderIncludesBase64AndGPS() {
        let frameData = Data([0x01, 0x02, 0x03])
        let payload = FrameMessageBuilder.build(
            frameID: "frame-123",
            prompt: "road scene",
            model: "qwen2.5vl:3b",
            jpegData: frameData,
            gps: (lat: 37.33, lon: -122.02),
            diagnosticSessionID: "diag-session"
        )

        XCTAssertEqual(payload["type"] as? String, "frame")
        XCTAssertEqual(payload["frame_id"] as? String, "frame-123")
        XCTAssertEqual(payload["prompt"] as? String, "road scene")
        XCTAssertEqual(payload["model"] as? String, "qwen2.5vl:3b")
        XCTAssertEqual(payload["diagnostic_session_id"] as? String, "diag-session")
        XCTAssertEqual(payload["image_base64"] as? String, "AQID")
        let gps = payload["gps"] as? [String: Double]
        XCTAssertEqual(gps?["lat"], 37.33)
        XCTAssertEqual(gps?["lon"], -122.02)
    }

    func testFrameMessageBuilderWithoutGPS() {
        let frameData = Data([0x0A])
        let payload = FrameMessageBuilder.build(
            frameID: "frame-abc",
            prompt: "scene",
            model: "qwen2.5vl:3b",
            jpegData: frameData,
            gps: nil
        )

        XCTAssertNil(payload["gps"])
    }

    func testFrameMessageBuilderIncludesModeAndQuestion() {
        let payload = FrameMessageBuilder.build(
            frameID: "frame-1",
            prompt: "legacy",
            model: "qwen2.5vl:3b",
            jpegData: Data([0x01]),
            gps: nil,
            mode: "walking",
            question: "前面是红灯还是绿灯？"
        )

        XCTAssertEqual(payload["mode"] as? String, "walking")
        XCTAssertEqual(payload["question"] as? String, "前面是红灯还是绿灯？")
        // legacy prompt is still present for backward compatibility
        XCTAssertEqual(payload["prompt"] as? String, "legacy")
    }

    func testFrameMessageBuilderOmitsBlankModeAndQuestion() {
        let payload = FrameMessageBuilder.build(
            frameID: "frame-2",
            prompt: "legacy",
            model: "qwen2.5vl:3b",
            jpegData: Data([0x01]),
            gps: nil,
            mode: "   ",
            question: ""
        )

        XCTAssertNil(payload["mode"])
        XCTAssertNil(payload["question"])
    }

    func testFrameMessageBuilderIncludesPreviousImageAndOCR() {
        let payload = FrameMessageBuilder.build(
            frameID: "frame-3",
            prompt: "legacy",
            model: "qwen2.5vl:7b",
            jpegData: Data([0x01]),
            gps: nil,
            previousImageBase64: "previous-base64",
            ocrText: "出口 EXIT"
        )

        XCTAssertEqual(payload["previous_image_base64"] as? String, "previous-base64")
        XCTAssertEqual(payload["client_ocr_text"] as? String, "出口 EXIT")
    }

    func testAutomaticModelChoosesFastForWalkingAndAccurateForReading() {
        XCTAssertEqual(VqaModelOption.automatic.resolvedModel(for: .walking), VqaModelOption.fast3b.rawValue)
        XCTAssertEqual(VqaModelOption.automatic.resolvedModel(for: .surroundings), VqaModelOption.fast3b.rawValue)
        XCTAssertEqual(VqaModelOption.automatic.resolvedModel(for: .readText), VqaModelOption.accurate7b.rawValue)
        XCTAssertEqual(VqaModelOption.automatic.resolvedModel(for: .detail), VqaModelOption.accurate7b.rawValue)
    }

    func testRuntimeModelPolicyShowsOnlyResolvedModelForSingleRuntime() {
        let status = RuntimeStatus(
            status: "qwen",
            apiBaseURL: "http://127.0.0.1:11435",
            configuredModel: "qwen2.5vl:3b",
            resolvedModel: "qwen2.5vl:3b",
            dynamicModelSelection: false,
            availableModels: ["qwen2.5vl:3b"],
            routingReason: "configured",
            message: nil
        )

        XCTAssertEqual(RuntimeModelPolicy.selectableOptions(for: status), [.fast3b])
        XCTAssertEqual(
            RuntimeModelPolicy.modelID(selectedModel: .automatic, mode: .detail, status: status),
            "qwen2.5vl:3b"
        )
    }

    func testRuntimeModelPolicyAllowsAutomaticForDynamicRuntime() {
        let status = RuntimeStatus(
            status: "qwen",
            apiBaseURL: "http://127.0.0.1:11434",
            configuredModel: "qwen2.5vl:3b",
            resolvedModel: "qwen2.5vl:3b",
            dynamicModelSelection: true,
            availableModels: ["qwen2.5vl:3b", "qwen2.5vl:7b"],
            routingReason: "configured",
            message: nil
        )

        XCTAssertEqual(RuntimeModelPolicy.selectableOptions(for: status), [.automatic, .fast3b, .accurate7b])
        XCTAssertEqual(
            RuntimeModelPolicy.modelID(selectedModel: .automatic, mode: .detail, status: status),
            "qwen2.5vl:7b"
        )
    }

    func testModeEncodingProfilesTradeLatencyForDetail() {
        XCTAssertLessThan(AssistanceMode.walking.encodingProfile.maxDimension, AssistanceMode.readText.encodingProfile.maxDimension)
        XCTAssertLessThan(AssistanceMode.walking.encodingProfile.maxJPEGBytes, AssistanceMode.readText.encodingProfile.maxJPEGBytes)
    }

    func testContinuousModesDoNotSendPreviousFrameByDefault() {
        XCTAssertFalse(AssistanceMode.walking.shouldSendPreviousFrame)
        XCTAssertFalse(AssistanceMode.surroundings.shouldSendPreviousFrame)
        XCTAssertFalse(AssistanceMode.readText.shouldSendPreviousFrame)
        XCTAssertTrue(AssistanceMode.detail.shouldSendPreviousFrame)
    }

    func testLatencyBreakdownSeparatesNetworkFromModel() {
        // sent at t=1.0s, received at t=2.5s -> 1500ms round trip; model took 1000ms.
        let segments = LatencyBreakdown.compute(
            sentAt: 1.0,
            receivedAt: 2.5,
            encodeMs: 40,
            serverModelMs: 1000
        )

        XCTAssertEqual(segments.roundTripMs, 1500, accuracy: 0.001)
        XCTAssertEqual(segments.encodeMs, 40)
        XCTAssertEqual(segments.serverModelMs, 1000)
        XCTAssertEqual(segments.networkQueueMs ?? -1, 500, accuracy: 0.001)
    }

    func testLatencyBreakdownClampsNegativeNetworkTime() {
        // Model time reported larger than the measured round trip (clock skew) must not go negative.
        let segments = LatencyBreakdown.compute(
            sentAt: 1.0,
            receivedAt: 1.5,
            encodeMs: nil,
            serverModelMs: 900
        )

        XCTAssertEqual(segments.roundTripMs, 500, accuracy: 0.001)
        XCTAssertEqual(segments.networkQueueMs ?? -1, 0, accuracy: 0.001)
    }

    func testLatencyBreakdownWithoutServerModelTimeHasNoNetworkSplit() {
        let segments = LatencyBreakdown.compute(
            sentAt: 1.0,
            receivedAt: 2.0,
            encodeMs: 30,
            serverModelMs: nil
        )

        XCTAssertNil(segments.networkQueueMs)
        XCTAssertNil(segments.serverModelMs)
        XCTAssertEqual(segments.roundTripMs, 1000, accuracy: 0.001)
    }

    func testLatencyBreakdownFormatShowsAllSegments() {
        let segments = LatencySegments(encodeMs: 40, roundTripMs: 1500, serverModelMs: 1000)
        let text = LatencyBreakdown.format(segments)
        // End-to-end = encode(40) + round trip(1500) = 1540; segments listed for diagnosis.
        XCTAssertTrue(text.contains("端到端 1540 ms"), text)
        XCTAssertTrue(text.contains("编码40"), text)
        XCTAssertTrue(text.contains("网络+排队500"), text)
        XCTAssertTrue(text.contains("模型1000"), text)
    }

    // MARK: - Speech input (press-to-talk)

    func testSpeechTextCleanerCollapsesWhitespace() {
        XCTAssertEqual(SpeechTextCleaner.clean("  前面 是   红灯  "), "前面 是 红灯")
    }

    func testSpeechTextCleanerReturnsNilForEmptyOrBlank() {
        XCTAssertNil(SpeechTextCleaner.clean(""))
        XCTAssertNil(SpeechTextCleaner.clean("   \n\t "))
    }

    func testSpeechTextCleanerKeepsSingleWord() {
        XCTAssertEqual(SpeechTextCleaner.clean("红灯"), "红灯")
    }

    func testVoiceQuestionIntentClassifiesNonVisualTimeQuestion() {
        XCTAssertEqual(VoiceQuestionIntent.classify("你知道今天是星期几吗"), .nonVisual)
        XCTAssertEqual(VoiceQuestionIntent.classify("现在几点"), .nonVisual)
    }

    func testVoiceQuestionIntentClassifiesReadText() {
        XCTAssertEqual(VoiceQuestionIntent.classify("帮我读一下说明书"), .readText)
        XCTAssertEqual(VoiceQuestionIntent.classify("上面写的什么"), .readText)
    }

    func testVoiceQuestionIntentClassifiesVisualQuestion() {
        XCTAssertEqual(VoiceQuestionIntent.classify("右边有什么"), .visualQuestion)
        XCTAssertEqual(VoiceQuestionIntent.classify("前方能不能走"), .visualQuestion)
    }

    func testReadTextPresentationPrefersOCRText() {
        let text = "用法用量\n每日三次"
        XCTAssertEqual(ReadTextPresentation.summary(for: text), text)
        XCTAssertTrue(ReadTextPresentation.spokenText(for: text).contains("每日三次"))
    }

    func testReadTextPresentationGuidesWhenNoText() {
        XCTAssertTrue(ReadTextPresentation.summary(for: "  ").contains("没有读到"))
        XCTAssertTrue(ReadTextPresentation.action(for: "").contains("画面中央"))
    }

    func testSpeechAuthorizationEvaluatorIdleWhenAllGranted() {
        XCTAssertEqual(
            SpeechAuthorizationEvaluator.state(
                speechAuthorized: true, micGranted: true, recognizerAvailable: true
            ),
            .idle
        )
    }

    func testSpeechAuthorizationEvaluatorReportsMissingSpeechFirst() {
        // Speech authorization is checked before mic so the user sees the most relevant prompt.
        guard case .unavailable = SpeechAuthorizationEvaluator.state(
            speechAuthorized: false, micGranted: false, recognizerAvailable: false
        ) else {
            return XCTFail("expected unavailable when speech not authorized")
        }
    }

    func testSpeechAuthorizationEvaluatorReportsMissingMic() {
        guard case .unavailable = SpeechAuthorizationEvaluator.state(
            speechAuthorized: true, micGranted: false, recognizerAvailable: true
        ) else {
            return XCTFail("expected unavailable when mic not granted")
        }
    }

    func testSpeechAuthorizationEvaluatorReportsUnavailableRecognizer() {
        guard case .unavailable = SpeechAuthorizationEvaluator.state(
            speechAuthorized: true, micGranted: true, recognizerAvailable: false
        ) else {
            return XCTFail("expected unavailable when recognizer missing")
        }
    }

    // MARK: - Scene continuity / speak-gating

    func testSpeechGateSpeaksFirstVisualResultEvenWhenNoChange() {
        XCTAssertTrue(SpeechGate.shouldSpeak(
            changeSignificance: "none",
            previousRiskLevel: nil,
            newRiskLevel: "low",
            millisecondsSinceLastSpoken: nil,
            maxSilenceMs: 25_000
        ))
    }

    func testSpeechGateAlwaysSpeaksOnMajorChange() {
        XCTAssertTrue(SpeechGate.shouldSpeak(
            changeSignificance: "major",
            previousRiskLevel: "low",
            newRiskLevel: "low",
            millisecondsSinceLastSpoken: 0,
            maxSilenceMs: 25_000
        ))
    }

    func testSpeechGateStaysSilentOnNoChangeWhenRecent() {
        XCTAssertFalse(SpeechGate.shouldSpeak(
            changeSignificance: "none",
            previousRiskLevel: "low",
            newRiskLevel: "low",
            millisecondsSinceLastSpoken: 1_000,
            maxSilenceMs: 25_000
        ))
    }

    func testSpeechGateSpeaksWhenRiskRises() {
        XCTAssertTrue(SpeechGate.shouldSpeak(
            changeSignificance: "none",
            previousRiskLevel: "low",
            newRiskLevel: "high",
            millisecondsSinceLastSpoken: 1_000,
            maxSilenceMs: 25_000
        ))
    }

    func testSpeechGateStaysSilentWhenRiskDrops() {
        XCTAssertFalse(SpeechGate.shouldSpeak(
            changeSignificance: "minor",
            previousRiskLevel: "high",
            newRiskLevel: "low",
            millisecondsSinceLastSpoken: 1_000,
            maxSilenceMs: 25_000
        ))
    }

    func testSpeechGateSpeaksAfterLongSilenceHeartbeat() {
        XCTAssertTrue(SpeechGate.shouldSpeak(
            changeSignificance: "none",
            previousRiskLevel: "low",
            newRiskLevel: "low",
            millisecondsSinceLastSpoken: 30_000,
            maxSilenceMs: 25_000
        ))
    }



    private func sampleVqaResult(
        riskLevel: String = "low",
        changeSignificance: String = "none",
        changes: String = ""
    ) -> VqaDisplayResult {
        VqaDisplayResult(
            frameID: "frame-test",
            scene: "hallway",
            objects: [],
            description: "正前方可通行。",
            summary: "正前方可通行。",
            spatialDescription: "左侧信息不足，正前方可通行，右侧信息不足。",
            riskLevel: riskLevel,
            riskMessage: "暂未发现明显危险。",
            suggestedAction: "保持手机朝向前方。",
            spokenText: "正前方可通行。",
            ocrText: "",
            latencyMs: 1_000,
            changeSignificance: changeSignificance,
            changes: changes
        )
    }

    func testVoiceFeedbackPolicySpeaksFirstVisualResult() {
        XCTAssertEqual(
            VoiceFeedbackPolicy.decideForModelResult(
                answeringVoiceQuestion: false,
                hasOCROverride: false,
                ocrText: "",
                result: sampleVqaResult(changeSignificance: "none"),
                previousRiskLevel: nil,
                millisecondsSinceLastSpoken: nil,
                maxSilenceMs: 25_000
            ),
            .speak(text: "正前方可通行。", force: false, reason: "首次视觉反馈")
        )
    }

    func testVoiceFeedbackPolicyStaysSilentForRecentNoChange() {
        XCTAssertEqual(
            VoiceFeedbackPolicy.decideForModelResult(
                answeringVoiceQuestion: false,
                hasOCROverride: false,
                ocrText: "",
                result: sampleVqaResult(changeSignificance: "none"),
                previousRiskLevel: "low",
                millisecondsSinceLastSpoken: 1_000,
                maxSilenceMs: 25_000
            ),
            .silent(reason: "无重要变化")
        )
    }

    func testVoiceFeedbackPolicyForcesVoiceQuestionAnswer() {
        XCTAssertEqual(
            VoiceFeedbackPolicy.decideForModelResult(
                answeringVoiceQuestion: true,
                hasOCROverride: false,
                ocrText: "",
                result: sampleVqaResult(changeSignificance: "none"),
                previousRiskLevel: "low",
                millisecondsSinceLastSpoken: 1_000,
                maxSilenceMs: 25_000
            ),
            .speak(text: "正前方可通行。", force: true, reason: "回答用户提问")
        )
    }

    // MARK: - PressGestureInterpreter (push-to-talk)

    func testPressGestureBeganStartsRecording() {
        XCTAssertEqual(PressGestureInterpreter.action(for: .began), .start)
    }

    func testPressGestureEndedStopsRecording() {
        XCTAssertEqual(PressGestureInterpreter.action(for: .ended), .stop)
    }

    func testPressGestureCancelledStopsRecording() {
        XCTAssertEqual(PressGestureInterpreter.action(for: .cancelled), .stop)
    }

    func testPressGestureFailedStopsRecording() {
        XCTAssertEqual(PressGestureInterpreter.action(for: .failed), .stop)
    }

    func testPressGesturePossibleIsIgnored() {
        XCTAssertEqual(PressGestureInterpreter.action(for: .possible), .ignore)
    }

    func testPressGestureChangedIsIgnoredSoDriftDoesNotRestart() {
        // A finger drifting while held emits `.changed`; it must NOT re-trigger
        // start/stop, or the press would flap. This is the core "无法按住" guard.
        XCTAssertEqual(PressGestureInterpreter.action(for: .changed), .ignore)
    }

    // MARK: - LocalSubnetPlanner (Wi-Fi fallback host sweep)

    func testLocalSubnetPlannerSweepsDeviceSlash24ExcludingSelf() {
        let hosts = LocalSubnetPlanner.candidateHosts(deviceIPv4: "192.168.124.6")
        // .1 comes first (typical gateway/host), the device's own .6 is excluded,
        // and the usable range is 2...254 (no .0 network / .255 broadcast).
        XCTAssertEqual(hosts.first, "192.168.124.1")
        XCTAssertFalse(hosts.contains("192.168.124.6"))
        XCTAssertFalse(hosts.contains("192.168.124.0"))
        XCTAssertFalse(hosts.contains("192.168.124.255"))
        XCTAssertTrue(hosts.contains("192.168.124.7"))
        XCTAssertEqual(hosts.count, 253) // 1...254 minus self
    }

    func testLocalSubnetPlannerCoversHotspotSubnet() {
        let hosts = LocalSubnetPlanner.candidateHosts(deviceIPv4: "172.20.10.3")
        XCTAssertEqual(hosts.first, "172.20.10.1")
        XCTAssertTrue(hosts.contains("172.20.10.6"))
        XCTAssertFalse(hosts.contains("172.20.10.3"))
    }

    func testLocalSubnetPlannerPutsGatewayFirstEvenWhenDeviceIsNotDotOne() {
        let hosts = LocalSubnetPlanner.candidateHosts(deviceIPv4: "10.0.0.42")
        XCTAssertEqual(hosts.first, "10.0.0.1")
    }

    func testLocalSubnetPlannerWhenDeviceIsDotOneSkipsSelfFirst() {
        let hosts = LocalSubnetPlanner.candidateHosts(deviceIPv4: "192.168.1.1")
        XCTAssertFalse(hosts.contains("192.168.1.1"))
        XCTAssertEqual(hosts.first, "192.168.1.2")
    }

    func testLocalSubnetPlannerRejectsNonPrivateOrMalformed() {
        XCTAssertTrue(LocalSubnetPlanner.candidateHosts(deviceIPv4: "8.8.8.8").isEmpty)       // public
        XCTAssertTrue(LocalSubnetPlanner.candidateHosts(deviceIPv4: "169.254.1.2").isEmpty)   // link-local
        XCTAssertTrue(LocalSubnetPlanner.candidateHosts(deviceIPv4: "127.0.0.1").isEmpty)     // loopback
        XCTAssertTrue(LocalSubnetPlanner.candidateHosts(deviceIPv4: "172.15.0.1").isEmpty)    // outside 172.16-31
        XCTAssertTrue(LocalSubnetPlanner.candidateHosts(deviceIPv4: "not.an.ip").isEmpty)
        XCTAssertTrue(LocalSubnetPlanner.candidateHosts(deviceIPv4: "192.168.1").isEmpty)     // too few octets
        XCTAssertTrue(LocalSubnetPlanner.candidateHosts(deviceIPv4: "192.168.1.999").isEmpty) // octet out of range
    }

    // MARK: - BonjourTXTRecord (explicit ip TXT + path)

    func testBonjourTXTRecordPrefersExplicitIPOverHostname() {
        let txt = NetService.data(fromTXTRecord: [
            "ip": Data("192.168.1.20".utf8),
            "path": Data("/ws/signaling".utf8),
        ])
        let host = BonjourTXTRecord.preferredHost(
            txtRecordData: txt,
            addresses: nil,
            hostName: "macbook.local.",
            serviceName: "VQASee Mac VQA"
        )
        XCTAssertEqual(host, "192.168.1.20")
        XCTAssertEqual(BonjourTXTRecord.path(from: txt), "/ws/signaling")
    }

    func testBonjourTXTRecordRejectsLoopbackIPInTXT() {
        let txt = NetService.data(fromTXTRecord: [
            "ip": Data("127.0.0.1".utf8),
        ])
        let host = BonjourTXTRecord.preferredHost(
            txtRecordData: txt,
            addresses: nil,
            hostName: "macbook.local.",
            serviceName: "VQASee Mac VQA"
        )
        XCTAssertEqual(host, "macbook.local")
    }

    func testBackendDiscoveryTimingsAllowParallelBonjourAndSweep() {
        XCTAssertGreaterThanOrEqual(BackendDiscoveryTimings.bonjourWaitSeconds, 8)
        XCTAssertGreaterThanOrEqual(BackendDiscoveryTimings.healthProbeTimeoutSeconds, 0.5)
    }

    func testFrameContextHasContentAndPayload() {
        let empty = FrameContext(prevSummary: "", prevScene: "", prevObjects: [], placeLabel: "", elapsedMs: 0)
        XCTAssertFalse(empty.hasContent)
        XCTAssertTrue(empty.payload.isEmpty)

        let full = FrameContext(
            prevSummary: "正前方是一条走廊。",
            prevScene: "hallway",
            prevObjects: ["door"],
            placeLabel: "中关村南路附近",
            elapsedMs: 2_000
        )
        XCTAssertTrue(full.hasContent)
        XCTAssertEqual(full.payload["place_label"] as? String, "中关村南路附近")
        XCTAssertEqual(full.payload["elapsed_ms"] as? Double, 2_000)
    }

    func testFrameContextIncludesLocalVisionWhenPresent() {
        let localOnly = FrameContext(
            prevSummary: "", prevScene: "", prevObjects: [], placeLabel: "", elapsedMs: 0,
            localVisionSummary: "疑似有人在正前方"
        )

        XCTAssertTrue(localOnly.hasContent)
        XCTAssertEqual(localOnly.payload["local_vision"] as? String, "疑似有人在正前方")
    }

    func testFrameContextOmitsZeroElapsed() {
        let placeOnly = FrameContext(prevSummary: "", prevScene: "", prevObjects: [], placeLabel: "x附近", elapsedMs: 0)
        XCTAssertTrue(placeOnly.hasContent)
        XCTAssertNil(placeOnly.payload["elapsed_ms"])
    }

    func testFrameMessageBuilderIncludesContextWhenPresent() {
        let ctx = FrameContext(
            prevSummary: "s", prevScene: "hallway", prevObjects: ["door"],
            placeLabel: "中关村南路附近", elapsedMs: 2_000
        )
        let payload = FrameMessageBuilder.build(
            frameID: "f1", prompt: "p", model: "qwen2.5vl:3b", jpegData: Data([0x01]),
            gps: nil, mode: "surroundings", question: "", context: ctx
        )
        let context = payload["context"] as? [String: Any]
        XCTAssertNotNil(context)
        XCTAssertEqual(context?["prev_scene"] as? String, "hallway")
    }

    func testFrameMessageBuilderOmitsEmptyContext() {
        let empty = FrameContext(prevSummary: "", prevScene: "", prevObjects: [], placeLabel: "", elapsedMs: 0)
        let payload = FrameMessageBuilder.build(
            frameID: "f1", prompt: "p", model: "qwen2.5vl:3b", jpegData: Data([0x01]),
            gps: nil, mode: "surroundings", question: "", context: empty
        )
        XCTAssertNil(payload["context"])
    }


    // MARK: - Local Vision / Walking trigger policy

    func testLocalVisionSignalBuildsBackendContextForHuman() {
        let signal = LocalVisionSignal(
            hasHuman: true,
            humanDirection: .center,
            brightness: 0.5,
            sceneChangeScore: 0.02,
            isTooDark: false,
            isLikelyCovered: false,
            analyzerFailed: false
        )

        XCTAssertTrue(signal.backendContext.contains("疑似有人"))
        XCTAssertTrue(signal.backendContext.contains("正前方"))
    }





    func testLocalPerceptionSignalReportsRiskObjectInBackendContext() {
        let perception = LocalPerceptionSignal(
            objects: [
                LocalPerceptionObject(kind: .car, direction: .center, confidence: 0.82)
            ],
            modelStatus: .loaded
        )

        XCTAssertTrue(perception.hasPriorityRiskObject)
        XCTAssertEqual(perception.primaryRiskObject?.kind, .car)
        XCTAssertTrue(perception.backendContext.contains("正前方疑似车辆"), perception.backendContext)
    }

    func testLocalVisionSignalIncludesPerceptionContext() {
        let signal = LocalVisionSignal(
            hasHuman: false,
            humanDirection: .unknown,
            brightness: 0.5,
            sceneChangeScore: 0.01,
            isTooDark: false,
            isLikelyCovered: false,
            analyzerFailed: false,
            perception: LocalPerceptionSignal(
                objects: [LocalPerceptionObject(kind: .bicycle, direction: .right, confidence: 0.77)],
                modelStatus: .loaded
            )
        )

        XCTAssertTrue(signal.backendContext.contains("右侧疑似自行车"), signal.backendContext)
    }


    func testLocalPerceptionMapsRoadAndDropLabels() {
        XCTAssertEqual(LocalPerceptionObjectKind.from(label: "crosswalk"), .crosswalk)
        XCTAssertEqual(LocalPerceptionObjectKind.from(label: "lane_marking"), .laneMarking)
        XCTAssertEqual(LocalPerceptionObjectKind.from(label: "curb"), .curb)
        XCTAssertEqual(LocalPerceptionObjectKind.from(label: "stairs"), .stairs)
        XCTAssertEqual(LocalPerceptionObjectKind.from(label: "pothole"), .pothole)
    }

    func testWalkingImmediateFeedbackSpeaksRoadCue() {
        let signal = LocalVisionSignal(
            hasHuman: false, humanDirection: .unknown, brightness: 0.5,
            sceneChangeScore: 0.01, isTooDark: false, isLikelyCovered: false, analyzerFailed: false,
            perception: LocalPerceptionSignal(roadCues: LocalRoadCueSignal(crosswalk: .possible))
        )

        XCTAssertEqual(
            WalkingImmediateFeedbackPolicy.decide(
                mode: .walking,
                signal: signal,
                hasQuestion: false,
                millisecondsSinceLastImmediateSpeech: nil
            ),
            .speak(text: "前方有疑似边界或道路标线，请放慢并自行确认。", force: false, reason: "本地感知道路线索")
        )
    }

    func testWalkingImmediateFeedbackSpeaksHumanWhenNotCoolingDown() {
        let signal = LocalVisionSignal(
            hasHuman: true, humanDirection: .center, brightness: 0.5,
            sceneChangeScore: 0.01, isTooDark: false, isLikelyCovered: false, analyzerFailed: false
        )

        XCTAssertEqual(
            WalkingImmediateFeedbackPolicy.decide(
                mode: .walking,
                signal: signal,
                hasQuestion: false,
                millisecondsSinceLastImmediateSpeech: nil
            ),
            .speak(text: "正前方可能有人，我正在确认。", force: false, reason: "本地检测疑似人形")
        )
    }

    func testWalkingImmediateFeedbackRespectsCooldownAndQuestions() {
        let signal = LocalVisionSignal(
            hasHuman: true, humanDirection: .center, brightness: 0.5,
            sceneChangeScore: 0.01, isTooDark: false, isLikelyCovered: false, analyzerFailed: false
        )

        XCTAssertEqual(
            WalkingImmediateFeedbackPolicy.decide(
                mode: .walking,
                signal: signal,
                hasQuestion: false,
                millisecondsSinceLastImmediateSpeech: 1_000
            ),
            .silent(reason: "本地即时播报冷却中")
        )
        XCTAssertEqual(
            WalkingImmediateFeedbackPolicy.decide(
                mode: .walking,
                signal: signal,
                hasQuestion: true,
                millisecondsSinceLastImmediateSpeech: nil
            ),
            .silent(reason: "用户提问中，等待回答")
        )
    }



    func testWalkingImmediateFeedbackSpeaksPriorityObject() {
        let signal = LocalVisionSignal(
            hasHuman: false,
            humanDirection: .unknown,
            brightness: 0.5,
            sceneChangeScore: 0.01,
            isTooDark: false,
            isLikelyCovered: false,
            analyzerFailed: false,
            perception: LocalPerceptionSignal(
                objects: [LocalPerceptionObject(kind: .car, direction: .center, confidence: 0.82)],
                modelStatus: .loaded
            )
        )

        XCTAssertEqual(
            WalkingImmediateFeedbackPolicy.decide(
                mode: .walking,
                signal: signal,
                hasQuestion: false,
                millisecondsSinceLastImmediateSpeech: nil
            ),
            .speak(text: "正前方可能有车辆，请放慢，我正在确认。", force: false, reason: "本地感知检测到车辆")
        )
    }

    func testWalkingPolicySendsFirstFrame() {
        let signal = LocalVisionSignal(
            hasHuman: false, humanDirection: .unknown, brightness: 0.5,
            sceneChangeScore: 0.01, isTooDark: false, isLikelyCovered: false, analyzerFailed: false
        )

        XCTAssertEqual(
            WalkingFrameSendPolicy.decide(
                mode: .walking, signal: signal, hasQuestion: false, pendingSingleShot: false,
                millisecondsSinceLastBackendFrame: nil
            ),
            .send("行走模式首帧")
        )
    }



    func testWalkingPolicySendsPriorityPerceptionObject() {
        let signal = LocalVisionSignal(
            hasHuman: false,
            humanDirection: .unknown,
            brightness: 0.5,
            sceneChangeScore: 0.01,
            isTooDark: false,
            isLikelyCovered: false,
            analyzerFailed: false,
            perception: LocalPerceptionSignal(
                objects: [LocalPerceptionObject(kind: .truck, direction: .left, confidence: 0.75)],
                modelStatus: .loaded
            )
        )

        XCTAssertEqual(
            WalkingFrameSendPolicy.decide(
                mode: .walking,
                signal: signal,
                hasQuestion: false,
                pendingSingleShot: false,
                millisecondsSinceLastBackendFrame: 1_000
            ),
            .send("本地感知检测到风险物体")
        )
    }

    func testWalkingPolicySkipsStableRecentFrame() {
        let signal = LocalVisionSignal(
            hasHuman: false, humanDirection: .unknown, brightness: 0.5,
            sceneChangeScore: 0.01, isTooDark: false, isLikelyCovered: false, analyzerFailed: false
        )

        XCTAssertEqual(
            WalkingFrameSendPolicy.decide(
                mode: .walking, signal: signal, hasQuestion: false, pendingSingleShot: false,
                millisecondsSinceLastBackendFrame: 2_000
            ),
            .skip("画面稳定，等待变化或心跳")
        )
    }

    func testWalkingPolicySendsHumanSceneChangeQualityAndHeartbeat() {
        let human = LocalVisionSignal(
            hasHuman: true, humanDirection: .left, brightness: 0.5,
            sceneChangeScore: 0.01, isTooDark: false, isLikelyCovered: false, analyzerFailed: false
        )
        if case .send = WalkingFrameSendPolicy.decide(
            mode: .walking, signal: human, hasQuestion: false, pendingSingleShot: false,
            millisecondsSinceLastBackendFrame: 1_000
        ) {} else { XCTFail("human should trigger backend") }

        // Moderate scene change (>= threshold, < significant) is RATE-LIMITED: it is
        // skipped within the Qwen cooldown and only sent once the interval elapses.
        // This is the intended anti-spam contract; safety signals (human/quality/
        // significant change) bypass it via the other cases.
        let changed = LocalVisionSignal(
            hasHuman: false, humanDirection: .unknown, brightness: 0.5,
            sceneChangeScore: WalkingFrameSendPolicy.sceneChangeThreshold,
            isTooDark: false, isLikelyCovered: false, analyzerFailed: false
        )
        if case .skip = WalkingFrameSendPolicy.decide(
            mode: .walking, signal: changed, hasQuestion: false, pendingSingleShot: false,
            millisecondsSinceLastBackendFrame: 1_000
        ) {} else { XCTFail("moderate scene change within cooldown should be rate-limited") }
        if case .send = WalkingFrameSendPolicy.decide(
            mode: .walking, signal: changed, hasQuestion: false, pendingSingleShot: false,
            millisecondsSinceLastBackendFrame: WalkingFrameSendPolicy.minimumQwenIntervalMs
        ) {} else { XCTFail("moderate scene change should trigger backend after cooldown") }

        // Quality risk (dark/covered) is ALSO rate-limited: within the cooldown it is
        // skipped (already surfaced locally via voice/haptic — "画面质量风险已本地提示"),
        // and rechecked at low frequency once the interval elapses.
        let dark = LocalVisionSignal(
            hasHuman: false, humanDirection: .unknown, brightness: 0.05,
            sceneChangeScore: 0.01, isTooDark: true, isLikelyCovered: false, analyzerFailed: false
        )
        if case .skip = WalkingFrameSendPolicy.decide(
            mode: .walking, signal: dark, hasQuestion: false, pendingSingleShot: false,
            millisecondsSinceLastBackendFrame: 1_000
        ) {} else { XCTFail("quality risk within cooldown should be rate-limited") }
        if case .send = WalkingFrameSendPolicy.decide(
            mode: .walking, signal: dark, hasQuestion: false, pendingSingleShot: false,
            millisecondsSinceLastBackendFrame: WalkingFrameSendPolicy.minimumQwenIntervalMs
        ) {} else { XCTFail("quality risk should trigger backend after cooldown") }

        let stable = LocalVisionSignal(
            hasHuman: false, humanDirection: .unknown, brightness: 0.5,
            sceneChangeScore: 0.01, isTooDark: false, isLikelyCovered: false, analyzerFailed: false
        )
        if case .send = WalkingFrameSendPolicy.decide(
            mode: .walking, signal: stable, hasQuestion: false, pendingSingleShot: false,
            millisecondsSinceLastBackendFrame: WalkingFrameSendPolicy.heartbeatMs
        ) {} else { XCTFail("heartbeat should trigger backend") }
    }

    func testWalkingPolicyAlwaysSendsQuestionsAndNonWalkingModes() {
        let signal = LocalVisionSignal(
            hasHuman: false, humanDirection: .unknown, brightness: 0.5,
            sceneChangeScore: 0.01, isTooDark: false, isLikelyCovered: false, analyzerFailed: false
        )

        if case .send = WalkingFrameSendPolicy.decide(
            mode: .walking, signal: signal, hasQuestion: true, pendingSingleShot: false,
            millisecondsSinceLastBackendFrame: 1_000
        ) {} else { XCTFail("question should trigger backend") }

        if case .send = WalkingFrameSendPolicy.decide(
            mode: .surroundings, signal: signal, hasQuestion: false, pendingSingleShot: false,
            millisecondsSinceLastBackendFrame: 1_000
        ) {} else { XCTFail("non-walking mode should keep existing strategy") }
    }

    // MARK: - Auto-connect discovery policy

    private func server(_ name: String, _ urlString: String) -> DiscoveredServer {
        DiscoveredServer(name: name, url: URL(string: urlString)!)
    }

    func testAutoConnectSearchesWhenNothingDiscovered() {
        XCTAssertEqual(
            AutoConnectPolicy.decide(discovered: [], userPinned: false),
            .searching
        )
    }

    func testAutoConnectAutoFillsSingleBackend() {
        let s = server("Mac A", "ws://192.168.1.5:9000/ws/signaling")
        XCTAssertEqual(
            AutoConnectPolicy.decide(discovered: [s], userPinned: false),
            .autoFill(s.url)
        )
    }

    func testAutoConnectAsksToChooseWhenMultiple() {
        let a = server("Mac A", "ws://192.168.1.5:9000/ws/signaling")
        let b = server("Mac B", "ws://192.168.1.6:9000/ws/signaling")
        XCTAssertEqual(
            AutoConnectPolicy.decide(discovered: [a, b], userPinned: false),
            .choose([a, b])
        )
    }

    func testAutoConnectKeepsUserChoiceEvenWithSingleDiscovery() {
        let s = server("Mac A", "ws://192.168.1.5:9000/ws/signaling")
        XCTAssertEqual(
            AutoConnectPolicy.decide(discovered: [s], userPinned: true),
            .keepUserChoice
        )
    }

    func testAutoConnectKeepsUserChoiceWithMultipleDiscovery() {
        let a = server("Mac A", "ws://192.168.1.5:9000/ws/signaling")
        let b = server("Mac B", "ws://192.168.1.6:9000/ws/signaling")
        XCTAssertEqual(
            AutoConnectPolicy.decide(discovered: [a, b], userPinned: true),
            .keepUserChoice
        )
    }

    // MARK: - Sockaddr IPv4 parsing (prefer numeric IP over .local)

    /// Builds a `sockaddr_in` Data blob for the given dotted-quad + port, as
    /// `NetService.addresses` would hand us.
    private func makeIPv4SockaddrData(_ ip: String, port: UInt16) -> Data {
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        XCTAssertEqual(inet_pton(AF_INET, ip, &addr.sin_addr), 1)
        return withUnsafeBytes(of: &addr) { Data($0) }
    }

    func testSockaddrParserExtractsIPv4() {
        let data = makeIPv4SockaddrData("192.168.124.6", port: 9000)
        XCTAssertEqual(SockaddrParser.ipv4String(fromSockaddr: data), "192.168.124.6")
    }

    func testSockaddrParserReturnsNilForIPv6() {
        var addr6 = sockaddr_in6()
        addr6.sin6_len = UInt8(MemoryLayout<sockaddr_in6>.size)
        addr6.sin6_family = sa_family_t(AF_INET6)
        let data = withUnsafeBytes(of: &addr6) { Data($0) }
        XCTAssertNil(SockaddrParser.ipv4String(fromSockaddr: data))
    }

    func testSockaddrParserReturnsNilForTruncatedData() {
        XCTAssertNil(SockaddrParser.ipv4String(fromSockaddr: Data([0x02, 0x00])))
    }

    func testObservationRouteDefaultsToRiskObserve() {
        let route = ObservationRoute.resolve(question: "", voiceIntent: nil)

        XCTAssertEqual(route, .riskObserve)
        XCTAssertEqual(route.backendMode, "risk_observe")
        XCTAssertEqual(route.prompt, "")
        XCTAssertEqual(route.compatibilityMode, .walking)
        XCTAssertFalse(route.isSingleShotPreferred)
    }

    func testObservationRouteKeepsReadTextAsInternalIntentOnly() {
        let route = ObservationRoute.resolve(question: "帮我读一下", voiceIntent: .readText)

        XCTAssertEqual(route, .readText)
        XCTAssertEqual(route.backendMode, "readText")
        XCTAssertEqual(route.compatibilityMode, .readText)
        XCTAssertTrue(route.isSingleShotPreferred)
        XCTAssertTrue(route.prompt.contains("模式=读文字"))
    }

    func testObservationRouteDetailOnlyFromQuestionIntent() {
        let route = ObservationRoute.resolve(question: "请详细描述一下", voiceIntent: .visualQuestion)

        XCTAssertEqual(route, .detail)
        XCTAssertEqual(route.backendMode, "detail")
        XCTAssertEqual(route.compatibilityMode, .detail)
        XCTAssertTrue(route.isSingleShotPreferred)
        XCTAssertTrue(route.prompt.contains("模式=详细"))
    }

    func testObservationRouteVisualQuestionUsesRiskObserveBackend() {
        let route = ObservationRoute.resolve(question: "右边有什么", voiceIntent: .visualQuestion)

        XCTAssertEqual(route, .question)
        XCTAssertEqual(route.backendMode, "risk_observe")
        XCTAssertEqual(route.compatibilityMode, .walking)
        XCTAssertTrue(route.isSingleShotPreferred)
        XCTAssertTrue(route.prompt.contains("模式=风险观察"))
    }

    func testLocalPathGuidanceMarksCenterObjectAsBlocked() {
        let object = LocalPerceptionObject(
            kind: .person,
            direction: .center,
            confidence: 0.93,
            normalizedBoundingBox: CGRect(x: 0.42, y: 0.08, width: 0.18, height: 0.28)
        )
        let perception = LocalPerceptionSignal(objects: [object], modelStatus: .loaded)

        let guidance = LocalPathGuidanceEngine.evaluate(
            perception: perception,
            isTooDark: false,
            isLikelyCovered: false,
            depthCapability: .unsupported
        )

        XCTAssertFalse(guidance.blockedRegions.isEmpty)
        XCTAssertTrue(guidance.reasons.contains(.objectInNearPath))
        XCTAssertEqual(guidance.depthCapability, .unsupported)
        XCTAssertEqual(guidance.segmentationCapability, .unsupported)
    }

    func testLocalPathGuidanceEmptySceneIsCandidateOpenNotSafe() {
        let guidance = LocalPathGuidanceEngine.evaluate(
            perception: .empty,
            isTooDark: false,
            isLikelyCovered: false,
            depthCapability: .unsupported
        )

        XCTAssertTrue(guidance.blockedRegions.isEmpty)
        XCTAssertTrue(guidance.reasons.contains(.yoloOnly))
    }

    func testLocalPathGuidanceLowLightIsUnknown() {
        let guidance = LocalPathGuidanceEngine.evaluate(
            perception: .empty,
            isTooDark: true,
            isLikelyCovered: false,
            depthCapability: .unsupported
        )

        XCTAssertTrue(guidance.reasons.contains(.lowLight))
        XCTAssertFalse(guidance.uncertainRegions.isEmpty)
    }

    func testLocalPathGuidanceRightObjectRecordsBlockedRegion() {
        let object = LocalPerceptionObject(
            kind: .obstacle,
            direction: .right,
            confidence: 0.88,
            normalizedBoundingBox: CGRect(x: 0.80, y: 0.18, width: 0.15, height: 0.22)
        )
        let perception = LocalPerceptionSignal(objects: [object], modelStatus: .loaded)

        let guidance = LocalPathGuidanceEngine.evaluate(
            perception: perception,
            isTooDark: false,
            isLikelyCovered: false,
            depthCapability: .unsupported
        )

        XCTAssertEqual(guidance.blockedRegions.count, 1)
        XCTAssertTrue(guidance.reasons.contains(.objectInNearPath))
    }

    func testLocalPerceptionPostProcessorDowngradesSmallVehicleToObstacleCandidate() {
        let adjusted = LocalPerceptionPostProcessor.adjustedDetection(
            kind: .car,
            confidence: 0.92,
            boundingBox: CGRect(x: 0.56, y: 0.80, width: 0.18, height: 0.19)
        )

        XCTAssertEqual(adjusted?.kind, .obstacle)
        XCTAssertLessThanOrEqual(adjusted?.confidence ?? 1, 0.72)
    }

    func testLocalPerceptionPostProcessorSuppressesBottomEdgeSmallPerson() {
        let adjusted = LocalPerceptionPostProcessor.adjustedDetection(
            kind: .person,
            confidence: 0.98,
            boundingBox: CGRect(x: 0.49, y: -0.0003, width: 0.20, height: 0.09)
        )

        XCTAssertNil(adjusted)
    }

    func testLocalPerceptionPostProcessorKeepsLargePerson() {
        let adjusted = LocalPerceptionPostProcessor.adjustedDetection(
            kind: .person,
            confidence: 0.91,
            boundingBox: CGRect(x: 0.42, y: 0.18, width: 0.20, height: 0.34)
        )

        XCTAssertEqual(adjusted?.kind, .person)
    }

    // MARK: - G: role-conditioned N=5 multiclass traversability derivation

    // logits order = [bg, road, sidewalk, lane, obstacle] (SegClass contract).
    private static let sidewalkLogits: [Double] = [0, 0, 5, 0, 0]
    private static let roadLogits: [Double] = [0, 5, 0, 0, 0]
    private static let laneLogits: [Double] = [0, 0, 0, 5, 0]

    func testMulticlassPedestrianTreatsSidewalkAsTraversable() {
        // A walker's traversable surface is the sidewalk, NOT the road.
        let onSidewalk = LocalTraversabilitySegmentationRunner.traversableProbability(
            fromClassLogits: Self.sidewalkLogits, role: .pedestrian)
        let onRoad = LocalTraversabilitySegmentationRunner.traversableProbability(
            fromClassLogits: Self.roadLogits, role: .pedestrian)
        XCTAssertNotNil(onSidewalk)
        XCTAssertNotNil(onRoad)
        XCTAssertGreaterThan(onSidewalk!, 0.9)
        XCTAssertLessThan(onRoad!, 0.1)
        // Same pixel, opposite verdict for the two roles: the core of 区分人车.
        XCTAssertGreaterThan(onSidewalk!,
            LocalTraversabilitySegmentationRunner.traversableProbability(
                fromClassLogits: Self.sidewalkLogits, role: .vehicle)!)
    }

    func testMulticlassVehicleTreatsRoadAndLaneAsTraversable() {
        // A driver's surface is the carriageway + lane markings, NOT the sidewalk.
        let onRoad = LocalTraversabilitySegmentationRunner.traversableProbability(
            fromClassLogits: Self.roadLogits, role: .vehicle)
        let onLane = LocalTraversabilitySegmentationRunner.traversableProbability(
            fromClassLogits: Self.laneLogits, role: .vehicle)
        let onSidewalk = LocalTraversabilitySegmentationRunner.traversableProbability(
            fromClassLogits: Self.sidewalkLogits, role: .vehicle)
        XCTAssertGreaterThan(onRoad!, 0.9)
        XCTAssertGreaterThan(onLane!, 0.9)
        XCTAssertLessThan(onSidewalk!, 0.1)
    }

    func testMulticlassRejectsNonFiniteAndTooFewClasses() {
        XCTAssertNil(LocalTraversabilitySegmentationRunner.traversableProbability(
            fromClassLogits: [0, .nan, 5, 0, 0], role: .pedestrian))
        // Fewer than 3 classes is the binary/single-channel path, not multiclass.
        XCTAssertNil(LocalTraversabilitySegmentationRunner.traversableProbability(
            fromClassLogits: [0, 5], role: .pedestrian))
    }

    func testPerceptionConfigWireParsesRoleAndDefaultsToPedestrian() {
        func json(role: String?) -> Data {
            let roleLine = role.map { "\"role\": \"\($0)\"," } ?? ""
            return Data("""
            {
              "version": 1,
              \(roleLine)
              "thresholds": {
                "seg_traversable_pixel": 0.55
              }
            }
            """.utf8)
        }
        XCTAssertEqual(PerceptionConfig.from(jsonData: json(role: "vehicle"))?.role, .vehicle)
        XCTAssertEqual(PerceptionConfig.from(jsonData: json(role: nil))?.role, .pedestrian)
        // An explicit unknown role is rejected (never silently coerced).
        XCTAssertNil(PerceptionConfig.from(jsonData: json(role: "bogus")))
        // Staged rollout defaults: without the flag the app does not run binary Fast-SCNN.
        XCTAssertEqual(PerceptionConfig.default.useMulticlassSegmentation, false)
        XCTAssertEqual(PerceptionConfig.from(jsonData: json(role: nil))?.useMulticlassSegmentation, false)
        // Lane segmenter ships bundled → default ON (absent flag decodes to true).
        XCTAssertEqual(PerceptionConfig.default.useLaneSegmentation, true)
        XCTAssertEqual(PerceptionConfig.from(jsonData: json(role: nil))?.useLaneSegmentation, true)
        XCTAssertEqual(PerceptionConfig.default.roadBackend, .twinlite)
        XCTAssertEqual(PerceptionConfig.from(jsonData: json(role: nil))?.roadBackend, .twinlite)
    }

    func testPerceptionConfigWireParsesMulticlassFlag() {
        let jsonOn = Data("""
        {
          "version": 2,
          "use_multiclass_segmentation": true,
          "thresholds": {
            "seg_traversable_pixel": 0.55
          }
        }
        """.utf8)
        XCTAssertEqual(PerceptionConfig.from(jsonData: jsonOn)?.useMulticlassSegmentation, true)
    }

    func testPerceptionConfigWireParsesLaneFlagOffOverride() {
        let jsonOff = Data("""
        {
          "version": 2,
          "use_lane_segmentation": false,
          "thresholds": {
            "seg_traversable_pixel": 0.55
          }
        }
        """.utf8)
        XCTAssertEqual(PerceptionConfig.from(jsonData: jsonOff)?.useLaneSegmentation, false)
    }

    func testPerceptionConfigWireParsesRoadBackend() {
        func json(_ backend: String) -> Data {
            Data("""
            {
              "version": 2,
              "road_backend": "\(backend)",
              "thresholds": {
                "seg_traversable_pixel": 0.55
              }
            }
            """.utf8)
        }
        XCTAssertEqual(PerceptionConfig.from(jsonData: json("mc5"))?.roadBackend, .mc5)
        XCTAssertEqual(PerceptionConfig.from(jsonData: json("off"))?.roadBackend, .off)
        XCTAssertNil(PerceptionConfig.from(jsonData: json("ufld")))
    }

    // MARK: - UFLDv2 lane polyline decoding

    func testUFLDv2RowAnchorsDecodeIntoNormalizedLanePolyline() {
        var loc = Array(repeating: 0.0, count: 5 * 3 * 4)
        var exists = Array(repeating: 0.0, count: 2 * 3 * 4)
        for row in 0..<3 {
            loc[(3 * 3 * 4) + (row * 4) + 1] = 8.0
            exists[(1 * 3 * 4) + (row * 4) + 1] = 8.0
        }

        let lanes = UFLDv2LaneDecoder.decodeRowAnchors(
            locRow: loc,
            existRow: exists,
            numGrid: 5,
            numRows: 3,
            numLanes: 4,
            rowAnchors: [0.42, 0.71, 1.0],
            laneIndices: [1],
            gateDivisor: 2.0
        )

        XCTAssertEqual(lanes.count, 1)
        XCTAssertEqual(lanes[0].source, .rowAnchor)
        XCTAssertEqual(lanes[0].laneIndex, 1)
        XCTAssertEqual(lanes[0].points.count, 3)
        XCTAssertEqual(lanes[0].points[0].x, 0.875, accuracy: 0.02)
        XCTAssertEqual(lanes[0].points[0].y, 0.42, accuracy: 0.001)
        XCTAssertEqual(lanes[0].points[2].y, 1.0, accuracy: 0.001)
    }

    func testUFLDv2ColAnchorsDecodeIntoNormalizedLanePolyline() {
        var loc = Array(repeating: 0.0, count: 5 * 3 * 4)
        var exists = Array(repeating: 0.0, count: 2 * 3 * 4)
        for col in 0..<3 {
            loc[(2 * 3 * 4) + (col * 4) + 0] = 8.0
            exists[(1 * 3 * 4) + (col * 4) + 0] = 8.0
        }

        let lanes = UFLDv2LaneDecoder.decodeColAnchors(
            locCol: loc,
            existCol: exists,
            numGrid: 5,
            numCols: 3,
            numLanes: 4,
            colAnchors: [0.0, 0.5, 1.0],
            laneIndices: [0],
            gateDivisor: 2.0
        )

        XCTAssertEqual(lanes.count, 1)
        XCTAssertEqual(lanes[0].source, .colAnchor)
        XCTAssertEqual(lanes[0].laneIndex, 0)
        XCTAssertEqual(lanes[0].points.count, 3)
        XCTAssertEqual(lanes[0].points[0].x, 0.0, accuracy: 0.001)
        XCTAssertEqual(lanes[0].points[1].x, 0.5, accuracy: 0.001)
        XCTAssertEqual(lanes[0].points[0].y, 0.625, accuracy: 0.02)
    }

    func testUFLDv2DecoderDoesNotFabricateLaneWhenExistenceGateFails() {
        var loc = Array(repeating: 0.0, count: 5 * 3 * 4)
        var exists = Array(repeating: 0.0, count: 2 * 3 * 4)
        loc[(3 * 3 * 4) + (0 * 4) + 1] = 8.0
        exists[(1 * 3 * 4) + (0 * 4) + 1] = 8.0

        let lanes = UFLDv2LaneDecoder.decodeRowAnchors(
            locRow: loc,
            existRow: exists,
            numGrid: 5,
            numRows: 3,
            numLanes: 4,
            rowAnchors: [0.42, 0.71, 1.0],
            laneIndices: [1],
            gateDivisor: 2.0
        )

        XCTAssertTrue(lanes.isEmpty)
    }

    func testUFLDPolishRemovesIsolatedRowSpike() {
        var points: [CGPoint] = []
        for i in 0..<16 {
            let t = Double(i) / 15.0
            var x = 0.30 + 0.10 * t
            if i == 8 { x = 0.42 }
            points.append(CGPoint(x: x, y: 0.50 + 0.50 * t))
        }
        let polished = UFLDv2LanePolisher.polish(points)
        XCTAssertGreaterThanOrEqual(polished.count, 8)
        XCTAssertFalse(polished.contains { abs($0.x - 0.42) < 0.002 })
        let mid = polished[polished.count / 2].x
        XCTAssertLessThan(mid, 0.40)
    }

    func testUFLDPolishLeavesShortPolylineUntouched() {
        let points = [
            CGPoint(x: 0.20, y: 0.50),
            CGPoint(x: 0.22, y: 0.70),
            CGPoint(x: 0.24, y: 0.90),
        ]
        XCTAssertEqual(UFLDv2LanePolisher.polish(points), points)
    }

    func testUFLDPolishKeepsLongerFragmentAfterIdentityJump() {
        var points: [CGPoint] = []
        for i in 0..<10 {
            points.append(CGPoint(x: 0.52, y: 0.55 + 0.015 * Double(i)))
        }
        for i in 0..<24 {
            points.append(CGPoint(x: 0.32 - 0.004 * Double(i), y: 0.70 + 0.012 * Double(i)))
        }
        let polished = UFLDv2LanePolisher.polish(points)
        let xs = polished.map(\.x)
        XCTAssertLessThan(xs.max() ?? 1, 0.40)
        XCTAssertGreaterThan(xs.min() ?? 0, 0.15)
    }

    func testUFLDPolishSkipsColAnchorLanes() {
        let col = LanePolyline(
            laneIndex: 0,
            source: .colAnchor,
            points: (0..<16).map { i in
                let t = Double(i) / 15.0
                return CGPoint(x: t, y: i == 8 ? 0.80 : 0.40)
            }
        )
        let out = UFLDv2LanePolisher.polishRowEgoLanes([col])
        XCTAssertEqual(out[0].points, col.points)
    }

    // MARK: - UFLD polylines → guidance path (Phase B)

    func testUFLDMidlineFromEgoLanePair() {
        let left = LanePolyline(
            laneIndex: 1,
            source: .rowAnchor,
            points: [
                CGPoint(x: 0.40, y: 0.70),
                CGPoint(x: 0.42, y: 0.85),
                CGPoint(x: 0.44, y: 1.0),
            ]
        )
        let right = LanePolyline(
            laneIndex: 2,
            source: .rowAnchor,
            points: [
                CGPoint(x: 0.60, y: 0.70),
                CGPoint(x: 0.58, y: 0.85),
                CGPoint(x: 0.56, y: 1.0),
            ]
        )

        let path = GuidancePathBuilder.fromUFLDPolylines([left, right])
        XCTAssertNotNil(path)
        XCTAssertEqual(path?.status, .ok)
        XCTAssertEqual(path?.source, "ufld")
        let points = path?.primary?.points ?? []
        XCTAssertGreaterThanOrEqual(points.count, 3)
        XCTAssertEqual(points[0].x, 0.50, accuracy: 0.02)
        XCTAssertEqual(points[0].y, 0.30, accuracy: 0.02)
        XCTAssertEqual(points.last?.x ?? 0, 0.50, accuracy: 0.02)
        XCTAssertEqual(points.last?.y ?? 0, 0.0, accuracy: 0.02)
    }

    func testUFLDFallsBackToTwinLiteWhenNoPolylines() {
        let path = GuidancePathBuilder.fromUFLDPolylines([])
        XCTAssertNil(path)
    }

    func testUFLDSingleBoundaryOffsetsInward() {
        let leftOnly = LanePolyline(
            laneIndex: 1,
            source: .rowAnchor,
            points: [
                CGPoint(x: 0.40, y: 0.70),
                CGPoint(x: 0.42, y: 0.85),
                CGPoint(x: 0.44, y: 1.0),
            ]
        )

        let path = GuidancePathBuilder.fromUFLDPolylines([leftOnly])
        XCTAssertNotNil(path)
        let points = path?.primary?.points ?? []
        XCTAssertGreaterThanOrEqual(points.count, 3)
        XCTAssertGreaterThan(points[0].x, 0.40)
        XCTAssertLessThan(points[0].x, 0.50)
    }

}
