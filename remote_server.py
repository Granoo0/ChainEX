"""
remote_server.py — Lightweight LAN dashboard for ChainEX.

Serves a single mobile-friendly HTML page on every network interface so the
bot can be monitored and controlled from a phone on the same Wi-Fi network.

Endpoints
---------
GET  /        – Dashboard HTML
GET  /stats   – JSON stats snapshot (polled by the page every second)
POST /cmd     – Accept {"action": "start"|"stop"|"pause"} from the page
GET  /favicon.ico – 204 No Content (suppress browser errors)
"""

import json
import logging
import queue
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

_log = logging.getLogger("G Panel.Remote")

# ── Mobile-friendly dashboard HTML ────────────────────────────────────────────
_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>ChainEX Remote</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0A0E1A;color:#E2E8F0;font-family:'Segoe UI',system-ui,sans-serif;
     min-height:100dvh;padding:16px;max-width:500px;margin:0 auto}
.topbar{display:flex;align-items:center;gap:10px;margin-bottom:18px;
        padding-bottom:14px;border-bottom:1px solid #1E293B}
.logo{font-size:20px;font-weight:700;color:#22D3EE;flex:1;letter-spacing:.5px}
.badge{padding:4px 12px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.5px}
.idle   {background:#1E293B;color:#64748B}
.running{background:#064E3B;color:#10B981}
.paused {background:#422006;color:#FACC15}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px}
.card{background:#131829;border:1px solid #1E293B;border-radius:10px;padding:12px 14px}
.clabel{font-size:9px;font-weight:700;color:#64748B;letter-spacing:1px;
        text-transform:uppercase;margin-bottom:4px}
.cval{font-size:22px;font-weight:700;color:#22D3EE;font-family:Consolas,monospace;line-height:1.1}
.csub{font-size:10px;color:#94A3B8;margin-top:2px}
.step-card{background:#131829;border:1px solid #1E293B;border-radius:10px;
           padding:12px 14px;margin-bottom:14px}
.step-val{font-family:Consolas,monospace;font-size:13px;color:#E2E8F0;
          word-break:break-all;min-height:18px}
.controls{display:flex;flex-direction:column;gap:8px}
.btn{padding:15px;border:none;border-radius:10px;font-size:15px;font-weight:700;
     cursor:pointer;width:100%;letter-spacing:.3px;transition:filter .12s}
.btn:active:not(:disabled){filter:brightness(.85)}
.btn:disabled{opacity:.3;cursor:not-allowed}
.b-start{background:#10B981;color:#000}
.b-stop {background:#F43F5E;color:#fff}
.b-pause{background:#FACC15;color:#000}
.footer{margin-top:18px;text-align:center;font-size:10px;color:#334155}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;
     background:#10B981;margin-right:4px;animation:blink 1.4s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}
</style>
</head>
<body>
<div class="topbar">
  <div class="logo">&#9889; ChainEX</div>
  <span class="badge idle" id="badge">IDLE</span>
</div>
<div class="cards">
  <div class="card">
    <div class="clabel">Loops</div>
    <div class="cval" id="loops">&mdash;</div>
  </div>
  <div class="card">
    <div class="clabel">Session Time</div>
    <div class="cval" id="session">&mdash;</div>
  </div>
  <div class="card">
    <div class="clabel">Avg Loop</div>
    <div class="cval" id="avg">&mdash;</div>
    <div class="csub">seconds</div>
  </div>
  <div class="card">
    <div class="clabel">Last Loop</div>
    <div class="cval" id="last">&mdash;</div>
    <div class="csub">seconds</div>
  </div>
</div>
<div class="step-card">
  <div class="clabel">Current Step</div>
  <div class="step-val" id="step">&mdash;</div>
</div>
<div class="controls">
  <button class="btn b-start" id="btn-start" onclick="send('start')">&#9654;&nbsp; START BOT</button>
  <button class="btn b-stop"  id="btn-stop"  onclick="send('stop')">&#9632;&nbsp; STOP BOT</button>
  <button class="btn b-pause" id="btn-pause" onclick="send('pause')">&#9208;&nbsp; PAUSE</button>
</div>
<div class="footer"><span class="dot"></span>Refreshes every second &nbsp;&middot;&nbsp; ChainEX Remote</div>
<script>
async function send(action){
  try{await fetch('/cmd',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action})})}catch(e){}
}
async function poll(){
  try{
    const d=await(await fetch('/stats')).json();
    const r=!!d.running,p=!!d.paused;
    const b=document.getElementById('badge');
    if(r&&p){b.className='badge paused';b.textContent='PAUSED'}
    else if(r){b.className='badge running';b.textContent='▶ RUNNING'}
    else{b.className='badge idle';b.textContent='IDLE'}
    set('loops',  d.loops!=null?String(d.loops):'--');
    set('session',d.session||'--');
    set('avg',    d.avg_s!=null?d.avg_s:'--');
    set('last',   d.last_s!=null?d.last_s:'--');
    set('step',   d.step||'--');
    dis('btn-start', r);
    dis('btn-stop',  !r);
    dis('btn-pause', !r);
    document.getElementById('btn-pause').textContent=p?'▶  RESUME':'⏸  PAUSE';
  }catch(e){}
}
function set(id,v){const e=document.getElementById(id);if(e)e.textContent=v}
function dis(id,v){const e=document.getElementById(id);if(e)e.disabled=v}
poll();setInterval(poll,1000);
</script>
</body>
</html>"""


class RemoteDashboard:
    """HTTP server exposing a mobile-friendly control page over the local network."""

    def __init__(self, port: int = 8765) -> None:
        self._port       = port
        self._cmd_queue: queue.Queue[str] = queue.Queue()
        self._lock       = threading.Lock()
        self._stats: dict[str, Any] = {
            "running": False,
            "paused":  False,
            "loops":   None,
            "session": None,
            "avg_s":   None,
            "last_s":  None,
            "step":    None,
        }
        self._server: HTTPServer | None  = None
        self._thread: threading.Thread | None = None

    # ── Public API (all called from the main/UI thread) ───────────────────────

    def start(self) -> bool:
        """Bind and launch the background HTTP thread.  Returns True on success."""
        dashboard = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args) -> None:
                pass  # suppress default access log spam

            def do_GET(self) -> None:                          # noqa: N802
                if self.path in ("/", "/index.html"):
                    body = _HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type",   "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control",  "no-store")
                    self.end_headers()
                    self.wfile.write(body)

                elif self.path == "/stats":
                    with dashboard._lock:
                        body = json.dumps(dashboard._stats).encode()
                    self.send_response(200)
                    self.send_header("Content-Type",   "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control",  "no-store")
                    self.end_headers()
                    self.wfile.write(body)

                elif self.path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()

                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self) -> None:                         # noqa: N802
                if self.path == "/cmd":
                    length  = int(self.headers.get("Content-Length", 0))
                    raw     = self.rfile.read(length)
                    try:
                        action = str(json.loads(raw).get("action", "")).strip().lower()
                        if action in ("start", "stop", "pause"):
                            dashboard._cmd_queue.put_nowait(action)
                    except Exception:
                        pass
                    self.send_response(204)
                    self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()

        # allow_reuse_address MUST be set before the socket is bound.
        # HTTPServer.__init__ calls server_bind() internally, so we can't
        # patch the instance afterwards — we need a subclass that sets it
        # at class level (class attributes are read before __init__ runs).
        class _ReuseHTTPServer(HTTPServer):
            allow_reuse_address = True

        try:
            srv = _ReuseHTTPServer(("0.0.0.0", self._port), _Handler)
            self._server = srv
            self._thread = threading.Thread(
                target=srv.serve_forever, daemon=True, name="remote-dash")
            self._thread.start()
            _log.info("Remote dashboard -> http://%s:%d", self.local_ip(), self._port)
            return True
        except OSError as exc:
            _log.warning("Remote dashboard failed to start on port %d: %s",
                         self._port, exc)
            return False

    def stop(self) -> None:
        """Shut down the HTTP server (called on app close)."""
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None

    def update_stats(self, **kwargs: Any) -> None:
        """Thread-safe stat update — call from the main thread's poll timer."""
        with self._lock:
            self._stats.update(kwargs)

    def drain_commands(self) -> list[str]:
        """Return all pending remote commands and empty the queue."""
        cmds: list[str] = []
        while True:
            try:
                cmds.append(self._cmd_queue.get_nowait())
            except queue.Empty:
                break
        return cmds

    @property
    def port(self) -> int:
        return self._port

    @staticmethod
    def local_ip() -> str:
        """Best-effort LAN IP for display (not used for binding)."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.1)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"
