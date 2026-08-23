"""Fetch nearby gas prices and render the static dashboard page.

Run daily by .github/workflows/update-prices.yml. Queries GasBuddy's public
GraphQL endpoint via py-gasbuddy (no API key), picks the 4 cheapest regular
stations within a radius of CENTER, writes prices.json, and bakes the
result into index.html so the page needs no client-side JS to display
correctly for a screenshot pipeline.

Uses a custom GraphQL query rather than py_gasbuddy's price_lookup_service:
that library's built-in LOCATION_QUERY_PRICES doesn't request the station
`name` field and its parser drops the `address` it does request, so every
station comes back as "Unknown" with a blank address.

GasBuddy's search has no radius parameter — each query returns a fixed
catchment around one zipcode. To actually search a radius (see geo.py) we:
  1. resolve CENTER (a zipcode or a free-form address) to lat/lon
  2. find every zipcode whose centroid falls within RADIUS_MILES
  3. query GasBuddy for each of those zipcodes and merge/dedupe by station id
  4. drop any station whose own lat/lon falls outside RADIUS_MILES, since a
     zipcode's area can extend beyond the radius even if its centroid doesn't
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from string import Template

from py_gasbuddy import GasBuddy

import geo

CENTER = "83854"  # zipcode or free-form address, e.g. "355 E Neider Ave, Coeur d'Alene, ID"
RADIUS_MILES = 8  # wide enough to reach the Costco in Coeur d'Alene (~7mi away)
MAX_ZIPCODES = 20  # cap GasBuddy calls if a large radius pulls in many zipcodes
LOOKUP_LIMIT = 15
TOP_N = 7
FLARESOLVERR_URL = os.environ.get("FLARESOLVERR_URL")  # e.g. http://localhost:8191/v1

STATION_QUERY = (
    "query LocationBySearchTerm($brandId: Int, $cursor: String, $fuel: Int, "
    "$lat: Float, $lng: Float, $maxAge: Int, $search: String) { "
    "locationBySearchTerm(lat: $lat, lng: $lng, search: $search) { "
    "stations(brandId: $brandId cursor: $cursor fuel: $fuel lat: $lat lng: $lng maxAge: $maxAge) { "
    "results { id name address { line1 locality } latitude longitude "
    "prices { credit { price } fuelProduct } } } } }"
)

ROOT = Path(__file__).resolve().parent.parent
PRICES_JSON = ROOT / "prices.json"
TEMPLATE_HTML = ROOT / "scripts" / "template.html"
OUTPUT_HTML = ROOT / "index.html"

ROW_TEMPLATE = Template(
    """
    <li class="row">
      <span class="rank">$rank</span>
      <span class="details">
        <span class="name">$name</span>
        <span class="address">$address</span>
      </span>
      <span class="price">$price</span>
    </li>"""
)


async def fetch_zipcode(gb, zipcode):
    query = {
        "operationName": "LocationBySearchTerm",
        "query": STATION_QUERY,
        "variables": {"maxAge": 0, "search": str(zipcode)},
    }
    response = await gb.process_request(query)
    if "error" in response or "errors" in response:
        print(f"GasBuddy API error for {zipcode}: {response}", file=sys.stderr)
        return []
    return response["data"]["locationBySearchTerm"]["stations"]["results"][:LOOKUP_LIMIT]


async def fetch_stations():
    zip_centroids = await geo.load_zip_centroids()
    center_lat, center_lon = await geo.resolve_center(CENTER, zip_centroids)

    zipcodes = geo.zips_within_radius(center_lat, center_lon, RADIUS_MILES, zip_centroids)
    if not zipcodes:
        zipcodes = [CENTER] if geo.ZIPCODE_RE.match(CENTER) else []
    if len(zipcodes) > MAX_ZIPCODES:
        zipcodes.sort(
            key=lambda z: geo.haversine_miles(center_lat, center_lon, *zip_centroids[z])
        )
        print(
            f"{len(zipcodes)} zipcodes within {RADIUS_MILES}mi, "
            f"capping to the nearest {MAX_ZIPCODES}",
            file=sys.stderr,
        )
        zipcodes = zipcodes[:MAX_ZIPCODES]

    gb = GasBuddy(solver_url=FLARESOLVERR_URL) if FLARESOLVERR_URL else GasBuddy()
    results_by_id = {}
    for i, zipcode in enumerate(zipcodes):
        if i:
            await asyncio.sleep(1)  # avoid tripping GasBuddy's Cloudflare rate limit
        for station in await fetch_zipcode(gb, zipcode):
            results_by_id[station["id"]] = station

    stations = []
    for station in results_by_id.values():
        regular = next(
            (p for p in station.get("prices", []) if p.get("fuelProduct") == "regular_gas"),
            None,
        )
        price = regular["credit"]["price"] if regular and regular.get("credit") else None
        if not price:
            continue
        address = station.get("address") or {}
        line1 = (address.get("line1") or "").strip()
        locality = (address.get("locality") or "").strip()
        stations.append(
            {
                "name": station.get("name") or "Unknown",
                "address": f"{line1}, {locality}" if locality else line1,
                "price": float(price),
                "formatted_price": f"${price:.2f}",
                "latitude": station.get("latitude"),
                "longitude": station.get("longitude"),
            }
        )

    stations = geo.stations_within_radius(stations, center_lat, center_lon, RADIUS_MILES)
    for s in stations:
        del s["latitude"]
        del s["longitude"]
    stations.sort(key=lambda s: s["price"])
    return stations[:TOP_N]


def render_html(stations, updated_at):
    rows_html = "".join(
        ROW_TEMPLATE.substitute(
            rank=i + 1,
            name=s["name"],
            address=s["address"],
            price=s["formatted_price"],
        )
        for i, s in enumerate(stations)
    )
    template = Template(TEMPLATE_HTML.read_text())
    html = template.substitute(rows=rows_html, updated=updated_at)
    OUTPUT_HTML.write_text(html)


def main():
    stations = asyncio.run(fetch_stations())

    if not stations:
        print("No stations returned — leaving existing index.html/prices.json untouched.", file=sys.stderr)
        sys.exit(1)

    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %I:%M %p UTC")

    PRICES_JSON.write_text(
        json.dumps(
            {
                "center": CENTER,
                "radius_miles": RADIUS_MILES,
                "updated": updated_at,
                "stations": stations,
            },
            indent=2,
        )
    )
    render_html(stations, updated_at)
    print(f"Wrote {len(stations)} stations to prices.json and index.html")


if __name__ == "__main__":
    main()
