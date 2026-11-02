import json
import re

import httpcloak

from constants import ARTISTS


class TechnoBeatsClubScraper:
    def __init__(self):
        self.session = httpcloak.Session(preset="chrome-150-windows")

    def artist_request(self, artist: str):

        burp0_url = f"https://technobeatscloud.com:443/agenda/deejay/{artist}?_rsc=G0kqivbQnK3d_CUC"

        burp0_headers = {
            "Rsc": "1",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Accept-Language": "es-ES,es;q=0.9",
            "Sec-Ch-Ua": '"Not:A-Brand";v="8", "Chromium";v="150"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Next-Url": f"/es/escena/deejays/{artist}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": f"https://technobeatscloud.com/escena/deejays/{artist}",
            "Accept-Encoding": "gzip, deflate, br",
            "Priority": "u=1, i",
        }
        return self.session.get(
            burp0_url,
            headers=burp0_headers,
        )

    def retrieve_events(self, data):
        target_key = "initialEvents"

        if isinstance(data, dict):
            # Si la clave está directamente en este diccionario, la devolvemos
            if target_key in data:
                return data[target_key]
            # Si no, buscamos en cada valor del diccionario
            for value in data.values():
                result = self.retrieve_events(value)
                if result is not None:
                    return result

        elif isinstance(data, list):
            # Si es una lista, iteramos sobre cada elemento
            for item in data:
                result = self.retrieve_events(item)
                if result is not None:
                    return result

        # Si no se encuentra, devolvemos None
        return None

    def scrape(self):
        scraped_data = []
        for artist in ARTISTS:
            print(f"Scraping {artist}")
            artist = artist.replace(" ", "-")
            resp = self.artist_request(artist)
            fields = resp.text.split("\n")
            for field in fields:
                if artist in field and "initialEvents" in field:
                    splitted = re.split(r"^\w+:", field, maxsplit=1)
                    data = json.loads(splitted[1])
                    important_data = data[3]
                    events = self.retrieve_events(important_data)
                    for event in events:
                        slug = event.get("slug")
                        scraped_data.append(
                            {
                                "name": event.get("name"),
                                "artist": artist,
                                "start_date": event.get("starts_at_utc"),
                                "end_date": event.get("ends_at_utc"),
                                "club": event.get("venue_name_override"),
                                "city_name": event.get("city_name"),
                                "event_url": f"https://technobeatscloud.com/agenda/evento/{slug}",
                                "image_url": event.get("computed_flyer_url"),
                                "origin": "https://technobeatscloud.com",
                            }
                        )
        return scraped_data
