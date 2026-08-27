"""Render the gas price trend page (week/month/year change) from history.json.

Run daily by scripts/publish.sh, after fetch_prices.py has appended that
day's cheapest station price to history.json. No network calls — this just
reads that log and bakes trends.html so the page needs no client-side JS
(same 800x480 e-paper canvas as index.html/news.html).
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
HISTORY_JSON = ROOT / "history.json"
TRENDS_JSON = ROOT / "trends.json"
TEMPLATE_HTML = ROOT / "scripts" / "trends_template.html"
OUTPUT_HTML = ROOT / "trends.html"

WINDOWS = [("This Week", 7), ("This Month", 30), ("This Year", 365)]

ROW_TEMPLATE = Template(
    """
    <li class="row">
      <span class="details">
        <span class="name">$label</span>
        <span class="address">$compare</span>
      </span>
      <span class="price">$change</span>
    </li>"""
)


def closest_on_or_before(history, target_date):
    # history is sorted ascending by date, so the last match is the closest
    # day on or before target_date.
    candidates = [h for h in history if h["date"] <= target_date]
    return candidates[-1] if candidates else None


def compute_trends(history, today):
    current = history[-1]["price"]
    trends = []
    for label, days in WINDOWS:
        target_date = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
        past = closest_on_or_before(history, target_date)
        if past is None:
            trends.append({"label": label, "available": False})
            continue
        diff = current - past["price"]
        pct = (diff / past["price"]) * 100 if past["price"] else 0.0
        trends.append(
            {
                "label": label,
                "available": True,
                "compare_date": past["date"],
                "compare_price": past["price"],
                "diff": diff,
                "pct": pct,
            }
        )
    return trends


def format_change(t):
    if not t["available"]:
        return "not enough data yet"
    diff, pct = t["diff"], t["pct"]
    if abs(diff) < 0.005:
        return "flat"
    arrow = "▲" if diff > 0 else "▼"
    sign = "+" if diff > 0 else "-"
    return f"{arrow} {sign}${abs(diff):.2f} ({sign}{abs(pct):.1f}%)"


def render_html(current_price, trends, updated_at):
    rows_html = "".join(
        ROW_TEMPLATE.substitute(
            label=t["label"],
            compare=f"vs ${t['compare_price']:.2f} on {t['compare_date']}" if t["available"] else "collecting history",
            change=format_change(t),
        )
        for t in trends
    )
    template = Template(TEMPLATE_HTML.read_text())
    html = template.substitute(rows=rows_html, updated=updated_at, current=f"${current_price:.2f}")
    OUTPUT_HTML.write_text(html)


def main():
    if not HISTORY_JSON.exists():
        print("No history.json yet — run fetch_prices.py at least once first.", file=sys.stderr)
        sys.exit(1)

    history = json.loads(HISTORY_JSON.read_text())
    if not history:
        print("history.json is empty — nothing to compute trends from.", file=sys.stderr)
        sys.exit(1)

    today = history[-1]["date"]
    current_price = history[-1]["price"]
    trends = compute_trends(history, today)

    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %I:%M %p UTC")

    TRENDS_JSON.write_text(
        json.dumps({"updated": updated_at, "current_price": current_price, "trends": trends}, indent=2)
    )
    render_html(current_price, trends, updated_at)
    print("Wrote trends to trends.json and trends.html")


if __name__ == "__main__":
    main()
