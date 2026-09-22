import os
import socket
import subprocess
from typing import Optional


SERVICE_TYPE = "_vqasee._tcp.local."
SERVICE_NAME = "VQASee Mac VQA._vqasee._tcp.local."
DEFAULT_SIGNALING_PATH = "/ws/signaling"


def _get_interface_ipv4(interface: str) -> Optional[str]:
    """Return IPv4 for a named interface on macOS, or None."""
    try:
        completed = subprocess.run(
            ["ipconfig", "getifaddr", interface],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    ip = completed.stdout.strip()
    if not ip or ip.startswith("127."):
        return None
    return ip


def _route_based_lan_ip() -> Optional[str]:
    """Best-effort LAN IP via default route; may pick VPN when active."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
    except OSError:
        return None

    if ip.startswith("127."):
        return None
    return ip


def _get_lan_ip() -> Optional[str]:
    # Prefer Wi‑Fi (en0) over the 8.8.8.8 route trick, which often returns a
    # VPN tunnel address the iPhone cannot reach on the same LAN.
    for interface in ("en0", "en1", "bridge0"):
        ip = _get_interface_ipv4(interface)
        if ip:
            return ip
    return _route_based_lan_ip()


class BonjourAdvertiser:
    def __init__(self) -> None:
        self._zeroconf = None
        self._service_info = None
        self._dns_sd_process = None

    def start(self, port: int) -> None:
        if os.getenv("VQASEE_DISABLE_BONJOUR", "0") == "1":
            return

        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError:
            self._start_dns_sd_fallback(port=port)
            return

        lan_ip = _get_lan_ip()
        addresses = []
        if lan_ip:
            addresses.append(socket.inet_aton(lan_ip))

        hostname = socket.gethostname().split(".")[0] or "vqasee-mac"
        server = f"{hostname}.local."
        properties = {
            "path": DEFAULT_SIGNALING_PATH,
            "role": "vqa-backend",
        }
        if lan_ip:
            properties["ip"] = lan_ip

        service_info = ServiceInfo(
            SERVICE_TYPE,
            SERVICE_NAME,
            addresses=addresses,
            port=port,
            properties=properties,
            server=server,
        )

        # LAN auto-advertise is a best-effort convenience so the iPhone can find
        # this Mac automatically. If mDNS registration fails (zeroconf
        # EventLoopBlocked/timeout, no multicast route, firewall), we must NOT
        # take down the whole server: degrade loudly and keep serving. The iPhone
        # can still connect via a manually entered IP or the relay. This is an
        # explicit best-effort subsystem, so we log the failure instead of
        # re-raising (see AGENTS「不可妥协原则 4」).
        zeroconf = None
        try:
            zeroconf = Zeroconf()
            zeroconf.register_service(service_info)
        except Exception as exc:  # noqa: BLE001 - any mDNS failure must be non-fatal
            if zeroconf is not None:
                try:
                    zeroconf.close()
                except Exception:  # noqa: BLE001 - cleanup best-effort
                    pass
            print(
                f"Bonjour discovery unavailable ({type(exc).__name__}: {exc}). "
                "Server continues WITHOUT LAN auto-advertise; connect the iPhone "
                "via manual IP or relay. Set VQASEE_DISABLE_BONJOUR=1 to skip this."
            )
            return

        self._zeroconf = zeroconf
        self._service_info = service_info
        print(f"Bonjour advertised: {SERVICE_NAME} port={port} ip={lan_ip or server}")

    def stop(self) -> None:
        if self._dns_sd_process is not None:
            self._dns_sd_process.terminate()
            self._dns_sd_process = None

        if self._zeroconf is None or self._service_info is None:
            return
        self._zeroconf.unregister_service(self._service_info)
        self._zeroconf.close()
        self._zeroconf = None
        self._service_info = None

    def _start_dns_sd_fallback(self, port: int) -> None:
        lan_ip = _get_lan_ip()
        txt_records = [
            f"path={DEFAULT_SIGNALING_PATH}",
            "role=vqa-backend",
        ]
        if lan_ip:
            txt_records.append(f"ip={lan_ip}")
        try:
            self._dns_sd_process = subprocess.Popen(
                [
                    "dns-sd",
                    "-R",
                    "VQASee Mac VQA",
                    "_vqasee._tcp",
                    "local",
                    str(port),
                    *txt_records,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"Bonjour advertised via dns-sd: {SERVICE_NAME} port={port} ip={lan_ip or 'unknown'}")
        except OSError:
            print(
                "Bonjour discovery disabled: missing optional dependency 'zeroconf' "
                "and macOS dns-sd fallback is unavailable."
            )
