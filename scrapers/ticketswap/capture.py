"""Captura del ORDEN del canon que usa el SDK de TicketSwap.

Reescrito tras la actualizacion de la VM (build 812268256ae0c455): ya NO existe
`createHmacSignature` ni ningun wrapper `vm(0x.., arguments, ..)`, asi que el
logpoint por nombre de funcion no sirve.

Metodo nuevo, independiente del build y de los nombres:

  * CDP -> Page.addScriptToEvaluateOnNewDocument inyecta un hook en
    `crypto.subtle.sign` ANTES que el script de la pagina (y en cada documento
    nuevo, asi que sobrevive al reload del challenge).
  * El hook guarda el texto EXACTO que se firma (el canon) en window.__SIG.
  * Se navega a la home, y si hace falta se escribe en el buscador para forzar
    una peticion GraphQL.
  * `capture_order` devuelve el orden de los campos del canon cuyo `path` es
    /api/graphql/public (el que necesitan las peticiones del scraper).

Ventaja: si TicketSwap cambia el algoritmo, lo unico que hay que mirar es la
traza; el hook no depende de constantes del bundle.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

import requests
import websocket

HOME = "https://www.ticketswap.es"
PORT = 9222  # solo se usa si free_port no esta disponible


def free_port():
    """Puerto efimero: evita colisiones con otros Chrome/experimentos."""
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port
GQL_PATH = "/api/graphql/public"

# campos conocidos del canon (el orden se deduce del texto, no de aqui)
CAMPOS = ("fingerprint", "path", "tsDiff", "clientTs", "body", "method",
          "buildId", "query", "nonce", "b", "cv")

HOOK_JS = r"""
(function(){
  if (window.__SIG) return;
  window.__SIG = [];
  function ser(v){
    try{
      if (v == null) return String(v);
      if (typeof v === 'string') return v;
      if (v instanceof ArrayBuffer || ArrayBuffer.isView(v)){
        var off = v.byteOffset || 0, len = v.byteLength || 0;
        var ab = v.buffer ? v.buffer.slice(off, off+len) : v;
        try { return new TextDecoder('utf-8', {fatal:false}).decode(ab); } catch(e){ return ''; }
      }
      return 'obj:' + JSON.stringify(v).slice(0, 4000);
    }catch(e){ return '<err>'; }
  }
  try {
    var st = window.crypto && window.crypto.subtle;
    if (!st || !st.sign) return;
    var orig = st.sign;
    st.sign = function(alg, key, data){
      try { window.__SIG.push(ser(data)); } catch(e){}
      return orig.apply(this, arguments);
    };
  } catch(e){}
})();
"""


# --------------------------------------------------------------------- CDP
class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, suppress_origin=True)
        self.i = 0
        self.events = []

    def send(self, method, params=None):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.i:
                if "error" in m:
                    raise RuntimeError(f"{method} fallo: {m['error']}")
                return m.get("result", {})
            self.events.append(m)

    def eval(self, expr):
        r = self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True, "awaitPromise": True})
        return (r.get("result") or {}).get("value")

    def pump(self, seconds):
        self.ws.settimeout(0.4)
        end = time.time() + seconds
        while time.time() < end:
            try:
                self.events.append(json.loads(self.ws.recv()))
            except websocket.WebSocketTimeoutException:
                pass
        self.ws.settimeout(None)


def _kill(proc, profile):
    try:
        os.killpg(os.getpgid(proc.pid), 15)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    shutil.rmtree(profile, ignore_errors=True)


# ------------------------------------------------------------- deducir orden
def order_from_canon(canon):
    order = []
    for line in canon.split("\n"):
        if ":" not in line:
            continue
        name = line.split(":", 1)[0].strip()
        if name:
            order.append(name)
    return order


def pick_canon(canons, want_path=GQL_PATH):
    """Elige el canon mas informativo.

    1) Si hay uno de /api/graphql/public, ese (es el que usa el scraper).
    2) Si no (p.ej. Chrome headless solo supera el challenge y firma
       /_jRTV...), vale el del challenge: los campos comunes van en el MISMO
       orden y solo anade `cv` en medio (que el scraper filtra).
    """
    if not canons:
        return None
    gql = [c for c in canons if f"path:{want_path}" in c]
    pool = gql or [c for c in canons if c.count("\n") >= 6] or canons
    # mas lineas primero, y a igualdad el mas largo
    return sorted(pool, key=lambda c: (c.count("\n"), len(c)))[-1]


# -------------------------------------------------------------------- main
def run_capture(chrome_bin="google-chrome", wait=30, headless=True, url=HOME,
                trigger=True, chrome_extra=None):
    global PORT
    PORT = free_port()
    profile = tempfile.mkdtemp(prefix="prof_")
    cmd = [chrome_bin, f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
           "--no-first-run", "--no-sandbox", "--disable-dev-shm-usage",
           "--disable-gpu", "about:blank"]
    if headless:
        cmd.insert(1, "--headless=new")
    if chrome_extra:
        cmd.extend(chrome_extra)
    if shutil.which("xvfb-run") and not headless:
        cmd = ["xvfb-run", "-a"] + cmd

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        for _ in range(60):
            try:
                tabs = requests.get(f"http://127.0.0.1:{PORT}/json", timeout=1).json()
                if any(t.get("type") == "page" for t in tabs):
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("Chrome no arranco")

        tab = next(t for t in tabs if t["type"] == "page")
        cdp = CDP(tab["webSocketDebuggerUrl"])
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": HOOK_JS})
        cdp.send("Page.navigate", {"url": url})

        canons = []
        typed = False
        t0 = time.time()
        while time.time() - t0 < wait:
            cdp.pump(1.0)
            try:
                raw = cdp.eval("JSON.stringify(window.__SIG || [])")
                canons = json.loads(raw) if raw else []
            except Exception:
                canons = []
            if any(f"path:{GQL_PATH}" in c for c in canons):
                break
            if trigger and not typed and time.time() - t0 > 6:
                typed = True
                # forzar una peticion GraphQL: escribir en el buscador
                cdp.eval(
                    """(function(){
                        var i = document.querySelector('#site-search');
                        if (!i) return false;
                        var setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                        setter.call(i, 'dj marta');
                        i.dispatchEvent(new Event('input', {bubbles:true}));
                        i.dispatchEvent(new Event('change', {bubbles:true}));
                        i.focus();
                        return true;
                    })()"""
                )
        return {"canons": canons, "title": cdp.eval("document.title"),
                "url": cdp.eval("location.href")}
    finally:
        _kill(proc, profile)


def capture_order(src_resp=None, build_id=None, headless=True, wait=30):
    """Devuelve el orden de campos del canon. Compatible con la firma anterior."""
    res = run_capture(headless=headless, wait=wait)
    canons = res.get("canons") or []
    print(f"[capture] {len(canons)} firmas, title={res.get('title')!r}")
    for c in canons:
        print("   -", c.split("\n")[0][:40], "...", "lineas:", c.count("\n") + 1)
    best = pick_canon(canons)
    if not best:
        raise RuntimeError("No se capturo ninguna firma (challenge no superado?)")
    order = order_from_canon(best)
    # `cv` es el PoW del challenge: /api/graphql/public lo rechaza.
    # `b` solo aparece en peticiones del sitio con estado interno; el scraper
    # manda un `b` vacio y el server lo acepta (medido: 200 con b:[] y sin b).
    order = [f for f in order if f not in ("cv", "b")]
    print("[capture] canon elegido:", order)
    os.makedirs("work/live", exist_ok=True)
    with open("work/live/captured_canon.txt", "w", encoding="utf-8") as fh:
        fh.write(best)
    return order


def run_next_build(wait=25, chrome_bin="google-chrome", headless=True, url=HOME):
    """Captura el buildId de NEXT (40 hex) leyendo __NEXT_DATA__ con un Chrome real.

    Es OTRA cosa que el build del SDK del WAAP (812268256ae0c455). El de Next es
    el que va en la ruta /_next/data/<buildId>/es-ES/artist/<slug>.json y cambia
    en cada despliegue; el sitio solo lo publica dentro del __NEXT_DATA__ de una
    pagina servida de verdad (la home en Python suele contestar 202 challenge,
    sin HTML de Next). Devuelve None si no lo consigue.
    """
    global PORT
    PORT = free_port()
    profile = tempfile.mkdtemp(prefix="prof_")
    cmd = [chrome_bin, f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
           "--no-first-run", "--no-sandbox", "--disable-dev-shm-usage",
           "--disable-gpu", "about:blank"]
    if headless:
        cmd.insert(1, "--headless=new")
    if shutil.which("xvfb-run") and not headless:
        cmd = ["xvfb-run", "-a"] + cmd
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        tabs = None
        for _ in range(60):
            try:
                tabs = requests.get(f"http://127.0.0.1:{PORT}/json", timeout=1).json()
                if any(t.get("type") == "page" for t in tabs):
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            return None

        tab = next(t for t in tabs if t["type"] == "page")
        cdp = CDP(tab["webSocketDebuggerUrl"])
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.send("Page.navigate", {"url": url})
        expr = ("(function(){var e=document.getElementById('__NEXT_DATA__');"
                "if(!e)return null;try{return JSON.parse(e.textContent).buildId}"
                "catch(x){return null}})()")
        t0 = time.time()
        while time.time() - t0 < wait:
            cdp.pump(1.0)
            try:
                v = cdp.eval(expr)
            except Exception:
                v = None
            if v and re.fullmatch(r"[0-9a-f]{40}", str(v)):
                return v
        return None
    finally:
        _kill(proc, profile)


if __name__ == "__main__":
    import sys

    hd = "--headful" not in sys.argv
    print(capture_order(headless=hd))
