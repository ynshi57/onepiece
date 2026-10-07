"""One-step local pairing launcher. Secret never appears in process arguments or logs."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=9001)
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    with socket.socket() as probe:
        try: probe.bind(('0.0.0.0', args.port))
        except OSError as error:
            raise SystemExit(f'无法监听端口 {args.port}：{error}。请检查系统权限或端口占用。')
    runtime = root / 'build' / 'device-debug'
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime.chmod(0o700)
    token = secrets.token_urlsafe(32)
    host = socket.gethostname()
    try: host = subprocess.check_output(['scutil', '--get', 'LocalHostName'], text=True).strip() + '.local'
    except (OSError, subprocess.CalledProcessError): pass
    url = f'http://127.0.0.1:{args.port}'
    phone_url = f'http://{host}:{args.port}'
    pair = runtime / 'pairing.json'
    fd = os.open(pair, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'url': url, 'phone_url': phone_url, 'token': token}, f)
    os.environ['VQASEE_DEVICE_DEBUG_TOKEN'] = token
    os.environ['VQASEE_DEVICE_DEBUG_PORT'] = str(args.port)
    os.environ.setdefault('VQASEE_DEVICE_DEBUG_ROOT', str(runtime / 'sessions'))
    sys.path.insert(0, str(root / 'server-vqa'))
    print(f'真机调试：{url}/device-debug/ui\n手机与 Mac 同 Wi-Fi，在手机选择这台 Mac，然后在网页允许连接。\n证据保存在 {runtime}/sessions，停止服务按 Ctrl-C。', flush=True)
    if not args.no_open:
        def open_ready():
            for _ in range(60):
                try:
                    with urllib.request.urlopen(url + '/health', timeout=1) as response:
                        if response.status == 200:
                            subprocess.run(['open', url + '/device-debug/ui'], check=False)
                            return
                except (OSError, ValueError): time.sleep(.5)
            print('页面未自动打开，请检查服务输出。', flush=True)
        threading.Thread(target=open_ready, daemon=True).start()
    import uvicorn
    uvicorn.run('app.device_debug_server:app', host='0.0.0.0', port=args.port, access_log=False)

if __name__ == '__main__': main()
