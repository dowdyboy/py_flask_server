"""Probe: JSON logging and file logging (request_id correlation)."""

import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, Server, TMP_DIR, base_env, free_port  # noqa: E402

port = free_port()
with Server('log-json', port, base_env(SERVER_PORT=port, LOG_FORMAT='json',
                                       LOG_TO_CONSOLE='true', LOG_FILE_PATH='')) as srv:
    r = requests.get(srv.base + '/missing.js', headers={'X-Request-Id': 'log-probe-1'}, timeout=5)
    print('status', r.status_code, 'rid', r.headers.get('X-Request-Id'))
    lines = [ln for ln in srv.logs().splitlines() if ln.strip().startswith('{')]
    parsed = []
    for ln in lines:
        try:
            parsed.append(json.loads(ln))
        except Exception:
            pass
    print('json lines', len(parsed))
    hit = [p for p in parsed if p.get('request_id') == 'log-probe-1']
    print('request_id hit', len(hit), hit[:1])

log_path = TMP_DIR / 'file-log-probe.log'
port = free_port()
with Server('log-file', port, base_env(SERVER_PORT=port, LOG_FORMAT='text',
                                       LOG_TO_CONSOLE='false', LOG_FILE_PATH=str(log_path))) as srv:
    requests.get(srv.base + '/missing.js', headers={'X-Request-Id': 'file-probe-1'}, timeout=5)
    text = log_path.read_text(encoding='utf-8') if log_path.exists() else ''
    print('file exists', log_path.exists(), 'size', len(text), 'rid', 'file-probe-1' in text)
log_path.unlink(missing_ok=True)
