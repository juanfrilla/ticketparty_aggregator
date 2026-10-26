import json
import os
from datetime import date

from constants import LAST_SCRAPED_FILE


def save_json(filename: str, data: dict):
    folder = os.path.dirname(filename)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def read_json(filename):
    # 1. Comprobamos si el archivo existe
    if not os.path.exists(filename):
        print(f"⚠️ [read_json] El archivo '{filename}' no existe todavía.")
        return []

    # 2. Comprobamos si el archivo está vacío (evita bloqueos o errores extraños)
    if os.path.getsize(filename) == 0:
        print(f"⚠️ [read_json] El archivo '{filename}' está vacío (0 bytes).")
        return []

    try:
        # Usamos encoding="utf-8" y json.load(f) directamente
        with open(filename, "r", encoding="utf-8") as f:
            info = json.load(f)
            print(f"✅ [read_json] Leídos con éxito datos de '{filename}'")
            return info

    except json.JSONDecodeError as e:
        print(
            f"❌ [read_json] Error: El archivo '{filename}' tiene un JSON corrupto o mal formado: {e}"
        )
        return []
    except Exception as e:
        print(f"❌ [read_json] Error inesperado leyendo '{filename}': {e}")
        return []


def last_scraped_today() -> bool:
    if not os.path.exists(LAST_SCRAPED_FILE):
        return False

    try:
        with open(LAST_SCRAPED_FILE, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False

    last_date = data.get("date")
    return last_date == str(date.today())


def update_last_scraped() -> None:
    data = {"date": str(date.today())}

    with open(LAST_SCRAPED_FILE, "w") as f:
        json.dump(data, f, indent=2)


def normalize_string(s: str) -> str:
    return s.title().strip()
