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
PORT = 9222


# Independiente de los nombres _0x...: usa `arguments`
CONDITION = """(
  (window.__hmac = window.__hmac || []).push({
    t: Date.now(),
    args: Array.from(arguments).map(a => {
      try {
        if (a instanceof ArrayBuffer || ArrayBuffer.isView(a))
          return 'bytes:' + Array.from(new Uint8Array(a.buffer || a)).map(b => b.toString(16).padStart(2,'0')).join('');
        return typeof a === 'string' ? a : JSON.stringify(a);
      } catch (e) { return String(a); }
    })
  }),
  false
)"""

LINE_TERMINATORS = re.compile(r"\r\n|[\n\r\u2028\u2029]")


# ---------------------------------------------------------------- posición
def locate(src):
    """Devuelve (line, col, param_names) 0-based, como espera CDP."""
    m = re.search(r"function\s+createHmacSignature\s*\(([^)]*)\)\s*\{", src)
    if not m:
        raise RuntimeError(
            "No se encontró createHmacSignature: ¿cambió el nombre o el script?"
        )
    params = [p.strip() for p in m.group(1).split(",") if p.strip()]

    # preferimos el `return vm_` (statement seguro para breakpoint);
    # si no está, el inicio del cuerpo
    idx = src.find("return vm_", m.end())
    if idx == -1 or idx - m.end() > 500:
        idx = m.end()

    before = src[:idx]
    terms = list(LINE_TERMINATORS.finditer(before))
    line = len(terms)
    col = idx - (terms[-1].end() if terms else 0)
    return line, col, params


# --------------------------------------------------------------------- CDP
class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, suppress_origin=True)
        self.i = 0
        self.events = []

    def send(self, method, params=None):
        self.i += 1
        self.ws.send(
            json.dumps({"id": self.i, "method": method, "params": params or {}})
        )
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.i:
                if "error" in m:
                    raise RuntimeError(f"{method} falló: {m['error']}")
                return m.get("result", {})
            self.events.append(m)

    def pump(self, seconds):
        self.ws.settimeout(0.5)
        end = time.time() + seconds
        while time.time() < end:
            try:
                self.events.append(json.loads(self.ws.recv()))
            except websocket.WebSocketTimeoutException:
                pass
        self.ws.settimeout(None)

    def parsed_scripts(self, src_url):
        return [
            e["params"]
            for e in self.events
            if e.get("method") == "Debugger.scriptParsed"
            and src_url in e["params"].get("url", "")
        ]


def retrieve_v(src):
    return src.split("=")[1]


# -------------------------------------------------------------------- main
def run_capture(
    resp=None,
    chrome_bin="chromium",
    xvfb=True,
    wait=8,
):
    url = HOME
    line = col = params = None
    src = resp.text
    src_url = resp.url
    src_script = src_url.split("/")[-1].split("?")[0]
    if src:
        line, col, params = locate(src)
        print(f"Posición (requests): línea {line}, columna {col}, params {params}")

    # 2) lanzar Chrome
    profile = tempfile.mkdtemp(prefix="prof_")
    cmd = [
        chrome_bin,
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-sandbox",
        "about:blank",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ]
    if xvfb and shutil.which("xvfb-run"):
        cmd = ["xvfb-run", "-a"] + cmd

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        for _ in range(40):
            try:
                tabs = requests.get(f"http://127.0.0.1:{PORT}/json", timeout=1).json()
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("Chrome no arrancó")

        tab = next(t for t in tabs if t["type"] == "page")
        cdp = CDP(tab["webSocketDebuggerUrl"])
        cdp.send("Page.enable")
        cdp.send("Debugger.enable")

        # 2b) fallback: sacar el fuente del navegador
        if src is None:
            print("Pasada previa: localizando el script en el navegador…")
            cdp.send("Page.navigate", {"url": url})
            cdp.pump(wait)
            matches = cdp.parsed_scripts(src_script)
            if not matches:
                raise RuntimeError(
                    "El script no se cargó (¿403 / anti-bot / espera corta?)"
                )
            last = matches[-1]
            try:
                src = cdp.send(
                    "Debugger.getScriptSource", {"scriptId": last["scriptId"]}
                )["scriptSource"]
            except RuntimeError:
                r = cdp.send(
                    "Runtime.evaluate",
                    {
                        "expression": f"fetch({json.dumps(last['url'])}).then(r => r.text())",
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                )
                src = r["result"]["value"]
            line, col, params = locate(src)
            print(f"Posición (navegador): línea {line}, columna {col}, params {params}")
            cdp.send("Page.navigate", {"url": "about:blank"})
            cdp.pump(1)

        # 3) logpoint antes de cargar la web
        bp = cdp.send(
            "Debugger.setBreakpointByUrl",
            {
                "urlRegex": r".*" + re.escape(src_script) + r".*",
                "lineNumber": line,
                "columnNumber": col,
                "condition": CONDITION,
            },
        )
        print(f"Breakpoint: {bp}")

        # 4) cargar y leer resultados
        cdp.events.clear()
        cdp.send("Page.navigate", {"url": url})
        cdp.pump(wait)
        # ... aquí puedes disparar la acción que firma ...

        r = cdp.send(
            "Runtime.evaluate",
            {
                "expression": "JSON.stringify(window.__hmac || [])",
                "returnByValue": True,
            },
        )
        return {
            "params": params,
            "line": line,
            "col": col,
            "calls": json.loads(r["result"]["value"]),
        }
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), 15)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def capture_order(src_resp):
    order = []
    res = run_capture(src_resp)
    calls = res.get("calls")
    for call in calls:
        argies = call.get("args")
        for argy in argies:
            if "body" in argy:
                for element in argy.split("\n"):
                    splitted = element.split(":")
                    el = splitted[0]
                    if "cv" in el:
                        continue
                    order.append(splitted[0])
    return order
