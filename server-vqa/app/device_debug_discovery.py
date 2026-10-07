"""Dedicated Bonjour identity; failed advertisement stays visible to the Mac UI."""
import os
import socket
import subprocess
from app.discovery import _get_lan_ip
from app.device_debug_pairing import DISCOVERY

def mac_names():
    def setting(key, fallback):
        try:
            return subprocess.check_output(['scutil', '--get', key], text=True, stderr=subprocess.DEVNULL).strip() or fallback
        except (OSError, subprocess.CalledProcessError):
            return fallback
    return setting('ComputerName', 'VQASee Mac'), setting('LocalHostName', 'vqasee-debug')

class DeviceDebugAdvertiser:
    def __init__(self):
        self.service = None
        self.zeroconf = None

    def start(self, port):
        if os.environ.get('VQASEE_DISABLE_BONJOUR') == '1':
            DISCOVERY.update(state='disabled', message='附近发现已由运行配置关闭')
            return
        try:
            from zeroconf import ServiceInfo, Zeroconf
            ip = _get_lan_ip()
            if not ip: raise RuntimeError('没有可用局域网地址')
            display, hostname = mac_names()
            kind = '_vqasee-debug._tcp.local.'
            self.service = ServiceInfo(kind, f'{display} VQASee.{kind}',
                addresses=[socket.inet_aton(ip)], port=port,
                properties={'path':'/device-debug','version':'1'}, server=f'{hostname}.local.')
            self.zeroconf = Zeroconf()
            self.zeroconf.register_service(self.service, allow_name_change=True)
            DISCOVERY.update(state='ready', message='正在等待附近的 iPhone', name=self.service.name, display_name=display)
        except Exception as error:
            self.stop()
            DISCOVERY.update(state='failed', message=f'附近发现未启动：{error}。请检查 Wi-Fi 与系统防火墙。')

    def stop(self):
        if self.zeroconf:
            try:
                if self.service: self.zeroconf.unregister_service(self.service)
            finally:
                self.zeroconf.close(); self.zeroconf=None
