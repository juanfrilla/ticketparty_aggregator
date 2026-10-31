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

            # artist_ids = {}
            # # avatar publico del artista: la busqueda ya lo trae y sirve de
            # # respaldo real cuando el evento no tiene portada (p.ej. abel ramos)
            # artist_avatars = {}
            # slug de su pagina: /artist/<slug> -> de ahi sale el JSON de Next
            # artist_slugs = {}
            # for edge in edges:
            #     node = edge.get("node") or {}
            #     if node.get("__typename") == "ArtistResult" and node.get("id"):
            #         artist_ids[self.normalize_name(node.get("name"))] = node["id"]
            #         slug = slug_from_uri(
            #             (node.get("uri") or {}).get("path")
            #             or (node.get("uri") or {}).get("url")
            #         )
            #         if slug:
            #             artist_slugs[self.normalize_name(node.get("name"))] = slug
            #         if node.get("avatar"):
            #             artist_avatars[node["id"]] = node["avatar"]

            # artist_slug = self.pick_artist_id(artist_slugs, artist)

            # --- 2a) PAGINA del artista: /_next/data/<build>/es-ES/artist/<slug>.json
            # Trae avatarUrl + activeEvents[].imageUrl sin tocar GraphQL. Es la
            # fuente preferente; el lote queda como respaldo.
            # page_images = {}
            # if artist_slug and page is not None:
            #     pageprops = page.fetch_artist(artist_slug)
            #     if pageprops:
            #         page_images, page_avatar = images_from_pageprops(pageprops)
            #         if page_avatar and global_id and global_id not in artist_avatars:
            #             artist_avatars[global_id] = page_avatar
            #         print(
            #             f"[{artist}] pagina: {len(page_images)} eventos con imagen"
            #             f" · avatar={'si' if page_avatar else 'no'}"
            #         )
            #     else:
            #         print(f"[{artist}] pagina del artista no disponible -> GraphQL")

            # --- 2b) lote GraphQL (respaldo y eventos que la pagina no liste)
            # images = {}
            # if global_id:
            #     ev_resp = self.event_information_request(
            #         canon_order, dynamic_secret, server_ts, build_id, global_id
            #     )
            #     if ev_resp.status_code == 200:
            #         ops = [
            #             op["operationName"] for op in self.load_event_body(global_id)
            #         ]
            #         images = self.image_index(ev_resp.json(), ops)
            #     else:
            #         print(f"[{artist}] lote de imagenes -> {ev_resp.status_code}")
            # else:
            #     print(f"[{artist}] sin id global de artista")
            # # la pagina manda: si las dos traen el evento, gana la pagina
            # images = {**images, **page_images}
            # # [garantia] Solo URLs de la busqueda de ESTE artista: aunque un bloque
            # # del lote traiga activeEvents de otro artista (o si se llamase a
            # # image_index sin `ops`), no puede colarse una imagen ajena.
            # own_urls = {
            #     ((e.get("node") or {}).get("uri") or {}).get("url")
            #     for e in edges
            #     if (e.get("node") or {}).get("__typename") != "ArtistResult"
            # }
            # images = {u: v for u, v in images.items() if u in own_urls}

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
                        # "image_small_url": image_small_url,
                        # "image_source": image_source,
                    }
                )
        return scraped_data


# TODO e de cristian varela, dificil sacar la imagen
# TODO mirar la apk a ver si sale la imagen ahi


# import requests

# burp0_url = "https://www.ticketswap.es:443/_next/data/9755219a57713af6b70ea603af7ce7ee10a77302/es-ES/event/face-off-abel-ramos-y-cristian-varela-pinto-sala-groove-2026-12-07-CfGnwGWqMALA61gCheDM3/new.json?categoryTickets=concert-tickets&eventSlug=face-off-abel-ramos-y-cristian-varela-pinto-sala-groove-2026-12-07-CfGnwGWqMALA61gCheDM3"

# burp0_headers = {
#     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) Gecko/20100101 Firefox/159.0",
#     "Accept": "*/*",
#     "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
#     "Accept-Encoding": "gzip, deflate, br",
#     "Referer": "https://www.ticketswap.es/artist/abel-ramos-tickets-4o1KJMK9ECggdbuFZVrYMG",
#     "Baggage": "sentry-environment=production,sentry-release=9755219a57713af6b70ea603af7ce7ee10a77302,sentry-public_key=614c6d8e7a8248408b0c226150d9e239,sentry-trace_id=bb4e3710e75547efbfb4d1ebd902cf5d,sentry-org_id=11994,sentry-transaction=%2F%5B...alias%5D,sentry-sampled=false,sentry-sample_rand=0.7441346765857434,sentry-sample_rate=0",
#     "Sentry-Trace": "bb4e3710e75547efbfb4d1ebd902cf5d-a654f16507e429a8-0",
#     "Web-Attest": "eyJ2IjoxLCJzZXJ2ZXJUcyI6MTc5MzQzNzQxNDU2MywiY2xpZW50VHMiOjE3OTM0Mzg0MTQxODIsInRzRGlmZiI6MjgsImZwIjoiKzlQVHZkTVBkWjRncytJOVpzdEZweGljaTUySzNGZjhVWWNsb2h0SmpEU0JmZmtNZ1A5ckt5blJtbVJPWHRhenpnL09ueDd4Tm0yUlFhdVZaZUNUWEpSQy9aeWlkbEthaFVFS0JOa0U5K24vazdnSEk4QUhYU3pWcUp5aCtZQzRLM2ZqREthWGV6WEZzQVFXZEt0RGdSeVQ4U3FDY2JmbWo0OU5oQ2hMdGo3VEVuL0F1UEczektvZmNNTHY0UVR1NWhhcjBSZjhHYnN5blFUZDhJQUR2WGtIK3c3eisxRlVqTUVEU3lOSFdwWkdGMi9jbzYzalJpb0ZqYTZrQzQyeTRpVTdVMDhGNzkvWUE1dlJ0a3VjMHdZd1g5Sm5YSENEelBwb1hIYTh5Q1FINGoxUXE1aVVLSldKZ1ZvVkVWMThOc3hLSWVZY2h5TStaZWx2UzNoWXVIUE5yOGo4STQvSG5Qd0p6RFlTYzhSa1J4QVV4Q3pRN3ZZZm04VUN3SGdEbW5SdjVhd29DbFkvenRPRnBJN0V0V0NKQXI5VnVBZWI0djliSzVWREtvM2s4aFArNjFLWFIwaFRobm91M2NCK3V2ak5qQ2padVFIQldyM3dWTE9YM2VERjBLdTNvVCtiY0xKb3NNbDhwdStEUUpKVHhwVEc0bW50MTA3dnF0WDRvTVo0d2pDaDUwQWYiLCJzaWciOiJpRzBsaU1SSkFIZFVxdzdRcThHd0V1U244V3AxakpxKzV5SEV4UDUzRVRvPSIsImJ1aWxkSWQiOiJmZDg4YTYwMGVlZWI5Y2Y2IiwiYiI6WzEzMzUsNjg0LDE5NDA4LDI5LDE1NiwyLDEyLDY4LDQ4MSwxMCw3MiwzLDAsMCwxMCwxMCwwLDRdfQ==",
#     "Web-Request-Id": "1c292725f1e4f25dc52c9f6890b7bef1",
#     "X-Nextjs-Data": "1",
#     "Sec-Fetch-Dest": "empty",
#     "Sec-Fetch-Mode": "cors",
#     "Sec-Fetch-Site": "same-origin",
#     "Priority": "u=4",
#     "Te": "trailers",
# }
# requests.get(burp0_url, headers=burp0_headers, cookies=burp0_cookies)


# work/test_next_request_200.py
