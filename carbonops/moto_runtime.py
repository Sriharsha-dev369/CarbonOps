"""Start the AWS emulator (moto) inside this process when nothing is serving the endpoint yet.

Set CARBONOPS_AWS=real to skip all of this and talk to real AWS (then leave AWS_ENDPOINT_URL unset).
"""

import logging
import os
import socket
import threading
import time
import urllib.request
from urllib.parse import urlparse

from moto.server import ThreadedMotoServer

DEFAULT_ENDPOINT = 'http://localhost:5050'   # not 5000: macOS AirPlay Receiver listens there
DUMMY_ENV = {'AWS_ACCESS_KEY_ID': 'testing', 'AWS_SECRET_ACCESS_KEY': 'testing', 'AWS_DEFAULT_REGION': 'ap-south-1'}

_lock = threading.Lock()
_server: ThreadedMotoServer | None = None


def real_aws() -> bool:
    return os.environ.get('CARBONOPS_AWS', 'moto').lower() == 'real'


def is_sandbox() -> bool:
    """True when AWS calls cannot touch a real account: a local endpoint, or dummy credentials
    ('testing', or 'FOOBARKEY', which moto's in-process mock sets)."""
    if real_aws():
        return False
    host = urlparse(os.environ.get('AWS_ENDPOINT_URL') or '').hostname
    return host in ('localhost', '127.0.0.1') or os.environ.get('AWS_ACCESS_KEY_ID') in ('testing', 'FOOBARKEY')


def _is_moto(url: str) -> bool:
    try:
        with urllib.request.urlopen(f'{url.rstrip("/")}/moto-api/', timeout=1) as r:
            return r.status == 200 and b'moto' in r.read().lower()
    except Exception:
        return False


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def ensure_moto() -> str | None:
    """Make sure an emulator serves AWS_ENDPOINT_URL (default localhost:5050); idempotent.

    Also sets dummy credentials and region ap-south-1 if unset. Returns the endpoint, or None in real-AWS mode.
    """
    global _server
    if real_aws():
        return None
    url = os.environ.get('AWS_ENDPOINT_URL') or DEFAULT_ENDPOINT
    os.environ['AWS_ENDPOINT_URL'] = url
    for key, value in DUMMY_ENV.items():
        os.environ.setdefault(key, value)

    with _lock:
        if _is_moto(url):
            return url
        parsed = urlparse(url)
        host, port = parsed.hostname or 'localhost', parsed.port or 80
        if host not in ('localhost', '127.0.0.1'):
            raise RuntimeError(f'AWS_ENDPOINT_URL {url} is not local and is not answering like moto.')
        if _port_in_use('127.0.0.1', port):
            raise RuntimeError(f'Port {port} is busy with something that is not moto. '
                               f'Free it or set AWS_ENDPOINT_URL to another port (macOS AirPlay uses 5000).')
        logging.getLogger('werkzeug').setLevel(logging.ERROR)   # no per-request log lines in the terminal
        _server = ThreadedMotoServer(ip_address='127.0.0.1', port=port, verbose=False)
        _server.start()
        for _ in range(50):
            if _is_moto(url):
                return url
            time.sleep(0.1)
    raise RuntimeError(f'moto did not start on {url}')
