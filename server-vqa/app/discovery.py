import os
import socket
import subprocess
from typing import Optional


SERVICE_TYPE = "_vqasee._tcp.local."
SERVICE_NAME = "VQASee Mac VQA._vqasee._tcp.local."
DEFAULT_SIGNALING_PATH = "/ws/signaling"


def _get_lan_ip() -> Optional[str]:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
    except OSError:
        return None

    if ip.startswith("127."):
        return None
    return ip


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

        service_info = ServiceInfo(
            SERVICE_TYPE,
            SERVICE_NAME,
            addresses=addresses,
            port=port,
            properties=properties,
            server=server,
        )
        zeroconf = Zeroconf()
        zeroconf.register_service(service_info)

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
        try:
            self._dns_sd_process = subprocess.Popen(
                [
                    "dns-sd",
                    "-R",
                    "VQASee Mac VQA",
                    "_vqasee._tcp",
                    "local",
                    str(port),
                    f"path={DEFAULT_SIGNALING_PATH}",
                    "role=vqa-backend",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"Bonjour advertised via dns-sd: {SERVICE_NAME} port={port}")
        except OSError:
            print(
                "Bonjour discovery disabled: missing optional dependency 'zeroconf' "
                "and macOS dns-sd fallback is unavailable."
            )
