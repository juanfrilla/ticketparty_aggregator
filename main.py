import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import streamlit as st
from dateutil.parser import isoparse
from dotenv import load_dotenv

# Asegúrate de que estos módulos existan en tu estructura (o adáptalos a tus rutas)
from constants import DATA_FILE

# --- IMPORTA TU SCRAPER AQUÍ ---
from scrapers.ticketswap.ticketswap import (
    TicketSwapScraper,  # (O la ruta donde esté tu clase)
)
from utils import (
    last_scraped_today,
    read_json,
    save_json,
    update_last_scraped,
)

load_dotenv()

# Configuración inicial de la página de Streamlit
st.set_page_config(page_title="TicketParty Aggregator", page_icon="🎉", layout="wide")


def run_single_scraper(ScraperClass):
    """Función auxiliar para ejecutar un solo scraper de forma segura"""
    scraper = ScraperClass()

    # Intentamos obtener el nombre del scraper o usar el nombre de la clase
    scraper_name = getattr(scraper, "scraper_name", type(scraper).__name__)
    # logger = get_logger(scraper_name)

    try:
        print(f"Starting scraping {scraper_name}")
        events = scraper.scrape()

        # Verificamos que devuelva una lista
        if not isinstance(events, list):
            events = []

        print(f"Retrieved {len(events)} events for {scraper_name}")
        return events
    except Exception as e:
        print(f"Error in {scraper_name}: {e!s}")
        return []


def scrape_everything_parallel():
    """Ejecuta todos los scrapers de forma concurrente con ThreadPoolExecutor"""

    # Añade aquí todas las clases de tus scrapers
    scrapers = [
        TicketSwapScraper,
        # OtroScraper,
    ]

    if not scrapers:
        return []

    all_events = []
    with ThreadPoolExecutor(max_workers=len(scrapers)) as executor:
        results = list(executor.map(run_single_scraper, scrapers))

    for events in results:
        if events:
            all_events.extend(events)

    return all_events


@st.cache_resource
def get_scraper_lock():
    return threading.Lock()


@st.cache_data(ttl=86400, show_spinner=False)
def get_events_data():
    """Gestiona la caché diaria para no saturar peticiones de scraping innecesarias"""
    lock = get_scraper_lock()
    with lock:
        if last_scraped_today() and os.path.exists(DATA_FILE):
            return read_json(DATA_FILE)

        events = scrape_everything_parallel()
        if events:
            save_json(DATA_FILE, events)
            update_last_scraped()
        return events


# --- INTERFAZ GRÁFICA (STREAMLIT) ---
with st.spinner("Buscando eventos y fiestas..."):
    # os.makedirs(LOG_DIR, exist_ok=True)
    # logger = get_logger("main")
    events = get_events_data()

    if not events:
        events = []

    # --- FILTROS EN EL SIDEBAR ---
    st.sidebar.header("🎛️ Filtros de Búsqueda")

    # Filtro por Ciudad / Club usando los campos que devuelve tu scrape()
    cities = sorted(
        list({ev.get("city_name", "N/A") for ev in events if ev.get("city_name")})
    )
    selected_city = st.sidebar.selectbox("Ciudad", ["All"] + cities)

    events_after_city = [
        e
        for e in events
        if selected_city == "All" or e.get("city_name") == selected_city
    ]

    clubs = sorted(
        list({ev.get("club", "N/A") for ev in events_after_city if ev.get("club")})
    )
    selected_club = st.sidebar.selectbox("Club / Local", ["All"] + clubs)

    filtered_events = [
        e
        for e in events_after_city
        if selected_club == "All" or e.get("club") == selected_club
    ]

    # --- TÍTULO PRINCIPAL ---
    st.title(f"🎉 TicketParty Aggregator ({len(filtered_events)} eventos encontrados)")

    # Botón manual para forzar recarga de datos limpiando caché
    if st.sidebar.button("🔄 Forzar nuevo Scraping"):
        st.cache_data.clear()
        st.rerun()

    # --- PINTAR TARJETAS DE EVENTOS ---
    if not filtered_events:
        st.info("No hay eventos que coincidan con los filtros seleccionados.")

    for event in filtered_events:
        raw_date = event.get("start_date")
        is_new = False
        formatted_date = "Fecha por confirmar"

        if raw_date:
            try:
                posted_dt = isoparse(raw_date)
                now = datetime.now(UTC)
                is_new = (now - posted_dt).days < 3
                formatted_date = posted_dt.strftime("%d/%m/%Y - %H:%M")
            except Exception:
                formatted_date = str(raw_date)

        with st.container(border=True):
            nombre_evento = event.get("name", "Evento sin nombre")
            artista = event.get("artist", "Evento sin nombre")
            st.subheader(f"{nombre_evento} 🆕" if is_new else nombre_evento)

            col_info, col_button = st.columns([4, 1.5])

            # Información detallada adaptada a los campos de tu scrape()
            with col_info:
                st.write(f"🎧 ** Artista:** {artista}")
                st.write(
                    f"📍 **Club:** {event.get('club', 'N/A')} ({event.get('city_name', 'N/A')})"
                )
                st.write(f"📅 **Inicio:** {formatted_date}")

                end_date = event.get("end_date")
                if end_date:
                    try:
                        end_dt = isoparse(end_date)
                        st.write(f"🏁 **Fin:** {end_dt.strftime('%d/%m/%Y - %H:%M')}")
                    except Exception:
                        pass

            # Botón de compra / enlace directo
            with col_button:
                st.markdown("<br>", unsafe_allow_html=True)
                ticket_url = event.get("event_url", "#")
                st.markdown(
                    f"""<a href="{ticket_url}" target="_blank">
                        <button style="width:100%; border-radius:5px; background-color:#FF4B4B; color:white; border:none; padding:12px; font-weight:bold; cursor:pointer;">
                            🎫 Ver Tickets
                        </button>
                    </a>""",
                    unsafe_allow_html=True,
                )
