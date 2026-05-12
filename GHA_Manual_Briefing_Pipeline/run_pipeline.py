import argparse
import subprocess
import sys
from pathlib import Path


PIPELINE_DIR = Path(__file__).resolve().parent
APP_DIR = PIPELINE_DIR / "app"
CALENDAR_DIR = APP_DIR / "Calendar"
NEWS_DIR = APP_DIR / "Macro_News_Briefing"
OUTPUT_DIR = PIPELINE_DIR / "output"


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


def _read_env_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8-sig").splitlines()


def _write_env_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines).rstrip("\n") + "\n"
    path.write_text(text, encoding="utf-8")


def upsert_te_cookies(env_path: Path, te_cookies: str) -> None:
    if not te_cookies.strip():
        raise ValueError("TE_COOKIES is empty. Provide a valid TradingEconomics cookie string.")

    lines = _read_env_lines(env_path)
    replaced = False
    out: list[str] = []

    for line in lines:
        if line.strip().startswith("TE_COOKIES="):
            out.append(f"TE_COOKIES={te_cookies}")
            replaced = True
        else:
            out.append(line)

    if not replaced:
        out.append(f"TE_COOKIES={te_cookies}")

    _write_env_lines(env_path, out)


def run_step(cmd: list[str], cwd: Path) -> None:
    print(f"[RUN] {' '.join(cmd)}")
    completed = subprocess.run(cmd, cwd=str(cwd), check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with code {completed.returncode}: {' '.join(cmd)}")


def ensure_outputs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run manual GitHub Actions briefing pipeline.")
    parser.add_argument("--te-cookies", required=True, help="TradingEconomics cookie string")
    parser.add_argument("--send-mail", action="store_true", help="Send Gmail after crawling")
    parser.add_argument("--dry-run-mail", action="store_true", help="Build email HTML only")
    args = parser.parse_args()

    ensure_outputs()
    validate_news_hour_window(expected_hours=24)

    calendar_env = CALENDAR_DIR / ".env"
    upsert_te_cookies(calendar_env, args.te_cookies)
    print(f"[OK] Updated TE_COOKIES in {calendar_env}")

    run_step([sys.executable, "scraper.py"], cwd=CALENDAR_DIR)
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
