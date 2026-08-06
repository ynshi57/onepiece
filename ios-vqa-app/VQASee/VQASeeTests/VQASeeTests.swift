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
                "scene": "city street",
                "objects": ["car", "traffic_light"],
                "description": "cars on road",
                "latency_ms": 123.4
            ]
        )

        guard case let .vqaResult(result) = response else {
            return XCTFail("expected vqaResult, got \(response)")
        }
        XCTAssertEqual(result.scene, "city street")
        XCTAssertEqual(result.objects, ["car", "traffic_light"])
        XCTAssertEqual(result.description, "cars on road")
        XCTAssertEqual(result.latencyMs, 123.4)
    }

    func testFrameMessageBuilderIncludesBase64AndGPS() {
        let frameData = Data([0x01, 0x02, 0x03])
        let payload = FrameMessageBuilder.build(
            frameID: "frame-123",
            prompt: "road scene",
            model: "qwen2.5vl:3b",
            jpegData: frameData,
            gps: (lat: 37.33, lon: -122.02)
        )

        XCTAssertEqual(payload["type"] as? String, "frame")
        XCTAssertEqual(payload["frame_id"] as? String, "frame-123")
        XCTAssertEqual(payload["prompt"] as? String, "road scene")
        XCTAssertEqual(payload["model"] as? String, "qwen2.5vl:3b")
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

        let changed = LocalVisionSignal(
            hasHuman: false, humanDirection: .unknown, brightness: 0.5,
            sceneChangeScore: WalkingFrameSendPolicy.sceneChangeThreshold,
            isTooDark: false, isLikelyCovered: false, analyzerFailed: false
        )
        if case .send = WalkingFrameSendPolicy.decide(
            mode: .walking, signal: changed, hasQuestion: false, pendingSingleShot: false,
            millisecondsSinceLastBackendFrame: 1_000
        ) {} else { XCTFail("scene change should trigger backend") }

        let dark = LocalVisionSignal(
            hasHuman: false, humanDirection: .unknown, brightness: 0.05,
            sceneChangeScore: 0.01, isTooDark: true, isLikelyCovered: false, analyzerFailed: false
        )
        if case .send = WalkingFrameSendPolicy.decide(
            mode: .walking, signal: dark, hasQuestion: false, pendingSingleShot: false,
            millisecondsSinceLastBackendFrame: 1_000
        ) {} else { XCTFail("quality risk should trigger backend") }

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

}
