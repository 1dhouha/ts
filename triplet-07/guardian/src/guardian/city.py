"""2D city view: the City Brain's map with Guardian's verdicts drawn on it.
Read-only: it only serves a page and the map picture; the data comes from the existing /timeline/events stream."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

STATIC = Path(__file__).parent / "static"


def register(app: FastAPI) -> None:
    @app.get("/city", response_class=HTMLResponse, include_in_schema=False)
    def city_page():
        return (STATIC / "city.html").read_text(encoding="utf-8")

    @app.get("/city/map.svg", include_in_schema=False)
    def city_map():
        return FileResponse(STATIC / "city-map.svg", media_type="image/svg+xml")
