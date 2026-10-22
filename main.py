"""Ticketparty Aggregator — buscador multi-scraper (Streamlit).

Ejecutar en local:
    streamlit run main.py

Despliegue: ver README.md (ojo con el bloqueo de IP de datacenter).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

import streamlit as st

import scrapers
from scrapers.ticketswap import (
    CANON_FIELDS,
    SITES,
    AttestRejected,
    CanonOrderMissing,
    Event,
    ScrapeResult,
    _ORDERS,
    load_orders,
    register_order,
)

st.set_page_config(
    page_title="Ticketparty Aggregator",
    page_icon="🎟️",
    layout="wide",
    initial_sidebar_state="expanded",
)

DEFAULT_QUERY = "dj marta"
CACHE_TTL = int(os.environ.get("SCRAPE_TTL", "600"))


# --------------------------------------------------------------------------- #
# Caché de resultados (por fuente + query + páginas)
# --------------------------------------------------------------------------- #

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def buscar(fuente: str, market: str, query: str, pages: int) -> dict:
    """Devuelve un dict serializable (la caché de Streamlit exige picklable)."""
    mod = scrapers.get(fuente)
    t0 = time.time()
    if fuente == "TicketSwap":
        res: ScrapeResult = mod.scrape(query, market=market, pages=pages)
    else:
        res = mod.scrape(query)
    return {
        "source": res.source,
        "query": res.query,
        "elapsed_ms": res.elapsed_ms,
        "diag": res.diag,
        "rows": [e.as_row() for e in res.events],
        "kinds": [e.kind for e in res.events],
        "wall_ms": int((time.time() - t0) * 1000),
    }


def filas_a_eventos(rows: list[dict], kinds: list[str]) -> list[Event]:
    out = []
    for r, k in zip(rows, kinds):
        out.append(Event(
            kind=k,
            name=r.get("nombre", ""),
            url=r.get("url", ""),
            start=(r.get("inicio") or "").replace(" ", "T"),
            city=r.get("ciudad", ""),
            country=r.get("país", ""),
            category=r.get("categoría", ""),
        ))
    return out


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #

with st.sidebar:
    st.title("⚙️ Ajustes")

    fuentes = st.multiselect(
        "Fuentes", scrapers.available(), default=["TicketSwap"][:1] or scrapers.available()
    )
    market = st.selectbox(
        "Mercado TicketSwap", list(SITES), index=list(SITES).index("es"),
        format_func=lambda k: f"{k} — {SITES[k].replace('https://', '')}",
    )
    pages = st.slider("Páginas (20 resultados c/u)", 1, 3, 1)

    st.divider()
    with st.expander("🔐 Canon del build (avanzado)", expanded=False):
        st.caption(
            "El orden de las 9 líneas firmadas cambia con cada build del SDK. "
            "Está cacheado en `canon_orders.json`; si aparece un build nuevo "
            "pégalo aquí una vez y queda aprendido."
        )
        orders = load_orders()
        st.dataframe(
            [{"buildId": b, "orden": " → ".join(o)} for b, o in orders.items()],
            hide_index=True, width="stretch",
        )
        col_a, col_b = st.columns([3, 1])
        nuevo_build = col_a.text_input("buildId", placeholder="2836aca4722324e0")
        nuevo_orden = col_b.text_input("orden", placeholder=",".join(CANON_FIELDS[:3]) + ",...")
        if st.button("Guardar orden", width="stretch"):
            campos = [c.strip() for c in nuevo_orden.replace("→", ",").split(",") if c.strip()]
            try:
                register_order(nuevo_build.strip(), campos)
                st.success(f"Guardado para {nuevo_build}")
            except Exception as exc:
                st.error(f"Orden inválido: {exc}")

    if st.button("🧹 Limpiar caché de resultados", width="stretch"):
        st.cache_data.clear()
        st.toast("Caché vaciada")

    st.divider()
    st.caption(f"TTL caché: {CACHE_TTL}s · build de la app: {datetime.now():%Y-%m-%d %H:%M}")


# --------------------------------------------------------------------------- #
# Cabecera + búsqueda
# --------------------------------------------------------------------------- #

st.title("🎟️ Ticketparty Aggregator")
st.caption("Busca un artista/DJ y agrega los eventos y el perfil de artista que devuelve TicketSwap.")

with st.form("busqueda", border=True):
    c1, c2 = st.columns([5, 1])
    query = c1.text_input("Artista o DJ", value=DEFAULT_QUERY, placeholder="dj marta, fabrik, …")
    enviado = c2.form_submit_button("Buscar", width="stretch", type="primary")

if not enviado and "resultados" not in st.session_state:
    st.info(f"Pulsa **Buscar** para probar con *{DEFAULT_QUERY}*.")
    st.stop()

if enviado:
    if not query.strip():
        st.warning("Escribe algo para buscar.")
        st.stop()
    st.session_state["resultados"] = {}

resultados: dict[str, dict] = st.session_state.get("resultados", {})

if enviado:
    for fuente in fuentes or ["TicketSwap"]:
        with st.spinner(f"Consultando {fuente}…"):
            try:
                resultados[fuente] = buscar(fuente, market, query.strip(), pages)
            except CanonOrderMissing as exc:
                resultados[fuente] = {"error": "canon", "build": str(exc)}
            except AttestRejected as exc:
                resultados[fuente] = {"error": "attest", "detail": str(exc)}
            except Exception as exc:  # noqa: BLE001
                resultados[fuente] = {"error": "otro", "detail": f"{type(exc).__name__}: {exc}"}
    st.session_state["resultados"] = resultados


# --------------------------------------------------------------------------- #
# Resultados
# --------------------------------------------------------------------------- #

def pintar_fuente(nombre: str, data: dict) -> None:
    err = data.get("error")
    if err == "canon":
        st.error(
            f"**Build nuevo sin orden cacheado**: `{data['build']}`\n\n"
            "El endpoint exige firmar las 9 líneas en el orden exacto de ese build. "
            "Captúralo una vez (hook sobre `crypto.subtle.sign`, ver README) y pégalo en "
            "*Ajustes → Canon del build*."
        )
        return
    if err == "attest":
        st.error(f"**Attest rechazado** (403).\n\n`{data['detail']}`\n\n"
                 "Suele ser: nonce repetido, orden del canon viejo, o rate-limit de borde.")
        return
    if err == "otro":
        st.error(f"**Fallo del scraper**\n\n`{data['detail']}`")
        return

    rows = data.get("rows", [])
    kinds = data.get("kinds", [])
    eventos = filas_a_eventos(rows, kinds)
    evs = [e for e in eventos if e.kind == "EventResult"]
    arts = [e for e in eventos if e.kind == "ArtistResult"]
    otros = [e for e in eventos if e.kind not in ("EventResult", "ArtistResult")]

    diag = data.get("diag", {})
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Eventos", len(evs))
    m2.metric("Artistas", len(arts))
    m3.metric("Latencia", f"{data.get('elapsed_ms', 0)} ms")
    m4.metric("HTTP", diag.get("status", "—"))

    if arts:
        st.subheader("Artistas")
        st.dataframe(
            [{"nombre": a.name, "url": a.url} for a in arts],
            hide_index=True, width="stretch",
            column_config={"url": st.column_config.LinkColumn("perfil", display_text="abrir")},
        )

    st.subheader("Eventos")
    if evs:
        filas = []
        for e in evs:
            filas.append({
                "evento": e.name,
                "inicio": (e.start or "")[:16].replace("T", " "),
                "ciudad": e.city,
                "país": e.country,
                "sala": e.location,
                "categoría": e.category,
                "url": e.url,
            })
        st.dataframe(
            filas, hide_index=True, width="stretch",
            column_config={"url": st.column_config.LinkColumn("entradas", display_text="abrir")},
        )
        csv = "\n".join([",".join(filas[0].keys())] + [
            ",".join('"%s"' % str(v).replace('"', "'") for v in f.values()) for f in filas
        ])
        st.download_button("⬇️ CSV", csv, file_name=f"{data['query']}_ticketswap.csv",
                           mime="text/csv")
    else:
        st.warning("Sin eventos para esa búsqueda (solo perfiles u otros tipos).")

    if otros:
        with st.expander(f"Otros resultados ({len(otros)})"):
            st.dataframe([{"tipo": o.kind, "nombre": o.name, "url": o.url} for o in otros],
                         hide_index=True, width="stretch")

    with st.expander("🔎 Diagnóstico de la firma"):
        safe = {k: v for k, v in diag.items() if k != "dyn"}
        safe["dyn_prefix"] = (diag.get("dyn") or "")[:12] + "…"
        st.json(safe)
        st.caption(
            "buildId y `dyn` salen del HTML (`stds`), el orden del canon de "
            "`canon_orders.json`, y el HMAC se calcula localmente."
        )


if resultados:
    tabs = st.tabs(list(resultados))
    for tab, (nombre, data) in zip(tabs, resultados.items()):
        with tab:
            pintar_fuente(nombre, data)
else:
    st.info("Pulsa **Buscar** para empezar.")

st.divider()
st.caption(
    "Datos públicos de TicketSwap. Uso personal/educativo: la firma `web-attest` "
    "replica la del propio SDK del sitio."
)
