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

### ⚠️ Lo que hay que saber antes de desplegar

- **La IP del runner es de datacenter.** TicketSwap está detrás de un WAAP que
  devuelve *challenge* a IPs de DC. La app **funciona igual** (el challenge trae
  `stds`/`buildId` en 1.9 KB), pero si el WAAP endurece el rate-limit verás
  `403`/`202` sin datos. Por eso el diagnóstico muestra `HTTP` y `challenge`.
- **El orden del canon cambia con cada build** del SDK del sitio. Va cacheado en
  `canon_orders.json` (buildId → 9 campos). Si sale un build nuevo:
  - la app lo intenta con todos los órdenes conocidos (si acierta, lo **aprende**
    y lo escribe en el JSON);
  - si ninguno vale, muestra "build nuevo sin orden cacheado" y hay que capturarlo
    una vez: hook sobre `crypto.subtle.sign` en el navegador, leer el texto firmado
    (las 9 líneas `campo:valor`) y pegarlo en *Ajustes → Canon del build*.
- `canon_orders.json` en la raíz es **imprescindible**; no lo borres al desplegar.

## Cómo funciona la firma (`web-attest`)

```
GET /                       -> HTML (200 app o 202 challenge)
    de ahí: stds:"<serverTs>.<dyn>"  y  ?v=<buildId>
key = sha256("sig|"+dyn+"|"+nonce+"|"+clientTs).hexdigest()   # 64 chars ASCII
sig = base64(HMAC-SHA256(key, canon))                          # canon = 9 líneas, orden del build
POST /api/graphql/public?version=8
    headers: Web-Attest: <b64 envelope>   Web-Request-Id: <nonce>
    body:    data=<exactamente el string firmado>              # nunca json=dict
```

Trampas que cuestan un `403`: `nonce` repetido · `fp` (línea) ausente · orden del
canon viejo · `cv`/`b` en el envelope · re-serializar el body con espacios.

## Estructura

```
main.py                 UI Streamlit
scrapers/__init__.py    registro de fuentes
scrapers/ticketswap.py  cliente + firma + paginación
canon_orders.json       buildId -> orden del canon
```

Añadir otra fuente = crear `scrapers/mifuente.py` con `scrape(query, **kw) -> ScrapeResult`
y registrarlo en `scrapers/__init__.py` (`REGISTRY`). La UI lo detecta sola.

## Aviso

Datos públicos de TicketSwap para uso personal/educativo. La firma replica la del
propio SDK del sitio; respeta los términos de servicio y no lo uses a alto volumen.
