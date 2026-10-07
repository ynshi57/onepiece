import Foundation
import Combine
import Security
import UIKit

/// Discovery is separate from screen capture. Selecting a Mac never starts recording.
@MainActor
final class DeviceDebugConnection: NSObject, ObservableObject, NetServiceBrowserDelegate, NetServiceDelegate, URLSessionTaskDelegate {
    struct Mac: Identifiable { let id: String; let name: String; let url: String }
    @Published private(set) var macs: [Mac] = []
    @Published private(set) var message = "正在寻找附近的 Mac…"
    @Published private(set) var selected: Mac?
    @Published private(set) var code = ""
    @Published private(set) var credential: String?
    @Published private(set) var waiting = false
    private let browser = NetServiceBrowser()
    private var services: [NetService] = []
    private var task: Task<Void, Never>?
    private var browsing = false
    private var attempt = UUID()
    private var pendingRequest: (Mac, String, String)?
    private var discoveryTimeout: Task<Void, Never>?
    private lazy var network: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 8
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()
    func startDiscovery() {
        guard !browsing else { return }
        browsing = true; browser.delegate = self
        browser.searchForServices(ofType: "_vqasee-debug._tcp.", inDomain: "local.")
        discoveryTimeout = Task {
            try? await Task.sleep(for: .seconds(10))
            guard !Task.isCancelled, macs.isEmpty, selected == nil else { return }
            message = "还没找到 Mac。请打开 Mac 真机调试页面，确认同一 Wi-Fi，再点重新查找。"
        }
    }
    func stopDiscovery() {
        browser.stop(); services.forEach { $0.stop() }; services = []; browsing = false
        discoveryTimeout?.cancel()
    }
    func retry() {
        stopDiscovery(); macs = []; message = "正在重新寻找 Mac…"; startDiscovery()
    }
    func cancel() {
        if let (mac, id, secret) = pendingRequest {
            pendingRequest = nil
            Task { _ = try? await request(mac, "/pairing/requests/\(id)", secret: secret, method: "DELETE") }
        }
        attempt = UUID(); task?.cancel(); task = nil; waiting = false; code = ""
        credential = nil; selected = nil; message = "请选择附近的 Mac"
    }
    func select(_ mac: Mac) {
        cancel(); selected = mac; waiting = true; message = "正在连接 \(mac.name)…"
        let current = attempt
        task = Task {
            do {
                if let saved = readCredential(mac.id) {
                    do {
                        _ = try await request(mac, "/pairing/me", token: saved)
                        guard current == attempt else { return }
                        credential = saved; waiting = false; message = "已连接 \(mac.name)"; return
                    } catch let error as NSError where error.domain == "DebugHTTP" && error.code == 401 {
                        try saveCredential(nil, id: mac.id)
                    }
                }
                let reply = try await request(mac, "/pairing/requests", body: ["device_name": UIDevice.current.name])
                guard let id = reply["id"] as? String, let secret = reply["request_secret"] as? String,
                      let number = reply["code"] as? String else { throw failure("Mac 的响应无法识别，请更新调试服务") }
                guard current == attempt else {
                    _ = try? await request(mac, "/pairing/requests/\(id)", secret: secret, method: "DELETE")
                    return
                }
                pendingRequest = (mac, id, secret)
                code = number; message = "请在 Mac 点击「允许连接」，核对以下数字"
                for _ in 0..<150 {
                    try await Task.sleep(for: .seconds(2))
                    let result: [String: Any]
                    do { result = try await request(mac, "/pairing/requests/\(id)", secret: secret) }
                    catch let error as URLError where [.timedOut, .networkConnectionLost, .notConnectedToInternet, .cannotConnectToHost].contains(error.code) {
                        message = "连接暂时中断，正在重试…"; continue
                    }
                    guard current == attempt else { return }
                    if result["status"] as? String == "denied" { throw failure("Mac 拒绝了连接，可重新选择 Mac") }
                    if result["status"] as? String == "approved", let token = result["device_token"] as? String {
                        try saveCredential(token, id: mac.id)
                        pendingRequest = nil
                        credential = token; code = ""; waiting = false; message = "已连接 \(mac.name)"; return
                    }
                }
                throw failure("等待确认已超时，请重新选择 Mac")
            } catch {
                guard current == attempt, !Task.isCancelled else { return }
                waiting = false; code = ""; credential = nil
                message = error.localizedDescription
            }
        }
    }
    func validatedCredential() async -> String? {
        guard let mac = selected, let saved = credential, !waiting else { return nil }
        let current = attempt; waiting = true
        defer { if current == attempt { waiting = false } }
        do {
            _ = try await request(mac, "/pairing/me", token: saved)
            guard current == attempt else { return nil }
            message = "已连接 \(mac.name)"; return saved
        } catch {
            guard current == attempt else { return nil }
            if let error = error as NSError?, error.domain == "DebugHTTP", error.code == 401 {
                try? saveCredential(nil, id: mac.id); credential = nil
                message = "Mac 已移除授权，请重新选择 Mac 并允许连接。"
            } else { message = "暂时连接不上 Mac，请检查同一 Wi-Fi 后重试。" }
            return nil
        }
    }
    private func failure(_ message: String) -> NSError { NSError(domain: "DebugConnection", code: 1, userInfo: [NSLocalizedDescriptionKey: message]) }
    private func request(_ mac: Mac, _ path: String, body: [String: String]? = nil, token: String? = nil, secret: String? = nil, method: String = "GET") async throws -> [String: Any] {
        guard let url = URL(string: mac.url + "/device-debug" + path) else { throw failure("无法连接这台 Mac") }
        var request = URLRequest(url: url)
        request.httpMethod = method
        if let body { request.httpMethod = "POST"; request.httpBody = try JSONSerialization.data(withJSONObject: body); request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        if let token { request.setValue(token, forHTTPHeaderField: "X-Device-Debug-Token") }
        if let secret { request.setValue(secret, forHTTPHeaderField: "X-Pairing-Secret") }
        let (data, response) = try await network.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw failure("Mac 未响应，请检查同一 Wi-Fi") }
        guard response.statusCode == 200 else {
            let message = response.statusCode == 410 ? "确认已过期，请重新选择 Mac" : response.statusCode == 429 ? "连接请求过多，请稍后再试" : "Mac 无法完成连接，请重试"
            throw NSError(domain: "DebugHTTP", code: response.statusCode, userInfo: [NSLocalizedDescriptionKey: message])
        }
        guard let value = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw failure("Mac 响应格式不正确") }
        return value
    }
    private func key(_ id: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "VQASee.DeviceDebug", kSecAttrAccount as String: id]
    }
    private func readCredential(_ id: String) -> String? {
        var query = key(id); query[kSecReturnData as String] = true
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
    private func saveCredential(_ value: String?, id: String) throws {
        let query = key(id); let deletion = SecItemDelete(query as CFDictionary)
        guard deletion == errSecSuccess || deletion == errSecItemNotFound else { throw failure("无法更新本机连接记录，请重试") }
        guard let value else { return }
        var item = query
        item[kSecValueData as String] = Data(value.utf8)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else { throw failure("无法保存连接记录，请重试") }
    }
    nonisolated func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
    func netServiceBrowser(_ browser: NetServiceBrowser, didFind service: NetService, moreComing: Bool) {
        services.append(service); service.delegate = self; service.resolve(withTimeout: 8)
    }
    func netServiceBrowser(_ browser: NetServiceBrowser, didRemove service: NetService, moreComing: Bool) {
        macs.removeAll { $0.id == service.name + service.domain }; services.removeAll { $0.name == service.name }
    }
    func netServiceBrowser(_ browser: NetServiceBrowser, didNotSearch errorDict: [String: NSNumber]) {
        message = "无法查找 Mac。请在 iPhone 设置中允许 VQASee 使用本地网络，再重新查找。"
    }
    func netServiceDidResolveAddress(_ sender: NetService) {
        guard let hostname = sender.hostName, sender.port > 0 else { return }
        let id = sender.name + sender.domain
        macs.removeAll { $0.id == id }
        macs.append(Mac(id: id, name: sender.name.replacingOccurrences(of: " VQASee", with: ""), url: "http://\(hostname):\(sender.port)"))
        macs.sort { $0.name < $1.name }
        if selected == nil { message = "选择要接收画面的 Mac" }
    }
    func netService(_ sender: NetService, didNotResolve errorDict: [String: NSNumber]) { message = "发现了 Mac，但暂时连接不上。请检查同一 Wi-Fi，再重新查找。" }
}
