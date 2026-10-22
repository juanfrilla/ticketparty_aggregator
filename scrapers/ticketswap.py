"""TicketSwap — búsqueda pública vía GraphQL firmado (`web-attest`).

El endpoint `/api/graphql/public` exige la cabecera `Web-Attest`: un
HMAC-SHA256 sobre un "canon" de 9 líneas `campo:valor` cuyo **orden cambia con
cada build** del SDK. Ese orden vive en `canon_orders.json` (raíz del repo),
indexado por `buildId`.

Flujo por petición:
    GET /                      -> HTML (200 app o 202 challenge; da igual)
                                  de ahí salen `stds: "<serverTs>.<dyn>"` y `?v=<buildId>`
    key   = sha256("sig|"+dyn+"|"+nonce+"|"+clientTs).hexdigest()   (64 chars ASCII)
    sig   = base64(HMAC-SHA256(key, canon))
    POST /api/graphql/public?version=8   con `Web-Attest` + `Web-Request-Id`
         y `data=<el body exacto que se firmó>`   <- nunca `json=dict`

Notas que cuestan 403 si se ignoran:
  * `fp` (fingerprint) puede ir vacío, pero la LÍNEA del canon es obligatoria.
  * `nonce` es de un solo uso (se repite -> 403).
  * `cv` y `b` NO van en el envelope de GraphQL.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import httpcloak
from bs4 import BeautifulSoup

GQL_PATH = "/api/graphql/public"
GQL_QUERY = "?version=8"
KEY_PREFIX = "sig|"
PRESET = "firefox-148-windows"

SITES = {
    "es": "https://www.ticketswap.es",
    "com": "https://www.ticketswap.com",
    "nl": "https://www.ticketswap.nl",
    "de": "https://www.ticketswap.de",
    "uk": "https://www.ticketswap.uk",
}

# Campos posibles del canon (el orden lo manda el build, no esta lista).
CANON_FIELDS = (
    "path",
    "tsDiff",
    "clientTs",
    "nonce",
    "body",
    "buildId",
    "fingerprint",
    "method",
    "query",
)

_ROOT = Path(__file__).resolve().parent.parent
_ORDERS_FILE = Path(os.environ.get("TS_CANON_ORDERS", _ROOT / "canon_orders.json"))

SEARCH_QUERY = """query getSearchResults($query: String!, $type: SearchType = null, $after: String = null) {
  search(query: $query, first: 20, type: $type, after: $after) {
    pageInfo {
      hasNextPage
      endCursor
      __typename
    }
    edges {
      node {
        ... on EventResult {
          __typename
          ...siteSearchEventResult
        }
        ... on CityResult {
          __typename
          ...siteSearchCityResult
        }
        ... on LocationResult {
          __typename
          ...siteSearchLocationResult
        }
        ... on ArtistResult {
          __typename
          ...siteSearchArtistResult
        }
        ... on OrganizerResult {
          __typename
          ...siteSearchOrganizerResult
        }
        __typename
      }
      __typename
    }
    __typename
  }
}

fragment country on Country {
  code
  name
  slug
  __typename
}

fragment siteSearchArtistResult on ArtistResult {
  __typename
  id
  slug
  name
  avatar
  uri {
    path
    url
    isExternal
    __typename
  }
}

fragment siteSearchCityResult on CityResult {
  __typename
  id
  slug
  name
  geoInfo {
    latitude
    longitude
    __typename
  }
  country {
    ...country
    __typename
  }
  uri {
    path
    url
    isExternal
    __typename
  }
}

fragment siteSearchEventResult on EventResult {
  __typename
  id
  slug
  name
  startDate
  endDate
  locationName
  cityName
  category
  country {
    ...country
    __typename
  }
  hasOngoingEventType
  uri {
    url
    path
    host
    isExternal
    trackingUrl
    __typename
  }
}

fragment siteSearchLocationResult on LocationResult {
  __typename
  id
  slug
  name
  cityName
  country {
    ...country
    __typename
  }
  uri {
    path
    url
    isExternal
    __typename
  }
}

fragment siteSearchOrganizerResult on OrganizerResult {
  __typename
  id
  name
  logoUrl
  slug
  uri {
    path
    url
    isExternal
    __typename
  }
}"""


class CanonOrderMissing(RuntimeError):
    """El build visto en el HTML no tiene orden de canon cacheado."""


class AttestRejected(RuntimeError):
    """El servidor respondió 403 de aplicación (attest rechazado)."""


@dataclass
class Event:
    kind: str            # EventResult | ArtistResult | ...
    name: str
    url: str = ""
    slug: str = ""
    start: str = ""
    end: str = ""
    city: str = ""
    country: str = ""
    location: str = ""
    category: str = ""

    def as_row(self) -> dict[str, str]:
        return {
            "tipo": self.kind.replace("Result", ""),
            "nombre": self.name,
            "inicio": (self.start or "")[:16].replace("T", " "),
            "ciudad": self.city,
            "país": self.country,
            "categoría": self.category,
            "url": self.url,
        }


@dataclass
class ScrapeResult:
    query: str
    source: str
    events: list[Event] = field(default_factory=list)
    pages: int = 1
    elapsed_ms: int = 0
    diag: dict[str, Any] = field(default_factory=dict)

    @property
    def artists(self) -> list[Event]:
        return [e for e in self.events if e.kind == "ArtistResult"]

    @property
    def only_events(self) -> list[Event]:
        return [e for e in self.events if e.kind == "EventResult"]


# --------------------------------------------------------------------------- #
# Caché de órdenes de canon (buildId -> [campos])
# --------------------------------------------------------------------------- #

def load_orders(path: Path | None = None) -> dict[str, list[str]]:
    p = Path(path or _ORDERS_FILE)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_orders(orders: dict[str, list[str]], path: Path | None = None) -> bool:
    """Guarda el mapa buildId->orden. Silencioso si el FS es de solo lectura
    (Streamlit Cloud lo es): el orden sigue en memoria para esta sesión."""
    p = Path(path or _ORDERS_FILE)
    try:
        p.write_text(json.dumps(orders, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False


_ORDERS: dict[str, list[str]] = load_orders()


def register_order(build_id: str, order: Iterable[str]) -> None:
    order = list(order)
    if sorted(order) != sorted(CANON_FIELDS):
        raise ValueError(f"orden incompleto/inválido: {order}")
    _ORDERS[build_id] = order
    save_orders(_ORDERS)


# --------------------------------------------------------------------------- #
# Cliente
# --------------------------------------------------------------------------- #

class TicketSwapClient:
    def __init__(self, market: str = "es", canon_order: list[str] | None = None):
        if market not in SITES:
            raise ValueError(f"mercado desconocido: {market}")
        self.market = market
        self.site = SITES[market]
        self.session = httpcloak.Session(preset=PRESET)
        self.canon_order = canon_order
        self._build_id: str | None = None
        self._dyn: str | None = None
        self._server_ts: int | None = None

    # -- parsing del HTML ---------------------------------------------------- #

    @staticmethod
    def retrieve_stds(html: str) -> str | None:
        """`stds:"<serverTs>.<dyn>"` -> decodifica \\/ y \\u002b (no solo \\/)."""
        m = re.search(r'stds\s*:\s*"((?:[^"\\]|\\.)*)"', html)
        if not m:
            return None
        try:
            return json.loads('"%s"' % m.group(1))
        except Exception:
            return m.group(1).replace("\\/", "/")

    @staticmethod
    def retrieve_v(html: str) -> str | None:
        m = re.search(r"[?&]v=([0-9a-zA-Z._-]{8,})", html)
        if m:
            return m.group(1)
        m = re.search(r"cachejs_([0-9a-zA-Z]{8,})_", html)
        return m.group(1) if m else None

    # -- cripto -------------------------------------------------------------- #

    @staticmethod
    def hmac_key(dyn: str, nonce: str, client_ts: int) -> str:
        return hashlib.sha256(
            f"{KEY_PREFIX}{dyn}|{nonce}|{client_ts}".encode()
        ).hexdigest()

    @staticmethod
    def create_hmac_signature(key: str, msg: str) -> bytes:
        return hmac.new(key.encode(), msg.encode(), hashlib.sha256).digest()

    def canon_text(self, order: list[str], dyn: str, nonce: str, client_ts: int,
                   body: str, method: str, path: str, query: str,
                   build_id: str, ts_diff: int) -> str:
        fields = {
            "path": path,
            "tsDiff": str(ts_diff),
            "clientTs": str(client_ts),
            "nonce": nonce,
            "body": hashlib.sha256(body.encode()).hexdigest(),
            "buildId": build_id,
            "fingerprint": "",
            "method": method,
            "query": query,
        }
        return "\n".join("%s:%s" % (k, fields[k]) for k in order)

    def sign(self, order, dyn, nonce, client_ts, body, method, path, query,
             build_id, ts_diff) -> str:
        canon = self.canon_text(order, dyn, nonce, client_ts, body, method,
                               path, query, build_id, ts_diff)
        key = self.hmac_key(dyn, nonce, client_ts)
        return base64.b64encode(self.create_hmac_signature(key, canon)).decode()

    def build_attestation(self, order, dyn, server_ts, build_id, body,
                          nonce: str, client_ts: int,
                          method: str = "POST", path: str = GQL_PATH,
                          query: str = GQL_QUERY) -> str:
        # client_ts es el MISMO que se usa para derivar la key (si difiere -> 403)
        ts_diff = client_ts - server_ts
        sig = self.sign(order, dyn, nonce, client_ts, body, method, path,
                        query, build_id, ts_diff)
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

    # -- HTTP ---------------------------------------------------------------- #

    def first_request(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
            "Connection": "keep-alive",
        }
        return self.session.get(self.site + "/", headers=headers)

    def last_request(self, web_attest: str, nonce: str, body: str):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
            "Accept": "application/graphql-response+json,application/json;q=0.9",
            "Accept-Language": "es-ES",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": self.site + "/",
            "Origin": self.site,
            "Content-Type": "application/json",
            "Device-Id": str(uuid.uuid4()),
            "Web-Attest": web_attest,
            "Web-Request-Id": nonce,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Priority": "u=4",
        }
        return self.session.post(
            self.site + GQL_PATH,
            params={"version": "8"},
            headers=headers,
            data=body,          # el string exacto firmado; NUNCA json=dict
        )

    # -- alto nivel ---------------------------------------------------------- #

    def load_body(self, query: str, after: str | None = None) -> str:
        payload = [{
            "extensions": {"clientLibrary": {"name": "@apollo/client", "version": "4.2.9"}},
            "operationName": "getSearchResults",
            "query": SEARCH_QUERY,
            "variables": {"after": after, "query": query, "type": None},
        }]
        return json.dumps(payload, separators=(",", ":"))

    def refresh_context(self) -> dict[str, Any]:
        """GET / (200 o 202) -> buildId + dyn + serverTs, en el MISMO cliente."""
        resp = self.first_request()
        html = resp.text or ""
        stds = self.retrieve_stds(html)
        build_id = self.retrieve_v(html)
        if not stds or not build_id:
            raise AttestRejected(
                f"GET / devolvió {resp.status_code} ({len(html)} B) sin stds/buildId; "
                "probablemente bloqueo de borde (WAAP). Reintenta en unos segundos."
            )
        server_ts, dyn = stds.split(".", 1)
        self._build_id = build_id
        self._dyn = dyn
        self._server_ts = int(server_ts)
        return {
            "status": resp.status_code,
            "html_bytes": len(html),
            "challenge": "Verifying" in html,
            "build_id": build_id,
            "dyn": dyn,
            "server_ts": self._server_ts,
        }

    def _order_for(self, build_id: str) -> list[str]:
        if self.canon_order:
            return self.canon_order
        if build_id in _ORDERS:
            return _ORDERS[build_id]
        raise CanonOrderMissing(build_id)

    def graphql(self, body: str, force_order: list[str] | None = None):
        """Firma y envía un body ya serializado. Devuelve (status, text, nonce)."""
        if self._dyn is None or self._build_id is None:
            self.refresh_context()
        order = force_order or self._order_for(self._build_id)
        nonce = uuid.uuid4().hex
        client_ts = int(time.time() * 1000)
        attest = self.build_attestation(
            order, self._dyn, self._server_ts, self._build_id, body,
            nonce=nonce, client_ts=client_ts,
        )
        resp = self.last_request(attest, nonce, body)
        return resp.status_code, resp.text, nonce

    def search(self, query: str, pages: int = 1) -> ScrapeResult:
        t0 = time.time()
        diag: dict[str, Any] = {}
        ctx = self.refresh_context()
        diag.update(ctx)
        diag["order_cached"] = self._build_id in _ORDERS

        candidates: list[list[str]] = []
        if self.canon_order:
            candidates = [list(self.canon_order)]
        elif self._build_id in _ORDERS:
            candidates = [_ORDERS[self._build_id]]
        else:
            seen = []
            for o in _ORDERS.values():
                if o not in seen:
                    seen.append(o)
            candidates = seen
        diag["orders_tried"] = len(candidates)

        events: list[Event] = []
        after: str | None = None
        got = 0
        status = 0
        for page in range(max(1, pages)):
            body = self.load_body(query, after)
            order = candidates[0] if len(candidates) == 1 else None

            if order is not None:
                status, text, _ = self.graphql(body, force_order=order)
            else:
                status, text = 0, ""
                for cand in candidates:
                    status, text, _ = self.graphql(body, force_order=cand)
                    if status == 200:
                        register_order(self._build_id, cand)   # aprendido
                        candidates = [cand]
                        diag["order_learned"] = cand
                        break
                    time.sleep(1.2)

            if status == 403:
                # dyn puede haber rotado: refresca contexto y reintenta 1 vez
                self.refresh_context()
                diag["retry_after_403"] = True
                order = self._order_for(self._build_id)
                status, text, _ = self.graphql(body, force_order=order)

            if status != 200:
                diag["status"] = status
                diag["body_snippet"] = text[:300]
                raise AttestRejected(
                    f"GraphQL {status} {text[:160]!r}"
                )

            try:
                data = json.loads(text)[0]["data"]["search"]
            except Exception:
                diag["status"] = status
                diag["body_snippet"] = text[:300]
                raise AttestRejected(f"respuesta inesperada: {text[:160]!r}")

            page_events = [self._node_to_event(e.get("node") or {})
                           for e in data.get("edges") or []]
            page_events = [e for e in page_events if e]
            events.extend(page_events)
            got += len(page_events)

            info = data.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = info.get("endCursor")
            if not after:
                break
            time.sleep(0.8)

        diag["status"] = status
        diag["raw_nodes"] = got
        return ScrapeResult(
            query=query,
            source=f"ticketswap.{self.market}",
            events=events,
            pages=max(1, pages),
            elapsed_ms=int((time.time() - t0) * 1000),
            diag=diag,
        )

    @staticmethod
    def _node_to_event(n: dict[str, Any]) -> Event | None:
        kind = n.get("__typename") or ""
        if not kind:
            return None
        uri = n.get("uri") or {}
        url = uri.get("url") or ""
        if not url and uri.get("path"):
            url = "https://www.ticketswap.es" + uri["path"]
        country = (n.get("country") or {}).get("name") or ""
        return Event(
            kind=kind,
            name=n.get("name") or n.get("slug") or "",
            url=url,
            slug=n.get("slug") or "",
            start=n.get("startDate") or "",
            end=n.get("endDate") or "",
            city=n.get("cityName") or "",
            country=country,
            location=n.get("locationName") or "",
            category=n.get("category") or "",
        )


def scrape(query: str, market: str = "es", pages: int = 1) -> ScrapeResult:
    """Punto de entrada usado por la app."""
    return TicketSwapClient(market=market).search(query, pages=pages)
