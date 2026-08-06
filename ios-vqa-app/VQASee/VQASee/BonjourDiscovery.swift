import Foundation

// MARK: - Bonjour discovery
//
// `NearbyServerBrowser` extracted verbatim from ContentView.swift. Continuously
// browses `_vqasee._tcp` on the local network, de-duplicates resolved backends,
// and prefers a numeric IPv4 address over the flaky `.local` hostname.

final class NearbyServerBrowser: NSObject, NetServiceBrowserDelegate, NetServiceDelegate {
    /// Fires with the full, de-duplicated set of resolved backends whenever it
    /// changes (a service resolved, or a service dropped off the network). The
    /// ViewModel decides what to do with the set (auto-fill vs. choose).
    var onServersChanged: (([DiscoveredServer]) -> Void)?
    var onStatusChanged: ((String) -> Void)?

    private let browser = NetServiceBrowser()
    /// Services currently being resolved; retained so resolution can complete.
    private var pendingServices: [NetService] = []
    /// Resolved backends keyed by "host:port" so re-resolves don't create dupes.
    private var resolved: [String: DiscoveredServer] = [:]
    private var isBrowsing = false

    func start() {
        guard !isBrowsing else {
            return
        }
        isBrowsing = true
        browser.delegate = self
        onStatusChanged?("正在寻找附近的 Mac 后端…")
        browser.searchForServices(ofType: "_vqasee._tcp.", inDomain: "local.")
    }

    func stop() {
        browser.stop()
        pendingServices.removeAll()
        resolved.removeAll()
        isBrowsing = false
    }

    private func emitServers() {
        let servers = resolved.values.sorted { $0.name < $1.name }
        DispatchQueue.main.async {
            self.onServersChanged?(servers)
        }
    }

    func netServiceBrowserWillSearch(_ browser: NetServiceBrowser) {
        DispatchQueue.main.async {
            self.onStatusChanged?("正在寻找附近的 Mac 后端…")
        }
    }

    func netServiceBrowser(_ browser: NetServiceBrowser, didNotSearch errorDict: [String: NSNumber]) {
        DispatchQueue.main.async {
            self.onStatusChanged?("无法搜索本地网络，请确认已允许本地网络权限。")
        }
    }

    func netServiceBrowser(
        _ browser: NetServiceBrowser,
        didFind service: NetService,
        moreComing: Bool
    ) {
        pendingServices.append(service)
        service.delegate = self
        service.resolve(withTimeout: 5)
    }

    func netServiceBrowser(
        _ browser: NetServiceBrowser,
        didRemove service: NetService,
        moreComing: Bool
    ) {
        // A backend went away — drop it from both the pending and resolved sets so
        // the discovery list shrinks instead of only ever growing. The removed
        // service may no longer resolve to a host:port, so match on its name.
        pendingServices.removeAll { $0 === service }
        if let existingKey = resolved.first(where: { $0.value.name == service.name })?.key {
            resolved.removeValue(forKey: existingKey)
            emitServers()
        }
    }

    func netServiceDidResolveAddress(_ sender: NetService) {
        let path = Self.path(from: sender.txtRecordData()) ?? "/ws/signaling"
        let host = Self.preferredHost(for: sender)
        guard let url = URL(string: "ws://\(host):\(sender.port)\(path)") else {
            return
        }

        let key = "\(host):\(sender.port)"
        resolved[key] = DiscoveredServer(name: sender.name, url: url)
        DispatchQueue.main.async {
            self.onStatusChanged?("已发现 Mac 后端：\(host)")
        }
        emitServers()
    }

    func netService(_ sender: NetService, didNotResolve errorDict: [String: NSNumber]) {
        pendingServices.removeAll { $0 === sender }
        DispatchQueue.main.async {
            self.onStatusChanged?("发现 Mac 后端但解析失败，正在继续搜索…")
        }
    }

    /// Prefer a numeric IPv4 address (more reliable across routers where `.local`
    /// mDNS resolution is flaky) and fall back to the `.local` hostname.
    private static func preferredHost(for service: NetService) -> String {
        if let addresses = service.addresses {
            for address in addresses {
                if let ipv4 = SockaddrParser.ipv4String(fromSockaddr: address) {
                    return ipv4
                }
            }
        }
        let rawHost = service.hostName ?? "\(service.name).local"
        return rawHost.hasSuffix(".") ? String(rawHost.dropLast()) : rawHost
    }

    private static func path(from txtRecordData: Data?) -> String? {
        guard let txtRecordData else {
            return nil
        }
        let dictionary = NetService.dictionary(fromTXTRecord: txtRecordData)
        guard
            let data = dictionary["path"],
            let path = String(data: data, encoding: .utf8),
            path.hasPrefix("/")
        else {
            return nil
        }
        return path
    }
}
