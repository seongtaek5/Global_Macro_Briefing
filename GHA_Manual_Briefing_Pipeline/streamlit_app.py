import glob
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st


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


def render_calendar(df: pd.DataFrame) -> None:
    st.subheader("Economic Calendar (2 x 6)")

    st.markdown(
        """
        <style>
        .day-cell {
            border: 1px solid #d6dbe3;
            border-radius: 10px;
            padding: 10px;
            min-height: 220px;
            background: #ffffff;
        }
        .day-header {
            font-weight: 700;
            margin-bottom: 8px;
            font-size: 14px;
        }
        .event-card {
            border: 1px solid #eef1f5;
            border-radius: 8px;
            padding: 8px;
            margin-bottom: 8px;
            background: #fbfcfe;
        }
        .event-title {
            line-height: 1.25;
            min-height: 2.5em;
            max-height: 2.5em;
            overflow: hidden;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            font-weight: 700;
            margin-bottom: 6px;
        }
        .afp-row {
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            font-size: 12px;
            line-height: 1.2;
        }
        .afp-a { color: #0f766e; font-weight: 700; }
        .afp-f { color: #1d4ed8; font-weight: 700; }
        .afp-p { color: #b45309; font-weight: 700; }
        .empty-day {
            color: #94a3b8;
            font-size: 12px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    country_order = {"united states": 0, "south korea": 1}
    country_flag = {"united states": "🇺🇸", "south korea": "🇰🇷"}

    for week in build_weeks(date.today()):
        cols = st.columns(6)
        for idx, d in enumerate(week):
            rows = df[df["_date"] == d]
            if not rows.empty and "Country" in rows.columns:
                rows = rows.assign(
                    _country_order=rows["Country"].astype(str).str.lower().map(country_order).fillna(99)
                ).sort_values(["_country_order", "_dt"])
            with cols[idx]:
                html_parts = [f'<div class="day-cell"><div class="day-header">{d.strftime("%a %m/%d")}</div>']
                if rows.empty:
                    html_parts.append('<div class="empty-day">-</div>')
                else:
                    for _, r in rows.iterrows():
                        country = str(r.get("Country", "")).strip()
                        event = str(r.get("Event", "")).strip()
                        actual = str(r.get("Actual", "")).strip() or "-"
                        forecast = str(r.get("Forecast", "")).strip() or "-"
                        previous = str(r.get("Previous", "")).strip() or "-"
                        ckey = country.lower()
                        flag = country_flag.get(ckey, "🏳️")
                        html_parts.append('<div class="event-card">')
                        html_parts.append(f'<div class="event-title"><strong>{flag} {event}</strong></div>')
                        html_parts.append(
                            '<div class="afp-row">'
                            f'<span class="afp-a">A</span>: {actual} &nbsp; '
                            f'<span class="afp-f">F</span>: {forecast} &nbsp; '
                            f'<span class="afp-p">P</span>: {previous}'
                            '</div>'
                        )
                        html_parts.append("</div>")
                html_parts.append("</div>")
                st.markdown("".join(html_parts), unsafe_allow_html=True)


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

        merged.sort(key=lambda x: str(x.get("date", "")), reverse=True)
        st.markdown(f"### {flag} {country_name}")
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
            st.markdown("---")


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
