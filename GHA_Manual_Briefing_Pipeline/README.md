# Manual GitHub Actions Pipeline

This folder contains a manual-run pipeline that you can trigger from GitHub Actions.

It is self-contained for deployment with only two folders:

- `.github/`
- `GHA_Manual_Briefing_Pipeline/`

## What It Does

1. Accepts `TE_COOKIES` input each run.
2. Crawls TradingEconomics calendar.
3. Runs macro news crawling and filtering.
4. Builds email with:
   - 3 x 6 calendar table
   - All filtered news by country/topic
   - Full Link/Source URL text for every article
5. Sends Gmail (optional toggle in workflow input).
6. Stores artifacts (`briefing_result.json`, latest calendar CSV, email preview HTML).

## Files

- `run_pipeline.py`: orchestrates full flow
- `send_full_email.py`: builds full email and sends Gmail
- `streamlit_app.py`: dashboard for local run
- `app/Calendar/scraper.py`: economic calendar crawler
- `app/Macro_News_Briefing/main.py`: news crawl and filtering pipeline
- `app/Macro_News_Briefing/api_and_rss/*`: news source/search/filter modules
- `requirements_macro.txt`: copied dependency lock from Macro_News_Briefing

## Local Run

```bash
python GHA_Manual_Briefing_Pipeline/run_pipeline.py --te-cookies "YOUR_COOKIE" --send-mail
```

Dry-run mail mode:

```bash
python GHA_Manual_Briefing_Pipeline/run_pipeline.py --te-cookies "YOUR_COOKIE" --dry-run-mail
```

Run dashboard locally:

```bash
streamlit run GHA_Manual_Briefing_Pipeline/streamlit_app.py
```
