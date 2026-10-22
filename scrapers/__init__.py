"""Scrapers disponibles para la app. Cada módulo expone `scrape(query, **kw)`."""

from . import ticketswap  # noqa: F401

REGISTRY = {
    "TicketSwap": ticketswap,
}


def available() -> list[str]:
    return list(REGISTRY)


def get(name: str):
    return REGISTRY[name]
