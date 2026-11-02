# 🎟️ Ticketparty Aggregator

Buscador multi-scraper (Streamlit). Arranca con **TicketSwap**: busca un artista/DJ
y devuelve el perfil de artista + los eventos con enlace a entradas.

Ejemplo por defecto: **`dj marta`**.

```
Eventos  3      Artistas  17
- VANTA CLUB presents DJ MARTA   · 2026-12-24 · Madrid
- Makineros XXL Winter Edition   · 2026-11-28 · Madrid
- AMW ADE 24/5 DJ Marathon       · 2026-10-24 · Amsterdam
```

## Local

```bash
uv venv .venv                       # o python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run main.py
```

## Despliegue en Streamlit Cloud

1. Sube el repo a GitHub (`main.py`, `scrapers/`, `canon_orders.json`, `requirements.txt`).
2. share.streamlit.io → *New app* → repo + `main.py` → Deploy.
3. `httpcloak` tiene wheel `manylinux_x86_64` en PyPI, así que instala sin compilar.

