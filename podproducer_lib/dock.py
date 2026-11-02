"""Cue dock: localhost-only HTTP page for an OBS Custom Browser Dock (host only, never in the stream)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 8765

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Producer</title>
<style>
:root{--bg:#fbfaf7;--ink:#1d1b18;--mute:#6b665e;--line:#e4e0d8;--card:#fff;--p1:#d9480f;--p2:#b7791f;--p3:#2f7d6b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:18px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:14px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap}
.clock{font-size:34px;font-weight:700;font-variant-numeric:tabular-nums}
.seg{color:var(--mute);font-size:16px}
.row{display:flex;align-items:center;gap:10px;margin:8px 0;font-size:15px;color:var(--mute)}
.dot{width:12px;height:12px;border-radius:50%;display:inline-block}
.bar{flex:1;height:12px;border-radius:6px;background:var(--line);overflow:hidden;display:flex}
.bar i{display:block;height:100%}
.cue{background:var(--card);border:1px solid var(--line);border-left:8px solid var(--p2);border-radius:10px;padding:12px 14px;margin:10px 0}
.cue.p1{border-left-color:var(--p1)}.cue.p3{border-left-color:var(--p3)}
.cue.old{opacity:.45}
.chip{display:inline-block;font-size:13px;font-weight:600;padding:2px 9px;border-radius:99px;background:var(--line);margin-bottom:6px}
.text{font-size:22px;font-weight:600}
.why{font-size:14px;color:var(--mute);margin-top:4px}
.btns{margin-top:8px;display:flex;gap:8px}
button{font:inherit;font-size:15px;padding:6px 16px;border-radius:8px;border:1px solid var(--line);background:#fff;cursor:pointer}
button:hover{background:var(--line)}
.empty{color:var(--mute);padding:24px 0}
</style></head><body>
<div class="top"><div class="clock" id="clock">0:00</div><div class="seg" id="seg"></div></div>
<div class="row"><span>Energy</span><span class="dot" id="energy"></span><span id="elabel"></span>
<span style="margin-left:auto">Talk</span><div class="bar" id="share" style="max-width:160px"></div><span id="slabel"></span></div>
<div id="cues"><div class="empty">Listening. Cues will land here.</div></div>
<script>
const COL={low:'#b7791f',ok:'#2f7d6b',high:'#d9480f'};
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function ack(id,s){await fetch('/ack',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,status:s})});load()}
async function load(){
 try{
  const d=await (await fetch('/cues.json',{cache:'no-store'})).json();
  document.getElementById('clock').textContent=d.clock;
  const sg=d.segment||{};
  document.getElementById('seg').textContent=sg.name?(sg.name+' \\u00b7 '+(sg.over?'over time':sg.minutes_left+' min left')):'';
  document.getElementById('energy').style.background=COL[d.energy]||'#999';
  document.getElementById('elabel').textContent=d.energy;
  const h=d.talk_share.Host||0,g=d.talk_share.Guests||0,t=(h+g)||1;
  document.getElementById('share').innerHTML='<i style="width:'+(h/t*100)+'%;background:#2b6cb0"></i><i style="width:'+(g/t*100)+'%;background:#d9480f"></i>';
  document.getElementById('slabel').textContent=(h+g)?('Host '+Math.round(h/t*100)+'%'):'';
  const box=document.getElementById('cues');
  if(!d.cues.length){box.innerHTML='<div class="empty">Listening. Cues will land here.</div>';return}
  box.innerHTML=d.cues.map(c=>{
   const open=c.status==='new';
   return '<div class="cue p'+c.priority+(open?'':' old')+'"><span class="chip">'+esc(c.type.replace('_',' '))+'</span>'+
   '<div class="text">'+esc(c.text)+'</div>'+(c.why?'<div class="why">'+esc(c.why)+'</div>':'')+
   (open?'<div class="btns"><button onclick="ack('+c.id+',\\'done\\')">Done</button><button onclick="ack('+c.id+',\\'skipped\\')">Skip</button></div>':'<div class="why">'+c.status+'</div>')+'</div>'}).join('');
 }catch(e){}
}
load();setInterval(load,3000);
</script></body></html>
"""


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, PAGE, "text/html; charset=utf-8")
            elif path == "/cues.json":
                self._send(200, json.dumps(store.snapshot()), "application/json")
            else:
                self._send(404, "not found", "text/plain")

        def do_POST(self):
            if self.path.split("?")[0] != "/ack":
                return self._send(404, "not found", "text/plain")
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
                ok = store.ack(int(body.get("id")), str(body.get("status", "done")))
            except (ValueError, TypeError):
                ok = False
            self._send(200 if ok else 400, json.dumps({"ok": ok}), "application/json")

    return Handler


def start_dock(store, port=PORT):
    """Start the dock on 127.0.0.1 in a daemon thread. port=0 picks a free port. Returns the server."""
    srv = ThreadingHTTPServer((HOST, port), make_handler(store))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


DOCK_HELP = """In OBS: Docks > Custom Browser Docks, name "Producer", URL http://127.0.0.1:{port}
This dock is for the host only. Do not add it as a source to any scene."""
