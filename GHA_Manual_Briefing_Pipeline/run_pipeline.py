import argparse
import os
import subprocess
import sys
from pathlib import Path


PIPELINE_DIR = Path(__file__).resolve().parent
APP_DIR = PIPELINE_DIR / "app"
CALENDAR_DIR = APP_DIR / "Calendar"
NEWS_DIR = APP_DIR / "Macro_News_Briefing"
OUTPUT_DIR = PIPELINE_DIR / "output"


def prefetch_market_prices() -> None:
    from market_returns import MARKET_SECTIONS, load_market_prices

    tickers = tuple(ticker for items in MARKET_SECTIONS.values() for _, ticker in items)
    prices = load_market_prices(tickers, period="5y")
    if prices.empty:
        raise RuntimeError("Failed to fetch market prices from Yahoo Finance.")

    latest = prices.dropna(how="all").index.max()
    latest_text = str(latest.date()) if latest is not None else "unknown"
    print(f"[OK] Market prices updated: rows={len(prices)}, latest={latest_text}")


def validate_news_hour_window(expected_hours: int = 24) -> None:
    news_dir = str(NEWS_DIR)
    if news_dir not in sys.path:
        sys.path.insert(0, news_dir)

    import settings as news_settings

    actual = getattr(news_settings, "HOUR_WINDOW", None)
    if actual != expected_hours:
        raise RuntimeError(
            f"Expected Macro_News_Briefing/settings.py HOUR_WINDOW={expected_hours}, got {actual}."
        )

    print(f"[OK] News hour window check passed: HOUR_WINDOW={actual}")


def run_step(cmd: list[str], cwd: Path, env: dict | None = None) -> None:
    print(f"[RUN] {' '.join(cmd)}")
    completed = subprocess.run(cmd, cwd=str(cwd), check=False, env=env)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with code {completed.returncode}: {' '.join(cmd)}")


def ensure_outputs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run manual GitHub Actions briefing pipeline.")
    parser.add_argument("--te-cookies", default="", help="TradingEconomics cookie string")
    parser.add_argument("--send-mail", action="store_true", help="Send Gmail after crawling")
    parser.add_argument("--dry-run-mail", action="store_true", help="Build email HTML only")
    args = parser.parse_args()

    ensure_outputs()
    validate_news_hour_window(expected_hours=24)

    te_cookies = args.te_cookies.strip() or os.getenv("TE_COOKIES", "").strip()
    if not te_cookies:
        raise RuntimeError("Missing TE_COOKIES. Set GitHub secret TE_COOKIES or pass --te-cookies.")

    child_env = os.environ.copy()
    child_env["TE_COOKIES"] = te_cookies

    prefetch_market_prices()

    run_step([sys.executable, "scraper.py"], cwd=CALENDAR_DIR, env=child_env)
    run_step([sys.executable, "main.py"], cwd=NEWS_DIR)

    email_cmd = [
        sys.executable,
        str(PIPELINE_DIR / "send_full_email.py"),
        "--output-html",
        str(OUTPUT_DIR / "email_preview.html"),
    ]

    if args.send_mail:
        pass
    elif args.dry_run_mail:
        email_cmd.append("--dry-run")
    else:
        email_cmd.append("--dry-run")

    run_step(email_cmd, cwd=PIPELINE_DIR)

    print("[OK] Pipeline complete")
    print(f"[INFO] Email preview: {OUTPUT_DIR / 'email_preview.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
