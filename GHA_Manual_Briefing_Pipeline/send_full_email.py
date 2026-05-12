import argparse
import glob
import json
import os
import smtplib
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from html import escape
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv


PIPELINE_DIR = Path(__file__).resolve().parent
APP_DIR = PIPELINE_DIR / "app"
CALENDAR_DIR = APP_DIR / "Calendar"
NEWS_DIR = APP_DIR / "Macro_News_Briefing"

load_dotenv(dotenv_path=NEWS_DIR / ".env", override=True)
load_dotenv(dotenv_path=CALENDAR_DIR / ".env", override=True)

TOPIC_LABELS = {
    "US_monetary": "US Monetary",
    "US_fiscal": "US Fiscal",
    "US_politics": "US Politics",
    "CN_monetary": "China Monetary",
    "CN_fiscal": "China Fiscal",
    "CN_politics": "China Politics",
    "JP_monetary": "Japan Monetary",
    "JP_fiscal": "Japan Fiscal",
    "JP_politics": "Japan Politics",
    "KR_monetary": "Korea Monetary",
    "KR_fiscal": "Korea Fiscal",
    "KR_politics": "Korea Politics",
    "EU_monetary": "EU Monetary",
    "EU_politics": "EU Politics",
}


def _env(name: str) -> str:
    return str(os.getenv(name, "")).strip()


def _parse_recipients() -> list[str]:
    raw = _env("RECIPIENTS") or _env("RECEIPIENTS") or _env("RECEPIENTS")
    if not raw:
        return []
    normalized = raw.replace(";", ",").replace("\n", ",")
    return [x.strip() for x in normalized.split(",") if x.strip()]


def get_latest_calendar_csv() -> Path:
    files = glob.glob(str(CALENDAR_DIR / "economic_calendar_*.csv"))
    if not files:
        raise FileNotFoundError("No economic_calendar_*.csv found in Calendar directory")
    return Path(max(files, key=os.path.getmtime))


def get_briefing_json() -> Path:
    path = NEWS_DIR / "briefing_result.json"
    if not path.exists():
        raise FileNotFoundError("briefing_result.json not found. Run news pipeline first")
    return path


def parse_calendar_df() -> pd.DataFrame:
    df = pd.read_csv(get_latest_calendar_csv(), encoding="utf-8-sig")

    if "Country" in df.columns:
        countries = df["Country"].astype(str).str.lower().str.strip()
        df = df[countries.isin(["united states", "south korea"])]

    try:
        df["_dt"] = pd.to_datetime(df["Date"], errors="coerce", format="mixed")
    except TypeError:
        df["_dt"] = pd.to_datetime(df["Date"], errors="coerce")

    df = df.dropna(subset=["_dt"])
    df["_date"] = df["_dt"].dt.date
    df = df.sort_values("_dt")
    return df


def build_weeks(today: date) -> list[list[date]]:
    monday = today - timedelta(days=today.weekday() + 7)
    weeks = []
    for w in range(3):
        ws = monday + timedelta(weeks=w)
        weeks.append([ws + timedelta(days=d) for d in range(6)])
    return weeks


def _calendar_events_for_day(df: pd.DataFrame, d: date) -> list[dict]:
    rows = df[df["_date"] == d]
    out: list[dict] = []
    for _, r in rows.iterrows():
        out.append(
            {
                "country": str(r.get("Country", "")).strip(),
                "event": str(r.get("Event", "")).strip(),
                "actual": str(r.get("Actual", "")).strip() or "-",
                "forecast": str(r.get("Forecast", "")).strip() or "-",
                "previous": str(r.get("Previous", "")).strip() or "-",
            }
        )
    return out


def render_calendar_html(df: pd.DataFrame) -> str:
    parts = [
        '<h2 style="margin:0 0 8px 0;">Economic Calendar (3 x 6)</h2>',
        '<table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse; width:100%; table-layout:fixed;">',
    ]

    for week in build_weeks(date.today()):
        parts.append("<tr>")
        for d in week:
            parts.append('<td valign="top" style="width:16.6%; font-size:12px;">')
            parts.append(f"<b>{escape(d.strftime('%a %m/%d'))}</b><br>")
            events = _calendar_events_for_day(df, d)
            if not events:
                parts.append("-<br>")
            else:
                for e in events:
                    tag = "US" if e["country"].lower().startswith("united") else "KR"
                    parts.append(f"<b>{escape(tag)}</b> {escape(e['event'])}<br>")
                    parts.append(
                        f"A:{escape(e['actual'])} F:{escape(e['forecast'])} P:{escape(e['previous'])}<br><br>"
                    )
            parts.append("</td>")
        parts.append("</tr>")

    parts.append("</table>")
    return "\n".join(parts)


def _article_sort_key(article: dict) -> str:
    return str(article.get("date", ""))


def render_news_html() -> str:
    payload = json.loads(get_briefing_json().read_text(encoding="utf-8"))
    country_view = payload.get("country_view", {})

    parts = [
        '<h2 style="margin:22px 0 8px 0;">Foreign Companies</h2>',
        '<hr style="margin:8px 0 14px 0;">',
    ]

    if not isinstance(country_view, dict) or not country_view:
        parts.append("<p>No macro news available.</p>")
        return "\n".join(parts)

    for country, topics in country_view.items():
        if not isinstance(topics, dict):
            continue

        parts.append(f"<h2>{escape(str(country))}</h2>")
        parts.append("<hr>")

        for topic, articles in topics.items():
            if not isinstance(articles, list) or not articles:
                continue

            usable = [a for a in articles if isinstance(a, dict) and a.get("usable") is True]
            usable.sort(key=_article_sort_key, reverse=True)
            if not usable:
                continue

            topic_label = TOPIC_LABELS.get(str(topic), str(topic))
            parts.append(f"<h3>{escape(topic_label)}</h3>")

            for article in usable:
                title = escape(str(article.get("title", "")).strip())
                dt = escape(str(article.get("date", "")).strip())
                summary = str(article.get("AI_summary", "")).strip()
                if summary in ("", "Didn't run yet", "REPLACE_REAL_HERE", "SUMMARY ERROR"):
                    summary = str(article.get("lede", "")).strip()
                summary = escape(summary)

                source = str(article.get("link", "")).strip()
                source_text = escape(source)

                parts.append("<p>")
                parts.append(f"<b>Title:</b> {title}<br>")
                if dt:
                    parts.append(f"<b>Date:</b> {dt}<br>")
                if summary:
                    parts.append(f"<b>AI summary:</b> {summary}<br>")
                if source.startswith(("http://", "https://")):
                    safe_url = escape(source, quote=True)
                    parts.append(f'<b>Link/Source:</b> <a href="{safe_url}">{source_text}</a><br>')
                else:
                    parts.append("<b>Link/Source:</b> -<br>")
                parts.append("</p>")

    return "\n".join(parts)


def build_email_html() -> str:
    cal_df = parse_calendar_df()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""
<html>
  <body style="font-family: Arial, sans-serif; line-height: 1.5;">
    <h1 style="margin:0 0 6px 0;">Macro Briefing</h1>
    <p style="margin:0 0 12px 0; color:#555;">Generated at {escape(now)}</p>
    {render_calendar_html(cal_df)}
    {render_news_html()}
  </body>
</html>
""".strip()


def send_gmail(subject: str, html_body: str) -> None:
    sender = _env("SENDER_MAIL")
    password = _env("GOOGLE_PASSWORD")
    recipients = _parse_recipients()

    if not sender or not password or not recipients:
        raise RuntimeError("Missing SENDER_MAIL / GOOGLE_PASSWORD / RECIPIENTS")

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, recipients, msg.as_string())


def main() -> int:
    parser = argparse.ArgumentParser(description="Send full macro briefing email")
    parser.add_argument("--dry-run", action="store_true", help="Build HTML only")
    parser.add_argument("--output-html", default="", help="Optional output path for HTML preview")
    args = parser.parse_args()

    subject = f"Macro Briefing {date.today().strftime('%Y-%m-%d')}"
    html_body = build_email_html()

    if args.output_html:
        out_path = Path(args.output_html)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html_body, encoding="utf-8")
        print(f"[OK] Wrote HTML preview: {out_path}")

    if args.dry_run:
        print("[DRY RUN] Email not sent")
        print(f"[DRY RUN] Subject: {subject}")
        print("[DRY RUN] Preview length:", len(html_body))
        return 0

    send_gmail(subject, html_body)
    print("[OK] Email sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
