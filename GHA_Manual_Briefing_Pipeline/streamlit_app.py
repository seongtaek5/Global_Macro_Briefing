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
    monday = today - timedelta(days=today.weekday() + 7)
    weeks: list[list[date]] = []
    for w in range(3):
        ws = monday + timedelta(weeks=w)
        weeks.append([ws + timedelta(days=d) for d in range(6)])
    return weeks


def render_calendar(df: pd.DataFrame) -> None:
    st.subheader("Economic Calendar (3 x 6)")

    for week in build_weeks(date.today()):
        cols = st.columns(6)
        for idx, d in enumerate(week):
            rows = df[df["_date"] == d]
            with cols[idx]:
                st.markdown(f"**{d.strftime('%a %m/%d')}**")
                if rows.empty:
                    st.caption("-")
                else:
                    for _, r in rows.iterrows():
                        country = str(r.get("Country", "")).strip()
                        event = str(r.get("Event", "")).strip()
                        actual = str(r.get("Actual", "")).strip() or "-"
                        forecast = str(r.get("Forecast", "")).strip() or "-"
                        previous = str(r.get("Previous", "")).strip() or "-"
                        tag = "US" if country.lower().startswith("united") else "KR"
                        st.write(f"{tag} {event}")
                        st.caption(f"A:{actual} / F:{forecast} / P:{previous}")


def render_news(payload: dict) -> None:
    st.subheader("News by Country")
    country_view = payload.get("country_view", {})
    if not isinstance(country_view, dict) or not country_view:
        st.info("No news data")
        return

    for country, topics in country_view.items():
        st.markdown(f"### {country}")
        if not isinstance(topics, dict):
            continue
        for topic, articles in topics.items():
            usable = [a for a in articles if isinstance(a, dict) and a.get("usable") is True]
            if not usable:
                continue
            st.markdown(f"#### {topic}")
            for article in usable:
                title = str(article.get("title", "")).strip()
                dt = str(article.get("date", "")).strip()
                summary = str(article.get("AI_summary", "")).strip() or str(article.get("lede", "")).strip()
                link = str(article.get("link", "")).strip()

                st.markdown(f"- **Title:** {title}")
                if dt:
                    st.caption(f"Date: {dt}")
                if summary:
                    st.write(summary)
                if link:
                    st.markdown(f"Link/Source: {link}")


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
