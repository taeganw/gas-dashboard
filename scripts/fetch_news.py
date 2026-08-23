"""Fetch local North Idaho headlines and render a static news page.

Run daily by scripts/publish.sh alongside fetch_prices.py. Pulls KREM 2's
"local/idaho" RSS feed (Spokane CBS affiliate covering North Idaho,
including Coeur d'Alene and Post Falls), takes the 5 most recent
headlines, writes news.json, and bakes the result into news.html so the
page needs no client-side JS to display correctly for a screenshot
pipeline (same 800x480 e-paper canvas as index.html).
"""

import html
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from string import Template
from urllib.request import Request, urlopen

FEED_URL = "https://www.krem.com/feeds/syndication/rss/news/local/idaho"
SOURCE_NAME = "KREM 2 News"
TOP_N = 5
REQUEST_TIMEOUT = 20

ROOT = Path(__file__).resolve().parent.parent
NEWS_JSON = ROOT / "news.json"
TEMPLATE_HTML = ROOT / "scripts" / "news_template.html"
OUTPUT_HTML = ROOT / "news.html"

ROW_TEMPLATE = Template(
    """
    <li class="row">
      <span class="rank">$rank</span>
      <span class="details">
        <span class="headline">$headline</span>
        <span class="meta">$source · $time_ago</span>
      </span>
    </li>"""
)


def relative_time(published: datetime, now: datetime) -> str:
    delta = now - published
    seconds = delta.total_seconds()
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    if seconds < 7 * 86400:
        return f"{int(seconds // 86400)}d ago"
    return published.strftime("%b %-d")


def fetch_headlines():
    request = Request(FEED_URL, headers={"User-Agent": "gas-dashboard/1.0 (personal news tracker)"})
    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        raw = response.read()

    root = ET.fromstring(raw)
    now = datetime.now(timezone.utc)
    headlines = []
    for item in root.findall(".//item")[:TOP_N]:
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        pub_date_text = item.findtext("pubDate")
        try:
            published = parsedate_to_datetime(pub_date_text)
        except (TypeError, ValueError):
            published = now
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)

        headlines.append(
            {
                "headline": title,
                "source": SOURCE_NAME,
                "link": item.findtext("link") or "",
                "published": published.isoformat(),
                "time_ago": relative_time(published, now),
            }
        )
    return headlines


def render_html(headlines, updated_at):
    rows_html = "".join(
        ROW_TEMPLATE.substitute(
            rank=i + 1,
            headline=html.escape(h["headline"]),
            source=html.escape(h["source"]),
            time_ago=html.escape(h["time_ago"]),
        )
        for i, h in enumerate(headlines)
    )
    template = Template(TEMPLATE_HTML.read_text())
    output = template.substitute(rows=rows_html, updated=updated_at)
    OUTPUT_HTML.write_text(output)


def main():
    headlines = fetch_headlines()

    if not headlines:
        print("No headlines returned — leaving existing news.html/news.json untouched.", file=sys.stderr)
        sys.exit(1)

    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %I:%M %p UTC")

    NEWS_JSON.write_text(
        json.dumps({"source": SOURCE_NAME, "updated": updated_at, "headlines": headlines}, indent=2)
    )
    render_html(headlines, updated_at)
    print(f"Wrote {len(headlines)} headlines to news.json and news.html")


if __name__ == "__main__":
    main()
