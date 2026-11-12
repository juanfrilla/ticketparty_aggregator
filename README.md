# 🎟️ Ticketparty Aggregator

Buscador multi-scraper de eventos de música, construido con **Streamlit**. Busca un artista o DJ en varias plataformas de venta de entradas y devuelve su perfil junto con los eventos y el enlace directo a las entradas.

Actualmente incluye scrapers para **TicketSwap**, **Enterticket** y **Technobeats Club**.

Ejemplo por defecto: **`dj marta`**.

```
Eventos  3      Artistas  17
- VANTA CLUB presents DJ MARTA   · 2026-12-24 · Madrid
- Makineros XXL Winter Edition   · 2026-11-28 · Madrid
- AMW ADE 24/5 DJ Marathon       · 2026-10-24 · Amsterdam
```

## ✨ Características

- Búsqueda por artista/DJ desde una única interfaz.
- Perfil de artista + listado de eventos (nombre, fecha, ciudad).
- Enlace directo a la compra de entradas.
- Arquitectura modular: cada plataforma es un scraper independiente en `scrapers/`.
- Datos cacheados en `data/` para evitar scrapear de más.

## 📁 Estructura del proyecto

```
.
├── README.md
├── main.py                  # App Streamlit (punto de entrada)
├── constants.py             # Constantes globales
├── utils.py                 # Utilidades compartidas
├── notes.txt                # Notas de desarrollo
├── packages.txt             # Dependencias del sistema (Streamlit Cloud)
├── requirements.txt         # Dependencias Python
├── data/
│   ├── canon.json           # Datos canónicos de artistas
│   ├── events.json          # Eventos obtenidos
│   └── last_scraped.json    # Registro del último scrapeo
├── scrapers/
│   ├── __init__.py
│   ├── enterticket.py       # Scraper de Enterticket
│   ├── technobeatsclub.py   # Scraper de Technobeats Club
│   └── ticketswap/
│       ├── __init__.py
│       ├── ticketswap.py    # Scraper principal de TicketSwap
│       └── capture.py       # Captura de peticiones/sesión
└── work/
    └── live/
        └── captured_canon.txt   # Capturas de trabajo
```

## 🚀 Instalación local

Requisitos: Python 3.12+

```bash
uv venv .venv                       # o python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run main.py
```

La app se abrirá en `http://localhost:8501`.

## ☁️ Despliegue en Streamlit Cloud

1. Sube el repo a GitHub (`main.py`, `scrapers/`, `data/`, `constants.py`, `utils.py`, `requirements.txt`, `packages.txt`).
2. Ve a [share.streamlit.io](https://share.streamlit.io) → *New app* → selecciona repo y `main.py` → **Deploy**.
3. `httpcloak` tiene wheel `manylinux_x86_64` en PyPI, así que se instala sin compilar.

## ➕ Añadir un nuevo scraper

1. Crea un módulo en `scrapers/` (por ejemplo `scrapers/nueva_plataforma.py`).
2. Implementa una función de búsqueda que devuelva eventos con campos homogéneos: nombre, fecha, ciudad y URL de entradas.
3. Regístralo en `scrapers/__init__.py` para que `main.py` lo incluya en la búsqueda.

## 🗂️ Datos

| Archivo | Descripción |
|---|---|
| `data/canon.json` | Información canónica de artistas, usada para unificar nombres entre plataformas |
| `data/events.json` | Eventos obtenidos en el último scrapeo |
| `data/last_scraped.json` | Marcas de tiempo del último scrapeo por fuente |

## ⚠️ Aviso legal

Este proyecto es solo para uso personal y educativo. Respeta los términos de servicio y el `robots.txt` de cada plataforma, y no abuses de la frecuencia de las peticiones.

## 📝 .gitignore recomendado

```gitignore
__pycache__/
*.pyc
.venv/
work/
```