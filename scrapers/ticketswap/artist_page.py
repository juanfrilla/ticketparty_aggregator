"""Imagenes del artista desde la PAGINA (JSON de Next), no desde GraphQL.

Por que existe
--------------
El GraphQL solo devuelve `imageUrl` de eventos que TIENEN entradas a la venta
(`getArtistEventsStructuredData` -> `activeEvents`). Los eventos agotados o sin
stock salian sin portada, y el avatar habia que sacarlo de la busqueda.

La pagina del artista sirve TODO eso en un unico JSON:

    GET /_next/data/<NEXT_BUILD>/es-ES/artist/<slug>.json?slug=<slug>

    pageProps.artist.avatarUrl          -> avatar del artista
    pageProps.activeEvents.edges[].node.imageUrl       -> portada del evento
                                      .imageSmallUrl   -> miniatura
                                      .location.image  -> imagen de la sala
    pageProps.initialApolloState        -> cache Apollo (misma info)

Medido (no supuesto): el borde exige **3 cosas y solo 3**:

    * cabecera `web-attest` firmada con el path EXACTO (con `.json`) y el query
      EXACTO (`?slug=...`). Sin ella -> 403 {"error":"Access denied"}.
    * cookie `device_id` (vale cualquier uuid; el propio site la pone).
    * User-Agent Firefox 153 (el mismo que usa el SDK de httpcloak). Con
      firefox-148 o Chrome -> 403.
    NO hace falta la cookie de clearance `__tc` (aunque el navegador la lleve):
    medido 200 con attest + device_id a secas.

Ojo con los DOS build ids, que son distintos y no intercambiables:

    NEXT_BUILD = buildId de Next (40 hex)  -> va en la ruta /_next/data/...
    SDK_BUILD  = build del WAAP (16 hex)   -> va dentro del attest y en ?v= de la
                                             pagina de challenge

El attest se firma con SDK_BUILD (el `buildId` de dentro del JSON) y la URL
lleva NEXT_BUILD. Si se cruzan, 403 o 404.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid

from constants import NEXT_BUILD_FILE
from utils import read_json, save_json

HOME = "https://www.ticketswap.es"

#: UA que casa con el fingerprint TLS de httpcloak `firefox-148-windows`.
#: Medido: es lo que el borde compara; con otro UA -> 403.
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:153.0) Gecko/20100101 Firefox/153.0"

DEFAULT_CANON = [
    "fingerprint",
    "path",
    "tsDiff",
    "clientTs",
    "body",
    "method",
    "buildId",
    "query",
    "nonce",
]


# ---------------------------------------------------------------- helpers
def _hval(headers, name, default=""):
    """httpcloak devuelve los headers como listas (['1914']); esto los aplana."""
    v = headers.get(name, default) if hasattr(headers, "get") else default
    if isinstance(v, (list, tuple)):
        v = v[0] if v else default
    return v if isinstance(v, str) else ("" if v is None else str(v))


# ---------------------------------------------------------------- firma
def hmac_key(dyn, nonce, client_ts):
    return hashlib.sha256(f"sig|{dyn}|{nonce}|{client_ts}".encode()).hexdigest()


def build_attest(
    canon, dyn, nonce, client_ts, body, method, query, sdk_build, ts_diff, path
):
    fields = {
        "path": path,
        "tsDiff": str(ts_diff),
        "clientTs": str(client_ts),
        "nonce": nonce,
        "body": hashlib.sha256(body.encode()).hexdigest(),
        "buildId": sdk_build,
        "fingerprint": "",
        "method": method,
        "query": query,
        "b": "[]",
        "cv": "",
    }
    canon_txt = "\n".join("%s:%s" % (k, fields.get(k, "")) for k in canon)
    key = hmac_key(dyn, nonce, client_ts)
    sig = hmac.new(key.encode(), canon_txt.encode(), hashlib.sha256).digest()
    env = {
        "v": 1,
        "serverTs": client_ts - ts_diff,
        "clientTs": client_ts,
        "tsDiff": ts_diff,
        "fp": "",
        "sig": base64.b64encode(sig).decode(),
        "buildId": sdk_build,
    }
    return base64.b64encode(json.dumps(env, separators=(",", ":")).encode()).decode()


# --------------------------------------------------------- buildId de Next
def cached_next_build():
    try:
        data = read_json(NEXT_BUILD_FILE) or {}
    except Exception:
        return None
    return (data or {}).get("nextBuild")


def save_next_build(build):
    save_json(NEXT_BUILD_FILE, {"nextBuild": build, "captured_at": int(time.time())})


def _valid_next_build(build):
    return bool(build) and bool(re.fullmatch(r"[0-9a-f]{40}", build))


def refresh_next_build(canon=None, timeout=60):
    """Captura el buildId de Next con un Chrome real (solo cuando caduca).

    Devuelve el buildId o None. No se llama en el flujo normal del scraper:
    solo cuando el cacheado da 404.
    """
    try:
        from .capture import run_next_build
    except Exception:
        return None
    bid = run_next_build(wait=timeout)
    if _valid_next_build(bid):
        save_next_build(bid)
        print(f"[artist-page] nextBuild capturado: {bid}")
        return bid
    return None


# ------------------------------------------------------------- peticiones
class ArtistPage:
    """Una sesion httpcloak con el attest listo para pedir /_next/data/..."""

    def __init__(
        self,
        session=None,
        canon_order=None,
        sdk_build=None,
        server_ts=None,
        dyn=None,
        device_id=None,
    ):
        import httpcloak

        self.session = session or httpcloak.Session(preset="firefox-148-windows")
        self.device_id = device_id or str(uuid.uuid4())
        self.canon = canon_order or DEFAULT_CANON
        self.sdk_build = sdk_build
        self.server_ts = server_ts
        self.dyn = dyn
        if self.server_ts is None or not self.dyn:
            self.bootstrap()

    # -- stds del borde ---------------------------------------------------
    def bootstrap(self):
        """Saca `stds` (serverTs.dyn) y el SDK build de la home.

        La home puede contestar 202 (challenge) y aun asi trae las cabeceras
        `waap-webattest: <ts>.<dyn>=` y `waap-webattest-js: ...?v=<sdk_build>`,
        que es justo lo que hay que firmar.
        """
        r = self.session.get(
            HOME + "/",
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.9",
            },
            timeout=25,
        )
        txt = r.text

        m = re.search(r'stds\s*:\s*"((?:[^"\\]|\\.)*)"', txt)
        if m:
            stds = json.loads('"%s"' % m.group(1))
        else:
            hdr = _hval(r.headers, "waap-webattest")
            stds = hdr.strip().split("=")[0] if "." in hdr else ""
        if not stds or "." not in stds:
            raise RuntimeError(f"sin stds en la home ({r.status_code})")
        raw_ts, self.dyn = stds.split(".", 1)
        self.server_ts = int(raw_ts)

        if not self.sdk_build:
            js = _hval(r.headers, "waap-webattest-js")
            m2 = re.search(r"\?v=([A-Za-z0-9._-]+)", js) or re.search(
                r"\?v=([A-Za-z0-9._-]+)", txt
            )
            self.sdk_build = m2.group(1) if m2 else None
        if not self.sdk_build:
            raise RuntimeError(f"sin build del SDK en la home ({r.status_code})")
        return self

    # -- GET firmado ------------------------------------------------------
    def _sign_headers(self, path, query):
        client_ts = int(time.time() * 1000)
        ts_diff = client_ts - self.server_ts
        nonce = os.urandom(16).hex()
        attest = build_attest(
            self.canon,
            self.dyn,
            nonce,
            client_ts,
            "",
            "GET",
            query,
            self.sdk_build,
            ts_diff,
            path,
        )
        return {
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "es-ES,es;q=0.9",
            "X-Nextjs-Data": "1",
            "Referer": HOME + "/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Web-Attest": attest,
            "Web-Request-Id": nonce,
            "Cookie": f"device_id={self.device_id}",
        }

    def artist_json(self, next_build, slug, query_extra=True):
        path = f"/_next/data/{next_build}/es-ES/artist/{slug}.json"
        query = f"?slug={slug}" if query_extra else ""
        return self.session.get(
            HOME + path + query, headers=self._sign_headers(path, query), timeout=30
        )

    # -- API comoda -------------------------------------------------------
    def fetch_artist(self, slug, next_build=None, allow_refresh=True):
        """pageProps del artista, o None. Refresca el buildId si da 404."""
        next_build = next_build or cached_next_build()
        if not next_build:
            next_build = refresh_next_build(self.canon)
        if not next_build:
            return None
        r = self.artist_json(next_build, slug)
        if r.status_code == 200:
            try:
                return (r.json() or {}).get("pageProps")
            except Exception:
                return None
        if r.status_code == 404 and allow_refresh:
            # build caducado (Next devuelve 404 {"notFound":true} o un pageProps
            # con statusCode 404). Reintento una sola vez con build nuevo.
            fresh = refresh_next_build(self.canon)
            if fresh and fresh != next_build:
                return self.fetch_artist(slug, fresh, allow_refresh=False)
        return None


# ------------------------------------------------------------- extraccion
def images_from_pageprops(pageprops, base="https://www.ticketswap.es"):
    """{url_del_evento: (imageUrl, imageSmallUrl)} + avatar del artista.

    Devuelve (imagenes, avatar). Las claves son URLs absolutas del evento, para
    poder cruzarlas con lo que devuelve la busqueda de GraphQL.
    """
    pageprops = pageprops or {}
    out = {}
    artist = pageprops.get("artist") or {}
    avatar = artist.get("avatarUrl") or artist.get("avatarUrlBig")
    if not avatar:
        avatars = artist.get("avatars") or []
        if avatars:
            avatar = (avatars[0] or {}).get("url")

    edges = ((pageprops.get("activeEvents") or {}).get("edges")) or []
    for edge in edges:
        node = edge.get("node") or {}
        uri = node.get("uri") or {}
        url = uri.get("url") or uri.get("path")
        if not url:
            continue
        if url.startswith("/"):
            url = base + url
        big = node.get("imageUrl")
        small = node.get("imageSmallUrl")
        # los placeholders del CDN son genericos del site, no portada real
        if big and "placeholders/" in big:
            big = None
        if small and "placeholders/" in small:
            small = None
        out[url] = (big, small)
    return out, avatar


def slug_from_uri(path_or_url):
    """`/artist/abel-ramos-tickets-4o1K…` -> `abel-ramos-tickets-4o1K…`"""
    if not path_or_url:
        return None
    p = re.sub(r"^https?://[^/]+", "", str(path_or_url)).split("?")[0]
    p = p.rstrip("/")
    m = re.search(r"/artist/([^/]+)$", p)
    return m.group(1) if m else (p.split("/")[-1] or None)


__all__ = [
    "UA",
    "ArtistPage",
    "cached_next_build",
    "images_from_pageprops",
    "refresh_next_build",
    "save_next_build",
    "slug_from_uri",
]
