from datetime import UTC, datetime

import httpcloak

from constants import ARTISTS
from utils import parse_date


class EnterTicketScraper:
    def __init__(self):
        self.session = httpcloak.Session(preset="chrome-150-windows")

    def artist_request(self, artist_id: str, artist_slug: str):

        burp0_url = f"https://www.enterticket.es:443/api/events/artistevents?artist={artist_id}&country=es"

        burp0_headers = {
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Accept-Language": "es-ES,es;q=0.9",
            "Sec-Ch-Ua": '"Not:A-Brand";v="8", "Chromium";v="150"',
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
            "Sec-Ch-Ua-Mobile": "?0",
            "Accept": "*/*",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": f"https://www.enterticket.es/artistas/{artist_slug}",
            "Accept-Encoding": "gzip, deflate, br",
            "Priority": "u=1, i",
        }
        return self.session.get(burp0_url, headers=burp0_headers)

    def search_artist_request(self, artist_text: str):

        burp0_url = f"https://www.enterticket.es:443/api/artists?text={artist_text.replace(' ', '%20')}"
        burp0_headers = {
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Accept-Language": "es-ES,es;q=0.9",
            "Sec-Ch-Ua": '"Not:A-Brand";v="8", "Chromium";v="150"',
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
            "Sec-Ch-Ua-Mobile": "?0",
            "Accept": "*/*",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": f"https://www.enterticket.es/?text={artist_text.replace(' ', '+')}",
            "Accept-Encoding": "gzip, deflate, br",
            "Priority": "u=1, i",
        }
        return self.session.get(burp0_url, headers=burp0_headers)

    def scrape(self):
        scraped_data = []
        now = datetime.now(UTC).replace(tzinfo=None)
        for artist in ARTISTS:
            print(f"Scraping {artist}")
            search_resp = self.search_artist_request(artist)
            search_data = search_resp.json()
            artists = search_data.get("data")
            artist_id = None
            if artists:
                if len(artists) == 1:
                    artist_id = artists[0].get("id")
                    artist_slug = artists[0].get("slug")
                else:
                    for artist_data in artists:
                        if "dj" in artist_data.get("slug"):
                            artist_id = artist_data.get("id")
                if not artist_id:
                    raise Exception(f"Artist id not found for {artist}")

                artist_resp = self.artist_request(artist_id, artist_slug)
                events = artist_resp.json()
                if events:
                    for event in events:
                        venue = event.get("venue", {})
                        header_image = event.get("header_image")
                        slug = event.get("slug")

                        event_id = event.get("id")

                        existing_event = next(
                            (item for item in scraped_data if item["id"] == event_id),
                            None,
                        )

                        start_date = event.get("start_date", {}).get("date")
                        if parse_date(start_date) < now:
                            continue

                        if existing_event:
                            continue
                        else:
                            scraped_data.append(
                                {
                                    "id": event.get("id"),
                                    "name": event.get("name"),
                                    "artists": [
                                        hl.get("name")
                                        for hl in event.get("artists", [])
                                    ],
                                    "start_date": start_date,
                                    "end_date": event.get("end_date", {}).get("date"),
                                    "club": venue.get("name"),
                                    "city_name": venue.get("address", {}).get(
                                        "province"
                                    ),
                                    "event_url": f"https://www.enterticket.es/eventos/{slug}",
                                    "image_url": f"https://d31tcnbxvxtafg.cloudfront.net/images/events/{header_image}",
                                    "origin": "https://www.enterticket.es/",
                                }
                            )
        return scraped_data
