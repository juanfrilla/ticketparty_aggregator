import base64
import hashlib
import hmac
import json
import os
import random
import re
import time
import uuid
import zlib
from pathlib import Path

import httpcloak
from bs4 import BeautifulSoup

from constants import ARTISTS, CANON_FILE
from utils import read_json, save_json

from .capture import capture_order

GQL_PATH = "/api/graphql/public"
GQL_QUERY = "?version=8"

PRESET = "firefox-148-windows"
KEY_PREFIX = "sig|"


D36 = "0123456789abcdefghijklmnopqrstuvwxyz"
BASE_DIR = Path(__file__).resolve().parent


HOME = "https://www.ticketswap.es"


class TicketSwapScraper:
    def __init__(self):

        self.session = httpcloak.Session(preset="firefox-148-windows")

    @staticmethod
    def retrieve_canon_from_json(key_name) -> list:
        data = read_json(CANON_FILE)
        # Comprobamos que la clave existe y su valor es una lista
        if key_name in data and isinstance(data[key_name], list):
            # Buscamos si el valor está dentro de esa lista
            return data[key_name]

        return []

    @staticmethod
    def retrieve_v(src):
        match = re.search(r"\?v=([a-zA-Z0-9._-]+)", src)
        if match:
            return match.group(1)

    @staticmethod
    def b36(n):
        if n == 0:
            return "0"
        r = ""
        while n:
            r = D36[n % 36] + r
            n //= 36
        return r

    @staticmethod
    def retrieve_stds(text):
        m = re.search(r'stds\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        return json.loads('"%s"' % m.group(1))  # desescapa \/ y \u002b

    @staticmethod
    def hmac_key(dyn, nonce, client_ts):
        return hashlib.sha256(
            f"{KEY_PREFIX}{dyn}|{nonce}|{client_ts}".encode()
        ).hexdigest()

    @staticmethod
    def create_hmac_signature(key, msg):
        return hmac.new(key.encode(), msg.encode(), hashlib.sha256).digest()

    @staticmethod
    def load_artist_body(dj_name: str):
        return [
            {
                "extensions": {
                    "clientLibrary": {"name": "@apollo/client", "version": "4.2.9"}
                },
                "operationName": "getSearchResults",
                "query": "query getSearchResults($query: String!, $type: SearchType = null, $after: String = null) {\n  search(query: $query, first: 20, type: $type, after: $after) {\n    pageInfo {\n      hasNextPage\n      endCursor\n      __typename\n    }\n    edges {\n      node {\n        ... on EventResult {\n          __typename\n          ...siteSearchEventResult\n        }\n        ... on CityResult {\n          __typename\n          ...siteSearchCityResult\n        }\n        ... on LocationResult {\n          __typename\n          ...siteSearchLocationResult\n        }\n        ... on ArtistResult {\n          __typename\n          ...siteSearchArtistResult\n        }\n        ... on OrganizerResult {\n          __typename\n          ...siteSearchOrganizerResult\n        }\n        __typename\n      }\n      __typename\n    }\n    __typename\n  }\n}\n\nfragment country on Country {\n  code\n  name\n  slug\n  __typename\n}\n\nfragment siteSearchArtistResult on ArtistResult {\n  __typename\n  id\n  slug\n  name\n  avatar\n  uri {\n    path\n    url\n    isExternal\n    __typename\n  }\n}\n\nfragment siteSearchCityResult on CityResult {\n  __typename\n  id\n  slug\n  name\n  geoInfo {\n    latitude\n    longitude\n    __typename\n  }\n  country {\n    ...country\n    __typename\n  }\n  uri {\n    path\n    url\n    isExternal\n    __typename\n  }\n}\n\nfragment siteSearchEventResult on EventResult {\n  __typename\n  id\n  slug\n  name\n  startDate\n  endDate\n  locationName\n  cityName\n  category\n  country {\n    ...country\n    __typename\n  }\n  hasOngoingEventType\n  uri {\n    url\n    path\n    host\n    isExternal\n    trackingUrl\n    __typename\n  }\n}\n\nfragment siteSearchLocationResult on LocationResult {\n  __typename\n  id\n  slug\n  name\n  cityName\n  country {\n    ...country\n    __typename\n  }\n  uri {\n    path\n    url\n    isExternal\n    __typename\n  }\n}\n\nfragment siteSearchOrganizerResult on OrganizerResult {\n  __typename\n  id\n  name\n  logoUrl\n  slug\n  uri {\n    path\n    url\n    isExternal\n    __typename\n  }\n}",
                "variables": {"after": None, "query": dj_name, "type": None},
            }
        ]

    # OJO (medido): getActiveEvents y getSimilarArtists devuelven 401
    # {"error": "Login required"} (server: Skipper) y envenenan el lote entero.
    # La unica consulta estable es getArtistEventsStructuredData -> 200 y trae
    # eventCard con imageUrl/imageSmallUrl.
    EVENT_OPS = ("getArtistEventsStructuredData",)

    def sign(
        self,
        canon_order,
        dyn,
        nonce,
        client_ts,
        body,
        method,
        query,
        build_id,
        ts_diff,
        path,
    ):

        fields = {
            "path": path,
            "tsDiff": str(ts_diff),
            "clientTs": str(client_ts),
            "nonce": nonce,
            "body": hashlib.sha256(body.encode()).hexdigest(),  # El SHA-256 del body
            "buildId": build_id,
            "fingerprint": "",
            "method": method,
            "query": query,
            # Extras del build 812268256ae0c455:
            #   b  -> estado interno del SDK (el server acepta [])
            #   cv -> PoW del challenge (el scraper lo manda vacio)
            "b": "[]",
            "cv": "",
        }

        # 3. Unimos con \n (sin salto de línea al final)
        canon = "\n".join("%s:%s" % (k, fields.get(k, "")) for k in canon_order)

        # 4. Calculamos la clave (asumo que tu función hmac_key hace esto)
        # hmac_key = hashlib.sha256(f"sig|{dyn}|{nonce}|{client_ts}".encode()).hexdigest()
        key = self.hmac_key(dyn, nonce, client_ts)

        # 5. Firmamos y devolvemos en Base64
        mac = self.create_hmac_signature(key, canon)
        return base64.b64encode(mac).decode()

    def build_attestation(
        self,
        canon_order,
        nonce,
        dyn,
        server_ts,
        build_id,
        method,
        path,
        query,
        body,
    ):

        client_ts = int(time.time() * 1000)
        ts_diff = client_ts - server_ts
        sig = self.sign(
            canon_order,
            dyn,
            nonce,
            client_ts,
            body,
            method,
            query,
            build_id,
            ts_diff,
            path,
        )
        env = {
            "v": 1,
            "serverTs": server_ts,
            "clientTs": client_ts,
            "tsDiff": ts_diff,
            "fp": "",
            "sig": sig,
            "buildId": build_id,
        }
        raw = json.dumps(env, separators=(",", ":")).encode()
        return base64.b64encode(raw).decode()

    # --------------------------------------------------------------------------- #
    # PoW (ts-cv) — por si el clearance lo pide
    # --------------------------------------------------------------------------- #

    def solve_pow(self, seed, diff):
        sb, full, half = seed.encode(), diff >> 1, diff & 1
        n = 0
        while True:
            ns = self.b36(n)
            h = hashlib.sha256(sb + ns.encode()).digest()
            if all(b == 0 for b in h[:full]) and (not half or h[full] <= 15):
                return ns
            n += 1

    def retrieve_src_href(self):
        resp = self.first_request()
        soup = BeautifulSoup(resp.text, "html.parser")
        scripts = soup.select("script")
        for script in scripts:
            src = script.get("src")
            if src:
                return src

    def first_request(self):

        burp0_url = "https://www.ticketswap.es/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Priority": "u=0, i",
        }
        return self.session.get(burp0_url, headers=headers)

    def fetch_jsvmp_request(self, href):
        """Descarga el JS con requests. Devuelve (src, url) o (None, None)."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
            "Accept": "application/graphql-response+json,application/json;q=0.9",
            "Accept-Language": "es-ES",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.ticketswap.es/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Priority": "u=4",
            "Te": "trailers",
        }
        url = f"{HOME}{href}"
        r = self.session.get(url, timeout=15, headers=headers)
        return r

    def retrieve_events(
        self, web_attest: str, nonce: str, json_data: dict, device_id: str
    ):

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
            "Accept": "application/graphql-response+json,application/json;q=0.9",
            "Accept-Language": "es-ES",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.ticketswap.es/",
            "Content-Type": "application/json",
            "Device-Id": device_id,
            "Web-Attest": web_attest,
            "Web-Request-Id": nonce,
            "Origin": "https://www.ticketswap.es",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Priority": "u=4",
            "Te": "trailers",
        }

        params = {
            "version": "8",
        }

        response = self.session.post(
            "https://www.ticketswap.es/api/graphql/public",
            params=params,
            headers=headers,
            data=json_data,
        )
        return response

    def _post_batch(self, canon_order, dyn, server_ts, build_id, body, device_id):
        """Firma `body` (la MISMA cadena que se envía) y lo postea al GraphQL."""
        nonce = os.urandom(16).hex()
        attest = self.build_attestation(
            canon_order,
            nonce,
            dyn,
            server_ts,
            build_id,
            "POST",
            GQL_PATH,
            GQL_QUERY,
            body,
        )
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
            "Accept": "application/graphql-response+json,application/json;q=0.9",
            "Accept-Language": "es-ES",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.ticketswap.es/",
            "Content-Type": "application/json",
            "Device-Id": device_id,
            "Web-Attest": attest,  # sin esta cabecera -> 403
            "Web-Request-Id": nonce,  # sin ella o repetida -> 403
            "Origin": "https://www.ticketswap.es",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Priority": "u=4",
            "Te": "trailers",
        }
        return self.session.post(
            "https://www.ticketswap.es/api/graphql/public",
            params={"version": "8"},
            headers=headers,
            data=body,
        )

    def parse_release(self, src: str):
        match = re.search(r'release:"([a-fA-F0-9]{40})"', src)
        if match:
            return match.group(1)
        return ""

    def retrieve_second_build_id_src(self):

        burp0_url = "https://ticketswap-image-cdn.b-cdn.net:443/_next/static/chunks/32fu4d8md-_kv.js"
        burp0_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
            "Accept": "*/*",
            "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Storage-Access": "none",
            "Referer": "https://www.ticketswap.es/",
            "Sec-Fetch-Dest": "script",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
            "Te": "trailers",
            "Connection": "keep-alive",
        }

        return self.session.get(burp0_url, headers=burp0_headers)

    # Debe coincidir con el primer segmento de la ruta publica del evento
    # (/concert-tickets/<slug>). Next exige que la ruta interna sea SIEMPRE
    # /es-ES/event/<slug>/new.json y que la categoria viaje en el query
    # (categoryTickets). Si no coincide con la del evento, la respuesta es
    # 200 con pageProps.__N_REDIRECT (soft redirect 308) en vez de datos.
    CATEGORY_PREFIXES = (
        "concert-tickets",
        "club-tickets",
        "festival-tickets",
        "sport-tickets",
        "theatre-tickets",
        "comedy-tickets",
        "museum-tickets",
        "amusement-park-tickets",
        "vouchers-and-day-out",
    )

    @staticmethod
    def split_event_ref(event_name_id):
        """'<slug>' | '/<prefix>/<slug>' -> (slug, prefix o None)."""
        parts = [p for p in str(event_name_id or "").strip("/").split("/") if p]
        if not parts:
            return "", None
        slug = parts[-1]
        prefix = parts[0] if len(parts) > 1 else None
        return slug, prefix

    @staticmethod
    def soft_redirect(resp):
        """Prefijo de categoria de `pageProps.__N_REDIRECT` (o None si hay datos)."""
        try:
            props = resp.json().get("pageProps") or {}
        except Exception:
            return None
        target = props.get("__N_REDIRECT")
        if not target:
            return None
        parts = [p for p in str(target).strip("/").split("/") if p]
        return parts[0] if parts else None

    def _next_data_request(
        self,
        canon_order,
        dyn,
        build_id,
        second_build_id,
        slug,
        prefix,
        server_ts,
        device_id,
    ):
        path = f"/_next/data/{second_build_id}/es-ES/event/{slug}/new.json"
        query = f"?categoryTickets={prefix}&eventSlug={slug}"

        nonce = os.urandom(16).hex()
        attest = self.build_attestation(
            canon_order,
            nonce,
            dyn,
            server_ts,
            build_id,
            "GET",
            path,
            query,
            "",
        )

        burp0_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
            "Accept": "*/*",
            "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.ticketswap.es/",
            "Web-Attest": attest,
            "Web-Request-Id": nonce,
            "X-Nextjs-Data": "1",
            # httpcloak cachea por URL: a la 2a peticion identica contesta 304
            # con el cuerpo vacio. Con no-cache siempre devuelve el JSON.
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Priority": "u=4",
            "Te": "trailers",
        }
        self.session.set_cookie("device_id", device_id)
        return self.session.get(
            f"https://www.ticketswap.es{path}{query}", headers=burp0_headers
        )

    def next_request(
        self,
        canon_order,
        dyn,
        build_id,
        second_build_id,
        event_name_id,
        server_ts,
        device_id,
        category_prefix=None,
    ):
        slug, prefix = self.split_event_ref(event_name_id)
        prefix = category_prefix or prefix or "club-tickets"

        resp = None
        for _ in range(3):
            resp = self._next_data_request(
                canon_order,
                dyn,
                build_id,
                second_build_id,
                slug,
                prefix,
                server_ts,
                device_id,
            )
            if resp.status_code == 304:  # copia cacheada sin cuerpo: reintentar
                continue
            if resp.status_code == 200:
                real_prefix = self.soft_redirect(resp)
                if real_prefix and real_prefix != prefix:
                    prefix = real_prefix  # el __N_REDIRECT dicta la categoria
                    continue
            break
        return resp.json()

    def event_information_request(
        self, canon_order, dyn, server_ts, build_id, artist_id
    ):
        """Lote de eventos del artista. Firma el body EXACTO que envia."""
        body = json.dumps(self.load_event_body(artist_id), separators=(",", ":"))
        resp = self._post_batch(canon_order, dyn, server_ts, build_id, body)
        if resp.status_code == 401:  # cache de veredicto por hash del cuerpo
            body = json.dumps(self.load_event_body(artist_id), separators=(", ", ": "))
            resp = self._post_batch(canon_order, dyn, server_ts, build_id, body)
        return resp

    @classmethod
    def cover_for(cls, category, seed=None) -> str:
        """Portada generica que usa TicketSwap cuando el evento no tiene imagen.

        Equivale a getCoverImage(category) del bundle. Con `seed` la eleccion es
        ESTABLE (crc32) en vez de aleatoria, para que el mismo evento no cambie de
        foto entre scrapes.
        """
        names = (
            cls.COVERS.get(str(category or "").strip().upper()) or cls.COVERS["default"]
        )
        if not names:
            return ""
        if seed:
            idx = zlib.crc32(str(seed).encode("utf-8")) % len(names)
        else:
            idx = random.randrange(len(names))
        return cls.COVER_BASE + names[idx] + cls.COVER_QUERY

    @classmethod
    def placeholder_for(cls, category) -> str:
        """Compat: nombre viejo (devolvia concert-3 para todo). Ahora delega."""
        return cls.cover_for(category)

    @staticmethod
    def normalize_name(name) -> str:
        return re.sub(r"[^a-z0-9]", "", str(name or "").lower())

    @classmethod
    def pick_artist_id(cls, artist_ids: dict, artist: str):
        """artist_ids = {nombre_normalizado: 'QXJ0aXN0Oj…'} -> id o None."""
        key = cls.normalize_name(artist)
        if key in artist_ids:
            return artist_ids[key]
        for name, gid in artist_ids.items():
            if key and (key in name or name in key):
                return gid
        return None

    # operaciones del lote cuyo `activeEvents` SÍ está filtrado por el artista
    ARTIST_EVENT_OPS = ("getActiveEvents", "getArtistEventsStructuredData")

    @classmethod
    def image_index(cls, payload, ops=None) -> dict:
        """{uri.url: (imageUrl, imageSmallUrl)}.

        `ops` = operationName de cada bloque del lote (mismo orden). Si se pasa,
        solo se indexan los bloques artist-scoped: `getSimilarArtistEvents`
        devuelve eventos de OTROS artistas y contaminaba el índice.
        """
        blocks = payload if isinstance(payload, list) else [payload]
        out = {}
        for i, block in enumerate(blocks):
            if ops is not None and i < len(ops) and ops[i] not in cls.ARTIST_EVENT_OPS:
                continue
            conn = ((block or {}).get("data") or {}).get("activeEvents") or {}
            for edge in conn.get("edges") or []:
                node = edge.get("node") or {}
                url = (node.get("uri") or {}).get("url")
                if url:
                    big, small = node.get("imageUrl"), node.get("imageSmallUrl")
                    # los placeholders del CDN (…/static/images/placeholders/…) son
                    # genéricos del sitio, no la portada del evento
                    if big and "placeholders/" in big:
                        big = None
                    if small and "placeholders/" in small:
                        small = None
                    out[url] = (big, small)
        return out

    def scrape(self):
        scraped_data = []
        # cliente de la pagina del artista (JSON de Next). Si falla, se sigue
        # con el lote de GraphQL como siempre.
        device_id = str(uuid.uuid4())
        resp = self.first_request()
        try:
            stds = self.retrieve_stds(resp.text)
        except AttributeError:
            stds = None

        raw_server_ts, dynamic_secret = stds.split(".", 1)
        server_ts = int(raw_server_ts)
        build_id = self.retrieve_v(resp.text)
        canon_order = self.retrieve_canon_from_json(build_id)
        if not canon_order:
            print("Not canon order, using browser to get it")
            src_href = self.retrieve_src_href()
            jsvmp_src_resp = self.fetch_jsvmp_request(src_href)
            canon_order = capture_order(jsvmp_src_resp)
            save_json(CANON_FILE, {build_id: canon_order})
        print("Already have canon order, proceeding")
        resp = self.retrieve_second_build_id_src()
        src = resp.text
        second_build_id = self.parse_release(src)
        for artist in ARTISTS:
            print(f"Scraping {artist}")
            # --- 1) búsqueda: eventos + id global del artista
            nonce = os.urandom(16).hex()
            body = json.dumps(self.load_artist_body(artist), separators=(",", ":"))
            attest = self.build_attestation(
                canon_order,
                nonce,
                dynamic_secret,
                server_ts,
                build_id,
                "POST",
                GQL_PATH,
                GQL_QUERY,
                body,
            )
            search_resp = self.retrieve_events(attest, nonce, body, device_id)
            if search_resp.status_code != 200:
                print(f"[{artist}] búsqueda -> {search_resp.status_code}")
                continue
            edges = ((search_resp.json()[0].get("data") or {}).get("search") or {}).get(
                "edges"
            ) or []

            # --- 3) filas
            for edge in edges:
                node = edge.get("node") or {}
                if node.get("__typename") != "EventResult":
                    continue
                if node.get("country", {}).get("slug") != "spain":
                    continue
                event_url = node.get("uri", {}).get("url")
                event_path = node.get("uri", {}).get("path")
                event_name_id = event_path.replace("/concert-tickets/", "")

                next_data = self.next_request(
                    canon_order,
                    dynamic_secret,
                    build_id,
                    second_build_id,
                    event_name_id,
                    server_ts,
                    device_id,
                )
                image_url = (
                    next_data.get("pageProps", {})
                    .get("structuredDataEvent", {})
                    .get("imageUrl")
                )
                if not image_url:
                    image_url = None

                scraped_data.append(
                    {
                        "name": node.get("name"),
                        "artist": artist,
                        "start_date": node.get("startDate"),
                        "end_date": node.get("endDate"),
                        "club": node.get("locationName"),
                        "city_name": node.get("cityName"),
                        "event_url": event_url,
                        "image_url": image_url,
                        "origin": "www.ticketswap.com",
                    }
                )
        return scraped_data
