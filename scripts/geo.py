"""Radius search helpers: resolve a center point and find zip codes near it.

GasBuddy's API has no radius parameter of its own — each query returns a
fixed catchment around one zipcode. To search a real radius we:
  1. resolve the center (a zipcode or a free-form address) to lat/lon
  2. find every US zipcode whose centroid falls within that radius, using
     the GeoNames US postal code dataset (cached locally after first fetch)
  3. hand that list of zipcodes to the caller to query GasBuddy with
  4. let the caller filter individual stations by their own lat/lon, since
     a zipcode's area can extend beyond the radius even if its centroid
     doesn't
"""

from __future__ import annotations

import csv
import io
import math
import re
import time
import zipfile
from pathlib import Path
from typing import Iterable

import aiohttp

CACHE_DIR = Path(__file__).resolve().parent / ".cache"
ZIP_DATA_PATH = CACHE_DIR / "geonames_us_zip.txt"
ZIP_DATA_URL = "https://download.geonames.org/export/zip/US.zip"
ZIP_DATA_MAX_AGE = 30 * 24 * 60 * 60  # re-download monthly; centroids barely change

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "gas-dashboard/1.0 (personal price tracker)"}

ZIPCODE_RE = re.compile(r"^\d{5}$")

EARTH_RADIUS_MILES = 3958.8


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


async def _download_zip_data() -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(ZIP_DATA_URL) as response:
            response.raise_for_status()
            return await response.read()


async def _ensure_zip_data_cached() -> Path:
    if ZIP_DATA_PATH.exists():
        age = time.time() - ZIP_DATA_PATH.stat().st_mtime
        if age < ZIP_DATA_MAX_AGE:
            return ZIP_DATA_PATH

    raw = await _download_zip_data()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        text = zf.read("US.txt").decode("utf-8")

    CACHE_DIR.mkdir(exist_ok=True)
    ZIP_DATA_PATH.write_text(text)
    return ZIP_DATA_PATH


async def load_zip_centroids() -> dict[str, tuple[float, float]]:
    """Return {zipcode: (lat, lon)} for every US zipcode, from GeoNames."""
    path = await _ensure_zip_data_cached()
    centroids: dict[str, tuple[float, float]] = {}
    with path.open(newline="") as f:
        for row in csv.reader(f, delimiter="\t"):
            # country_code, postal_code, place_name, state_name, state_code,
            # county_name, county_code, community_name, community_code,
            # latitude, longitude, accuracy
            if len(row) < 11:
                continue
            zipcode, lat, lon = row[1], row[9], row[10]
            try:
                centroids[zipcode] = (float(lat), float(lon))
            except ValueError:
                continue
    return centroids


async def geocode_address(address: str) -> tuple[float, float]:
    """Geocode a free-form address via OpenStreetMap Nominatim."""
    params = {"q": address, "format": "json", "limit": "1", "countrycodes": "us"}
    async with aiohttp.ClientSession(headers=NOMINATIM_HEADERS) as session:
        async with session.get(NOMINATIM_URL, params=params) as response:
            response.raise_for_status()
            results = await response.json()
    if not results:
        raise ValueError(f"Could not geocode address: {address!r}")
    return float(results[0]["lat"]), float(results[0]["lon"])


async def resolve_center(
    center: str, zip_centroids: dict[str, tuple[float, float]]
) -> tuple[float, float]:
    """Resolve a zipcode or free-form address to (lat, lon)."""
    center = center.strip()
    if ZIPCODE_RE.match(center):
        if center not in zip_centroids:
            raise ValueError(f"Unknown zipcode: {center!r}")
        return zip_centroids[center]
    return await geocode_address(center)


def zips_within_radius(
    center_lat: float,
    center_lon: float,
    radius_miles: float,
    zip_centroids: dict[str, tuple[float, float]],
) -> list[str]:
    """Every zipcode whose centroid is within radius_miles of the center."""
    return [
        zipcode
        for zipcode, (lat, lon) in zip_centroids.items()
        if haversine_miles(center_lat, center_lon, lat, lon) <= radius_miles
    ]


def stations_within_radius(
    stations: Iterable[dict],
    center_lat: float,
    center_lon: float,
    radius_miles: float,
) -> list[dict]:
    """Filter stations (with lat/lon) to those within radius_miles, and
    stamp each with its distance from the center."""
    kept = []
    for station in stations:
        lat, lon = station.get("latitude"), station.get("longitude")
        if lat is None or lon is None:
            continue
        distance = haversine_miles(center_lat, center_lon, lat, lon)
        if distance <= radius_miles:
            station["distance"] = round(distance, 1)
            kept.append(station)
    return kept
