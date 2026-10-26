import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
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
    def load_body(dj_name: str):
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
        }

        # 3. Unimos con \n (sin salto de línea al final)
        canon = "\n".join("%s:%s" % (k, fields[k]) for k in canon_order)

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

    def retrieve_data(self, web_attest: str, nonce: str, json_data: dict):

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
            "Accept": "application/graphql-response+json,application/json;q=0.9",
            "Accept-Language": "es-ES",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.ticketswap.es/",
            "Content-Type": "application/json",
            "Device-Id": str(uuid.uuid4()),
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

    def scrape(self):
        scraped_data = []
        for artist in ARTISTS:
            print(f"Scraping {artist}")
            resp = self.first_request()
            stds = self.retrieve_stds(resp.text)
            if stds:
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
                nonce = os.urandom(16).hex()
                json_data = self.load_body(artist)
                body = json.dumps(json_data, separators=(",", ":"))
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

                resp = self.retrieve_data(attest, nonce, body)
                data = resp.json()
                events = data[0].get("data", {}).get("search", {}).get("edges", [])
                for event in events:
                    node = event.get("node")
                    if node.get("__typename") == "ArtistResult":
                        continue
                    if node.get("country", {}).get("slug") != "spain":
                        continue
                    # if artist not in node.get("name").lower():
                    #     continue
                    scraped_data.append(
                        {
                            "name": node.get("name"),
                            "artist": artist,
                            "start_date": node.get("startDate"),
                            "end_date": node.get("endDate"),
                            "club": node.get("locationName"),
                            "city_name": node.get("cityName"),
                            "event_url": node.get("uri", {}).get("url"),
                        }
                    )
        return scraped_data
