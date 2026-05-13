import glob
import json
from datetime import date, timedelta
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


PIPELINE_DIR = Path(__file__).resolve().parent
APP_DIR = PIPELINE_DIR / "app"
CALENDAR_DIR = APP_DIR / "Calendar"
NEWS_DIR = APP_DIR / "Macro_News_Briefing"


def get_csv_path() -> Path | None:
    files = sorted(glob.glob(str(CALENDAR_DIR / "economic_calendar_*.csv")))
    return Path(files[-1]) if files else None


def get_news_path() -> Path | None:
    path = NEWS_DIR / "briefing_result.json"
    return path if path.exists() else None


@st.cache_data
def load_calendar(path: str, mtime: float) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    if "Country" in df.columns:
        countries = df["Country"].astype(str).str.lower().str.strip()
        df = df[countries.isin(["united states", "south korea"])]

    try:
        df["_dt"] = pd.to_datetime(df["Date"], errors="coerce", format="mixed")
    except TypeError:
        df["_dt"] = pd.to_datetime(df["Date"], errors="coerce")

    df = df.dropna(subset=["_dt"])
    df["_date"] = df["_dt"].dt.date
    return df.sort_values("_dt")


@st.cache_data
def load_news(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_weeks(today: date) -> list[list[date]]:
    monday = today - timedelta(days=today.weekday())
    weeks: list[list[date]] = []
    for w in range(2):
        ws = monday + timedelta(weeks=w)
        weeks.append([ws + timedelta(days=d) for d in range(6)])
    return weeks


def _clean_metric_value(value: object) -> str:
    raw = str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "nat"}:
        return "-"
    # Source data occasionally includes multiline whitespace and a trailing trademark symbol.
    normalized = " ".join(raw.replace("®", "").split())
    return normalized or "-"


def _normalize_country_key(country: object) -> str:
    value = str(country).strip().lower()
    aliases = {
        "united states": "united states",
        "usa": "united states",
        "us": "united states",
        "u.s.": "united states",
        "south korea": "south korea",
        "korea": "south korea",
        "republic of korea": "south korea",
        "kr": "south korea",
    }
    return aliases.get(value, value)


def _clean_event_title(event: object, country_key: str) -> str:
    title = str(event).strip()
    if country_key == "united states":
        prefixes = ("US ", "U.S. ", "[US] ", "(US) ")
    elif country_key == "south korea":
        prefixes = ("KR ", "KOR ", "[KR] ", "(KR) ")
    else:
        return title

    for prefix in prefixes:
        if title.startswith(prefix):
            return title[len(prefix):].strip()
    return title


def render_calendar(df: pd.DataFrame) -> None:
    st.subheader("Economic Calendar (2 x 6)")
    country_order = {"united states": 0, "south korea": 1}
    country_flag = {"united states": "🇺🇸", "south korea": "🇰🇷"}

    parts: list[str] = [
        """
        <style>
        body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
        .cal-wrap { background: #ffffff; color: #111827; border-radius: 8px; }
        .cal-table { border-collapse: collapse; width: 100%; table-layout: fixed; font-size: 12px; }
        .cal-table td { border: 1px solid #d1d5db; vertical-align: top; padding: 8px; background: #ffffff; }
        .day-header { display: block; font-weight: 700; font-size: 20px; margin-bottom: 8px; color: #111827; }
        .event-card { border: 1px solid #e5e7eb; border-radius: 8px; padding: 8px; margin: 6px 0; background: #f9fafb; }
        .event-title { line-height: 1.25; min-height: 2.5em; max-height: 2.5em; overflow: hidden; font-weight: 700; margin-bottom: 6px; color: #1f2937; }
        .afp-row { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #374151; }
        .afp-a { color: #0f766e; font-weight: 700; }
        .afp-f { color: #1d4ed8; font-weight: 700; }
        .afp-p { color: #b45309; font-weight: 700; }
        .empty-day { color: #6b7280; }
        </style>
        <div class="cal-wrap">
        <table class="cal-table">
        """
    ]

    for week in build_weeks(date.today()):
        parts.append("<tr>")
        for d in week:
            rows = df[df["_date"] == d]
            if not rows.empty and "Country" in rows.columns:
                rows = rows.assign(
                    _country_key=rows["Country"].apply(_normalize_country_key),
                ).assign(
                    _country_order=lambda x: x["_country_key"].map(country_order).fillna(99)
                ).sort_values(["_country_order", "_dt"])
            parts.append('<td style="width:16.6%; min-height:220px;">')
            parts.append(f'<span class="day-header">{escape(d.strftime("%a %m/%d"))}</span>')
            if rows.empty:
                parts.append('<div class="empty-day">-</div>')
            else:
                for _, r in rows.iterrows():
                    ckey = _normalize_country_key(r.get("Country", ""))
                    flag = country_flag.get(ckey, "🏳️")
                    event = escape(_clean_event_title(r.get("Event", ""), ckey))
                    actual = escape(_clean_metric_value(r.get("Actual", "")))
                    forecast = escape(_clean_metric_value(r.get("Forecast", "")))
                    previous = escape(_clean_metric_value(r.get("Previous", "")))
                    parts.append('<div class="event-card">')
                    parts.append(f'<div class="event-title"><strong>{flag} {event}</strong></div>')
                    parts.append(
                        '<div class="afp-row">'
                        f'<span class="afp-a">A</span>: {actual} &nbsp; '
                        f'<span class="afp-f">F</span>: {forecast} &nbsp; '
                        f'<span class="afp-p">P</span>: {previous}'
                        '</div>'
                    )
                    parts.append("</div>")
            parts.append("</td>")
        parts.append("</tr>")

    parts.extend(["</table>", "</div>"])
    components.html("".join(parts), height=980, scrolling=True)


def render_news(payload: dict) -> None:
    st.subheader("Country")
    country_view = payload.get("country_view", {})
    if not isinstance(country_view, dict) or not country_view:
        st.info("No news data")
        return

    country_order = [
        ("United States", "🇺🇸"),
        ("China", "🇨🇳"),
        ("Japan", "🇯🇵"),
        ("Korea", "🇰🇷"),
        ("EU", "🇪🇺"),
    ]

    for country_name, flag in country_order:
        topics = country_view.get(country_name)
        if not isinstance(topics, dict):
            continue

        merged: list[dict] = []
        for _, articles in topics.items():
            if not isinstance(articles, list):
                continue
            merged.extend([a for a in articles if isinstance(a, dict) and a.get("usable") is True])

        if not merged:
            continue

        merged.sort(
            key=lambda x: (
                1 if x.get("is_big6_priority") else 0,
                str(x.get("date", "")),
            ),
            reverse=True,
        )
        st.markdown(
            f'<div style="font-size:32px; font-weight:800; line-height:1.2;">{flag} {country_name}</div>',
            unsafe_allow_html=True,
        )
        st.markdown("---")
        for article in merged:
            title = str(article.get("title", "")).strip()
            dt = str(article.get("date", "")).strip()
            summary = str(article.get("AI_summary", "")).strip()
            if summary in ("", "Didn't run yet", "REPLACE_REAL_HERE", "SUMMARY ERROR"):
                summary = str(article.get("lede", "")).strip()
            link = str(article.get("link", "")).strip()

            st.markdown(f"**News Title:** {title or '-'}")
            st.markdown(f"**DATE:** {dt or '-'}")
            st.markdown(f"**AI SUMMARY:** {summary or '-'}")
            st.markdown(f"**LINK:** {link or '-'}")
            st.write("")


def main() -> None:
    st.set_page_config(page_title="Manual Briefing Dashboard", page_icon="🌎", layout="wide")
    st.title("Manual Briefing Dashboard")

    csv_path = get_csv_path()
    news_path = get_news_path()

    if not csv_path:
        st.error("No calendar CSV found. Run pipeline first.")
        return
    if not news_path:
        st.error("No briefing_result.json found. Run pipeline first.")
        return

    cal_df = load_calendar(str(csv_path), csv_path.stat().st_mtime)
    news_payload = load_news(str(news_path), news_path.stat().st_mtime)

    tab1, tab2 = st.tabs(["Calendar", "News"])
    with tab1:
        render_calendar(cal_df)
    with tab2:
        render_news(news_payload)


if __name__ == "__main__":
    main()
