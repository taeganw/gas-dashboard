"""Fetch nearby gas prices and render the static dashboard page.

Run daily by .github/workflows/update-prices.yml. Queries GasBuddy's public
GraphQL endpoint via py-gasbuddy (no API key), picks the 4 cheapest regular
stations, writes prices.json, and bakes the result into index.html so the
page needs no client-side JS to display correctly for a screenshot pipeline.

Uses a custom GraphQL query rather than py_gasbuddy's price_lookup_service:
that library's built-in LOCATION_QUERY_PRICES doesn't request the station
`name` field and its parser drops the `address` it does request, so every
station comes back as "Unknown" with a blank address.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from string import Template

from py_gasbuddy import GasBuddy

ZIPCODE = 83854  # Post Falls, ID
LOOKUP_LIMIT = 15
TOP_N = 4
FLARESOLVERR_URL = os.environ.get("FLARESOLVERR_URL")  # e.g. http://localhost:8191/v1

STATION_QUERY = (
    "query LocationBySearchTerm($brandId: Int, $cursor: String, $fuel: Int, "
    "$lat: Float, $lng: Float, $maxAge: Int, $search: String) { "
    "locationBySearchTerm(lat: $lat, lng: $lng, search: $search) { "
    "stations(brandId: $brandId cursor: $cursor fuel: $fuel lat: $lat lng: $lng maxAge: $maxAge) { "
    "results { id name address { line1 locality } "
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


async def fetch_stations():
    gb = GasBuddy(solver_url=FLARESOLVERR_URL) if FLARESOLVERR_URL else GasBuddy()
    query = {
        "operationName": "LocationBySearchTerm",
        "query": STATION_QUERY,
        "variables": {"maxAge": 0, "search": str(ZIPCODE)},
    }
    response = await gb.process_request(query)
    if "error" in response or "errors" in response:
        print(f"GasBuddy API error: {response}", file=sys.stderr)
        return []

    results = response["data"]["locationBySearchTerm"]["stations"]["results"]
    stations = []
    for station in results[:LOOKUP_LIMIT]:
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
                "distance": None,
            }
        )
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
        json.dumps({"zipcode": ZIPCODE, "updated": updated_at, "stations": stations}, indent=2)
    )
    render_html(stations, updated_at)
    print(f"Wrote {len(stations)} stations to prices.json and index.html")


if __name__ == "__main__":
    main()
