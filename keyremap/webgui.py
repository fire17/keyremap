"""Browser GUI — stdlib http.server only, so it works on every machine that can
run Python, with no tkinter, no pip, no Electron.

Binds to 127.0.0.1 on an ephemeral port (never exposed to the network), serves a
single self-contained page, and talks to the same config/backends/state as the
TUI. Idle cost is a 1.5 s poll of an in-process status object.
"""

import json
import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as cfgmod
from . import doctor as doctormod
from . import envinfo, state
from .gui import classify_capture_line, mapping_rows, status_line

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>keyremap</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0e1016;--bg2:#151925;--bg3:#1d2231;--fg:#e8ebf2;--muted:#8b93a7;
--accent:#5cc8ff;--good:#5ddba4;--warn:#ffc95c;--bad:#ff6b6b;--lilac:#c3a6ff;
--radius:12px;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif}
header{display:flex;align-items:baseline;gap:12px;padding:20px 26px 14px}
h1{margin:0;font-size:19px;letter-spacing:-.2px}
h1 span{color:var(--accent)}
.sub{color:var(--muted);font-size:13px}
.env{margin-left:auto;color:var(--muted);font-family:var(--mono);font-size:12px}
.strip{display:flex;gap:10px;align-items:center;padding:12px 26px;background:var(--bg2);
border-top:1px solid #0000;border-bottom:1px solid #232838}
.dot{width:9px;height:9px;border-radius:50%;background:var(--warn);
box-shadow:0 0 0 3px #ffc95c22}
.dot.on{background:var(--good);box-shadow:0 0 0 3px #5ddba422}
.meta{margin-left:auto;color:var(--muted);font-family:var(--mono);font-size:12px}
nav{display:flex;gap:4px;padding:14px 26px 0}
nav button{background:none;border:0;color:var(--muted);padding:9px 15px;
border-radius:9px 9px 0 0;cursor:pointer;font-size:13px;font-weight:500}
nav button.active{background:var(--bg3);color:var(--accent)}
main{padding:0 26px 26px}
.card{background:var(--bg2);border-radius:var(--radius);padding:4px 0;overflow:hidden}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--muted);font-weight:600;font-size:11px;
letter-spacing:.7px;text-transform:uppercase;padding:12px 18px}
td{padding:11px 18px;border-top:1px solid #212636;font-family:var(--mono);font-size:12.5px}
tr:hover td{background:#ffffff06}
.key{color:var(--lilac)}
.layer{color:var(--muted)}
.bar{display:flex;gap:9px;padding:16px 26px;align-items:center;flex-wrap:wrap}
button.act{background:var(--bg3);color:var(--fg);border:0;padding:9px 16px;
border-radius:9px;cursor:pointer;font-size:13px}
button.act:hover{background:#273047}
button.pri{background:var(--accent);color:#0a0d13;font-weight:650}
button.pri:hover{background:#7ad4ff}
#flash{color:var(--good);font-size:13px;margin-left:auto}
pre{margin:0;padding:16px 18px;font-family:var(--mono);font-size:12.5px;
max-height:52vh;overflow:auto;white-space:pre-wrap}
.hit{color:var(--good)} .other{color:var(--muted)}
.ok{color:var(--good)} .warn{color:var(--warn)} .fail{color:var(--bad)}
.fix{color:var(--accent);font-size:12px}
.empty{color:var(--muted);padding:22px 18px;font-size:13px}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;background:var(--bg3);
color:var(--muted);font-size:11px;font-family:var(--mono);margin-right:6px}
.pill.on{background:#5ddba41f;color:var(--good)}
</style></head><body>
<header><h1><span>key</span>remap</h1><div class="sub">per-device key mapping</div>
<div class="env" id="env"></div></header>
<div class="strip"><div class="dot" id="dot"></div><div id="device">…</div>
<div class="meta" id="meta"></div></div>
<nav>
 <button data-t="map" class="active">Mappings</button>
 <button data-t="cap">Capture</button>
 <button data-t="doc">Doctor</button>
</nav>
<main>
 <div class="card" id="pane-map"><table><thead><tr>
   <th>Key</th><th>Does</th><th>Layer</th><th>Scancode</th></tr></thead>
   <tbody id="maps"></tbody></table></div>
 <div class="card" id="pane-cap" hidden><pre id="cap">press “Start capture”, then press keys on the device…</pre></div>
 <div class="card" id="pane-doc" hidden><table><thead><tr>
   <th>Check</th><th>Result</th><th>Fix</th></tr></thead>
   <tbody id="docs"></tbody></table>
   <div class="empty" id="docempty">press “Run doctor”</div></div>
</main>
<div class="bar">
 <button class="act pri" onclick="post('apply')">Apply</button>
 <button class="act" onclick="post('reload')">Reload config</button>
 <button class="act" onclick="runDoctor()">Run doctor</button>
 <button class="act" onclick="toggleCap()" id="capbtn">Start capture</button>
 <button class="act" onclick="post('export')">Export…</button>
 <div id="flash"></div>
</div>
<script>
let capturing=false;
const $=s=>document.querySelector(s);
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('nav button').forEach(x=>x.classList.remove('active'));
  b.classList.add('active');
  for(const t of ['map','cap','doc']) $('#pane-'+t).hidden = (t!==b.dataset.t);
});
function flash(m,c){const f=$('#flash');f.textContent=m;f.style.color=c||'var(--good)';
  setTimeout(()=>f.textContent='',4000);}
async function post(action){
  const r=await fetch('/api/'+action,{method:'POST'});const j=await r.json();
  flash(j.message, j.ok?null:'var(--bad)'); refresh();
  if(action==='reload') loadMaps();
}
async function runDoctor(){
  flash('running checks…','var(--warn)');
  const r=await fetch('/api/doctor',{method:'POST'});const rows=await r.json();
  $('#docempty').hidden=true;
  $('#docs').innerHTML=rows.map(([s,l,d,f])=>
    `<tr><td class="${s}">${s==='ok'?'✓':s==='warn'?'!':'✗'} ${l}</td>
     <td>${d}</td><td class="fix">${f||''}</td></tr>`).join('');
  document.querySelector('nav button[data-t=doc]').click();
  flash('doctor complete');
}
async function toggleCap(){
  capturing=!capturing;
  $('#capbtn').textContent=capturing?'Stop capture':'Start capture';
  await fetch('/api/capture?on='+(capturing?1:0),{method:'POST'});
  if(capturing){$('#cap').textContent='';document.querySelector('nav button[data-t=cap]').click();}
}
async function loadMaps(){
  const r=await fetch('/api/mappings');const rows=await r.json();
  $('#maps').innerHTML=rows.map(([k,d,l,s])=>
    `<tr><td class="key">${k}</td><td>${d}</td>
     <td class="layer">${l}</td><td class="layer">${s}</td></tr>`).join('');
}
async function refresh(){
  const r=await fetch('/api/status');const s=await r.json();
  $('#env').textContent=s.env+' · '+s.host;
  $('#device').textContent=s.device;
  $('#dot').className='dot'+(s.present?' on':'');
  $('#meta').innerHTML=s.layers.map(l=>`<span class="pill on">${l}</span>`).join('')
    +' applied '+s.applied+(s.changed?' · config changed':'');
  if(capturing&&s.lines.length){
    const el=$('#cap');
    el.innerHTML+=s.lines.map(([t,c])=>`<span class="${c}">${t}</span>`).join('\\n')+'\\n';
    el.scrollTop=el.scrollHeight;
  }
}
loadMaps();refresh();setInterval(refresh,1200);
</script></body></html>"""


class _State:
    def __init__(self, cfg_path):
        self.cfg_path = cfg_path
        self.cfg = cfgmod.load(cfg_path)
        self.env = envinfo.detect()
        self.lines: list[tuple[str, str]] = []
        self.capture_stop = threading.Event()
        self.capture_thread = None
        self.lock = threading.Lock()

    def start_capture(self):
        if self.capture_thread and self.capture_thread.is_alive():
            return
        self.capture_stop.clear()

        def worker():
            try:
                from .backends import get_backend
                for line in get_backend(self.env).listen(self.cfg, seconds=300):
                    if self.capture_stop.is_set():
                        break
                    with self.lock:
                        self.lines.append(classify_capture_line(self.cfg, line))
            except Exception as e:  # noqa: BLE001
                with self.lock:
                    self.lines.append((f"capture failed: {e}", "fail"))

        self.capture_thread = threading.Thread(target=worker, daemon=True)
        self.capture_thread.start()

    def drain(self):
        with self.lock:
            out, self.lines = self.lines, []
        return out


def _handler(app: _State):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the console clean
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if self.path == "/api/mappings":
                return self._send(200, json.dumps(mapping_rows(app.cfg)))
            if self.path == "/api/status":
                st = state.gather(app.cfg, quick=True)
                return self._send(200, json.dumps({
                    "env": st.env, "host": st.host,
                    "device": st.device_label or "not connected",
                    "present": st.device_present,
                    "layers": app.cfg.layers_applied,
                    "applied": st.deployment.applied_ago,
                    "changed": st.config_changed,
                    "lines": app.drain(),
                }))
            return self._send(404, "{}")

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                if path == "/api/reload":
                    app.cfg = cfgmod.load(app.cfg_path)
                    n = sum(len(t) for t in app.cfg.mappings.values())
                    return self._send(200, json.dumps(
                        {"ok": True, "message": f"reloaded · {n} mappings"}))
                if path == "/api/doctor":
                    return self._send(200, json.dumps(doctormod.run(app.cfg)))
                if path == "/api/capture":
                    on = self.path.endswith("on=1")
                    if on:
                        app.start_capture()
                    else:
                        app.capture_stop.set()
                    return self._send(200, json.dumps({"ok": True,
                                                       "message": ""}))
                if path == "/api/export":
                    from .portable import export_bundle
                    p = export_bundle(app.cfg)
                    return self._send(200, json.dumps(
                        {"ok": True, "message": f"exported → {os.path.basename(p)}"}))
                if path == "/api/apply":
                    from .backends import get_backend
                    be = get_backend(app.env)
                    out_dir = os.path.join(os.path.dirname(app.cfg_path),
                                           "out", app.env)
                    if app.env == "linux":
                        return self._send(200, json.dumps(
                            {"ok": False,
                             "message": "linux: run 'sudo keyremap apply'"}))
                    p = (be.apply(app.cfg, out_dir, mode="interception")
                         if app.env in ("windows", "wsl")
                         else be.apply(app.cfg, out_dir))
                    n = sum(len(t) for t in app.cfg.mappings.values())
                    state.save_state(state.Deployment(
                        applied_at=time.time(),
                        config_sha=state.config_sha(app.cfg_path),
                        backend=app.env, artifact=str(p), mappings=n))
                    return self._send(200, json.dumps(
                        {"ok": True, "message": f"applied · {n} mappings"}))
            except Exception as e:  # noqa: BLE001
                return self._send(200, json.dumps(
                    {"ok": False, "message": f"{type(e).__name__}: {e}"}))
            return self._send(404, "{}")

    return H


def serve(cfg_path: str, port: int = 0, open_browser: bool = True):
    app = _State(cfg_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _handler(app))
    url = f"http://127.0.0.1:{httpd.server_port}/"
    print(f"keyremap web UI → {url}   (ctrl-c to stop)")
    if open_browser:
        threading.Thread(target=lambda: (time.sleep(0.4), webbrowser.open(url)),
                         daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0
