import glob
import json
import re
import base64
from datetime import date, timedelta
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from market_returns import (
    MARKET_SECTIONS,
    RETURN_WINDOWS,
    get_market_returns,
    get_ts_mom_zscore_heatmap,
)


PIPELINE_DIR = Path(__file__).resolve().parent
APP_DIR = PIPELINE_DIR / "app"
CALENDAR_DIR = APP_DIR / "Calendar"
NEWS_DIR = APP_DIR / "Macro_News_Briefing"
FLAGS_DIR = PIPELINE_DIR / "assets" / "flags"


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


@st.cache_data
def _load_flag_data_uri(path: str, mtime: float) -> str:
    raw = Path(path).read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _country_flag_html(country_key: str, size_px: int = 16) -> str:
    file_map = {
        "united states": "us.png",
        "south korea": "kr.png",
        "china": "cn.png",
        "japan": "jp.png",
        "eu": "eu.png",
    }
    file_name = file_map.get(country_key)
    if not file_name:
        return ""

    path = FLAGS_DIR / file_name
    if not path.exists():
        return ""

    uri = _load_flag_data_uri(str(path), path.stat().st_mtime)
    return (
        f'<img src="{uri}" alt="{escape(country_key)} flag" '
        f'style="width:{size_px}px; height:{size_px}px; vertical-align:middle; margin-right:6px;"/>'
    )


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
    key_aliases: dict[str, tuple[str, ...]] = {
        "united states": ("us", "u.s", "u.s.", "usa"),
        "south korea": ("kr", "kor", "korea", "south korea", "republic of korea"),
        "china": ("cn", "chn", "china", "prc"),
        "japan": ("jp", "jpn", "japan"),
        "eu": ("eu", "europe", "european union"),
    }
    aliases = key_aliases.get(country_key, ())
    return _strip_country_prefix(title, aliases)


def _strip_country_prefix(text: str, aliases: tuple[str, ...]) -> str:
    title = text.strip()
    if not title or not aliases:
        return title

    escaped = [re.escape(a) for a in aliases if a]
    if not escaped:
        return title

    # Remove country prefixes only at the beginning, e.g. "US: ", "[KR] ", "(CN)-".
    token = "(?:" + "|".join(escaped) + ")"
    pattern = rf"^\s*(?:\[\s*{token}\s*\]|\(\s*{token}\s*\)|{token})\s*[:\-\|/]?\s*"
    cleaned = re.sub(pattern, "", title, flags=re.IGNORECASE)
    return cleaned.strip() or title


def render_calendar(df: pd.DataFrame) -> None:
    st.subheader("Economic Calendar (THIS WEEK & NEXT WEEK)")
    country_order = {"united states": 0, "south korea": 1}

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
                    flag_html = _country_flag_html(ckey, size_px=15)
                    event = escape(_clean_event_title(r.get("Event", ""), ckey))
                    actual = escape(_clean_metric_value(r.get("Actual", "")))
                    forecast = escape(_clean_metric_value(r.get("Forecast", "")))
                    previous = escape(_clean_metric_value(r.get("Previous", "")))
                    parts.append('<div class="event-card">')
                    parts.append(f'<div class="event-title"><strong>{flag_html}{event}</strong></div>')
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

    country_order = ["United States", "China", "Japan", "Korea", "EU"]

    for country_name in country_order:
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
        country_key = _normalize_country_key(country_name)
        flag_html = _country_flag_html(country_key, size_px=24)
        st.markdown(
            f'<div style="font-size:32px; font-weight:800; line-height:1.2;">{flag_html}{country_name}</div>',
            unsafe_allow_html=True,
        )
        st.markdown("---")
        for article in merged:
            title = _strip_country_prefix(
                str(article.get("title", "")).strip(),
                {
                    "United States": ("us", "u.s", "u.s.", "usa"),
                    "Korea": ("kr", "kor", "korea", "south korea", "republic of korea"),
                    "China": ("cn", "chn", "china", "prc"),
                    "Japan": ("jp", "jpn", "japan"),
                    "EU": ("eu", "europe", "european union"),
                }.get(country_name, ()),
            )
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


def load_market_returns() -> dict[str, pd.DataFrame]:
    return get_market_returns(MARKET_SECTIONS, RETURN_WINDOWS)


def load_ts_mom_heatmaps() -> dict[str, pd.DataFrame]:
    return get_ts_mom_zscore_heatmap(MARKET_SECTIONS, lookback_months=24)


def _ret_cell_html(val: float | None) -> str:
    if val is None:
        return '<td class="ret-cell ret-na">-</td>'

    # Color scale is clipped to [-30, +30], while display keeps the actual return.
    clipped = max(-30.0, min(30.0, float(val)))
    neg = "#7a1f3d"
    neu = "#f4f6fa"
    pos = "#0c5a55"

    if clipped < 0:
        bg = _mix_hex(neg, neu, (clipped + 30.0) / 30.0)
    else:
        bg = _mix_hex(neu, pos, clipped / 30.0)

    txt = "#ffffff" if abs(clipped) >= 17.5 else "#24324a"
    return (
        f'<td class="ret-cell" style="background:{bg}; color:{txt};">'
        f"{float(val):+.2f}%"
        "</td>"
    )


def _mix_hex(c1: str, c2: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _zscore_cell_html(val: float | None) -> str:
    if val is None:
        return '<td class="z-cell z-na">-</td>'

    clipped = max(-3.0, min(3.0, float(val)))
    neg = "#7a1f3d"
    neu = "#f4f6fa"
    pos = "#0c5a55"

    if clipped < 0:
        bg = _mix_hex(neg, neu, (clipped + 3.0) / 3.0)
    else:
        bg = _mix_hex(neu, pos, clipped / 3.0)

    txt = "#ffffff" if abs(clipped) >= 1.75 else "#24324a"
    return (
        f'<td class="z-cell" style="background:{bg}; color:{txt};">'
        f"{clipped:+.2f}"
        "</td>"
    )


def render_markets() -> None:
    st.subheader("Market Performance")
    st.caption("Yahoo Finance 기준 | 1D, 1M, 3M, 6M, 12M, YTD 수익률 | Returns 색상 스케일: -30% ~ +30% (범위 밖은 경계색 고정)")

    section_returns = load_market_returns()
    section_heatmaps = load_ts_mom_heatmaps()
    if not section_returns:
        st.error("Yahoo Finance 데이터를 불러오지 못했습니다.")
        return

    has_any_value = False
    for table in section_returns.values():
        for window in RETURN_WINDOWS:
            if window in table.columns and table[window].notna().any():
                has_any_value = True
                break
        if has_any_value:
            break
    if not has_any_value:
        st.error("Yahoo Finance 데이터를 불러오지 못했습니다.")
        return

    st.markdown(
        """
        <style>
        .perf-section {
            margin: 10px 0 24px 0;
            border: 1px solid #d9deea;
            border-radius: 12px;
            overflow: hidden;
            background: linear-gradient(180deg, #ffffff 0%, #f7f9fc 100%);
        }
        .perf-title {
            font-size: 18px;
            font-weight: 800;
            letter-spacing: 0.04em;
            color: #27324a;
            padding: 12px 14px;
            background: #eef2f9;
            border-bottom: 1px solid #d9deea;
        }
        .perf-table {
            width: 100%;
            border-collapse: collapse;
            table-layout: fixed;
            font-size: 14px;
        }
        .perf-table th,
        .perf-table td {
            border-bottom: 1px solid #e5e9f2;
            padding: 10px 12px;
            text-align: center;
            color: #223047;
        }
        .perf-table th:first-child,
        .perf-table td:first-child {
            text-align: left;
            width: 210px;
            font-weight: 700;
        }
        .ret-cell {
            font-weight: 700;
            border-radius: 8px;
        }
        .ret-pos {
            color: #0f6a62;
            background: #e8f5f2;
        }
        .ret-neg {
            color: #a23a57;
            background: #fbeef2;
        }
        .ret-flat {
            color: #5f6b7d;
            background: #f1f4f8;
        }
        .ret-na {
            color: #94a3b8;
            background: #f8fafc;
        }
        .hm-wrap {
            margin-top: 8px;
            overflow-x: auto;
            border-top: 1px solid #e5e9f2;
            padding-top: 10px;
        }
        .hm-title {
            font-size: 13px;
            font-weight: 800;
            color: #3a4760;
            margin: 0 0 8px 2px;
            letter-spacing: 0.02em;
        }
        .hm-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 3px;
            table-layout: fixed;
            font-size: 11px;
        }
        .hm-table th,
        .hm-table td {
            text-align: center;
            padding: 6px 4px;
            border-radius: 6px;
        }
        .hm-table th:first-child,
        .hm-table td:first-child {
            text-align: left;
            width: 210px;
            font-weight: 700;
            color: #223047;
            background: #eef2f9;
            border-radius: 8px;
            padding-left: 10px;
        }
        .hm-table thead th {
            background: #edf1f8;
            color: #4b5a73;
            font-weight: 700;
        }
        .z-cell {
            font-weight: 700;
        }
        .z-na {
            background: #f8fafc;
            color: #94a3b8;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    for section_name, table in section_returns.items():
        rows: list[str] = []
        for _, r in table.iterrows():
            display_name = str(r.get("Instrument", "")).strip()
            row = [f"<tr><td>{escape(display_name)}</td>"]
            for w in RETURN_WINDOWS:
                val = r.get(w)
                row.append(_ret_cell_html(float(val) if pd.notna(val) else None))
            row.append("</tr>")
            rows.append("".join(row))

        ret_head = "".join([f"<th>{escape(w)}</th>" for w in RETURN_WINDOWS])

        html = [
            '<div class="perf-section">',
            f'<div class="perf-title">{escape(section_name)}</div>',
            '<table class="perf-table">',
            f"<thead><tr><th>Instrument</th>{ret_head}</tr></thead>",
            "<tbody>",
            *rows,
            "</tbody></table>",
        ]

        hm_table = section_heatmaps.get(section_name, pd.DataFrame())
        if not hm_table.empty:
            hm_cols = [c for c in hm_table.columns if c not in {"Instrument", "Ticker"}]
            hm_rows: list[str] = []
            for _, hr in hm_table.iterrows():
                name = str(hr.get("Instrument", "")).strip()
                row = [f"<tr><td>{escape(name)}</td>"]
                for col in hm_cols:
                    val = hr.get(col)
                    row.append(_zscore_cell_html(float(val) if pd.notna(val) else None))
                row.append("</tr>")
                hm_rows.append("".join(row))

            hm_head = "".join([f"<th>{escape(str(c))}</th>" for c in hm_cols])
            html.extend(
                [
                    '<div class="hm-wrap">',
                    '<div class="hm-title">TS MOM Z-SCORE HEATMAP (1Y MOM, 1Y ROLLING Z | RANGE: -3 TO +3)</div>',
                    '<table class="hm-table">',
                    f"<thead><tr><th>Instrument</th>{hm_head}</tr></thead>",
                    "<tbody>",
                    *hm_rows,
                    "</tbody></table></div>",
                ]
            )

        html.append("</div>")
        st.markdown("".join(html), unsafe_allow_html=True)


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

    tab1, tab2, tab3 = st.tabs(["Calendar", "News", "Markets"])
    with tab1:
        render_calendar(cal_df)
    with tab2:
        render_news(news_payload)
    with tab3:
        render_markets()


if __name__ == "__main__":
    main()
