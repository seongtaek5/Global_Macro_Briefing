# 1. pip install playwright pandas python-dotenv beautifulsoup4 lxml
# 2. python -m playwright install chromium
# 3. tradingeconomics.com 로그인 후
#    F12 Console → copy(document.cookie) → .env의 TE_COOKIES에 붙여넣기
# 4. python scraper.py

import os
import sys
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"), override=True, encoding="utf-8-sig")

# ── 상수 ──────────────────────────────────────────────────────────────────────

COUNTRIES = ["united states", "south korea"]
TARGET_COUNTRIES = {c.lower().strip() for c in COUNTRIES}

# ── 쿠키 파싱 ─────────────────────────────────────────────────────────────────

def parse_cookies(raw: str) -> list[dict]:
    """쿠키 문자열 → Playwright add_cookies() 형식 리스트."""
    result = []
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        idx = part.find("=")
        name = part[:idx].strip() if idx != -1 else part
        value = part[idx + 1:].strip() if idx != -1 else ""
        if not name:
            continue
        result.append({
            "name": name, "value": value,
            "domain": ".tradingeconomics.com", "path": "/",
        })
    return result

# ── HTML 파싱 ─────────────────────────────────────────────────────────────────

def _clean(val: str) -> str:
    return val.strip() if val else ""

def parse_calendar_html(html: str, date_from: str, date_to: str) -> list[dict]:
    """
    TE 캘린더 HTML → 이벤트 dict 리스트.
    구조:
      thead: <th colspan="3">Monday May 11 2026</th>  ← 날짜 헤더
      tbody > tr[data-country][data-event]: 이벤트 행 (data-* 속성 활용)
        td[0]: 시간 | td[4]: 이벤트명 | td[5]: Actual | td[6]: Previous
        td[7]: Consensus | td[8]: Forecast
    """
    from datetime import datetime as dt_cls
    soup = BeautifulSoup(html, "lxml")
    records: list[dict] = []
    current_date = ""

    # 모든 thead / tr[data-country] 를 문서 순서대로 순회
    for elem in soup.find_all(["thead", "tr"]):
        # ── 날짜 헤더 추출 ──
        if elem.name == "thead":
            th = elem.find("th", attrs={"colspan": True})
            if th:
                date_text = th.get_text(strip=True)
                try:
                    current_date = dt_cls.strptime(date_text, "%A %B %d %Y").strftime("%Y-%m-%d")
                except ValueError:
                    pass
            continue

        # ── 이벤트 행 (data-country 속성이 있는 tr) ──
        if not elem.get("data-country"):
            continue

        country  = elem.get("data-country", "").lower().strip()
        if country not in TARGET_COUNTRIES:
            continue
        category = elem.get("data-category", "")
        symbol   = elem.get("data-symbol", "")

        # recursive=False: 직접 자식 td만 (nested flag table 제외)
        # 컬럼 순서: [0]시간 | [1]국기+코드 | [2]이벤트명 | [3]Actual | [4]Previous | [5]Consensus | [6]Forecast
        tds = elem.find_all("td", recursive=False)
        if len(tds) < 3:
            continue

        time_text  = _clean(tds[0].get_text(separator=" "))
        event_name = " ".join(_clean(tds[2].get_text(separator=" ")).split()) if len(tds) > 2 else ""
        actual     = _clean(tds[3].get_text(separator=" ")) if len(tds) > 3 else ""
        previous   = _clean(tds[4].get_text(separator=" ")) if len(tds) > 4 else ""
        consensus  = _clean(tds[5].get_text(separator=" ")) if len(tds) > 5 else ""
        forecast   = _clean(tds[6].get_text(separator=" ")) if len(tds) > 6 else ""

        # 이벤트명 없으면 data-event 속성으로 보완
        if not event_name:
            event_name = elem.get("data-event", "").replace("-", " ").title()

        if not event_name:
            continue

        records.append({
            "Date":      f"{current_date} {time_text}".strip(),
            "Country":   country,
            "Event":     event_name,
            "Category":  category,
            "Actual":    actual,
            "Previous":  previous,
            "Forecast":  forecast if forecast else consensus,
            "Symbol":    symbol,
            "Importance": 3,
        })

    return records

# ── 메인 수집 ─────────────────────────────────────────────────────────────────

def fetch_calendar(date_from: str, date_to: str) -> list[dict]:
    """Playwright로 TE 캘린더를 렌더링 후 DOM에서 데이터 추출."""
    raw_cookies = os.getenv("TE_COOKIES", "").strip()
    if not raw_cookies:
        raise ValueError(
            "TE_COOKIES가 설정되지 않았습니다.\n"
            "tradingeconomics.com 로그인 후 F12 Console에서\n"
            "copy(document.cookie) 실행 → .env의 TE_COOKIES에 붙여넣기"
        )

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        raise RuntimeError(
            "Playwright가 설치되지 않았습니다.\n"
            "pip install playwright\n"
            "python -m playwright install chromium"
        )

    cookies = parse_cookies(raw_cookies)
    calendar_url = (
        f"https://tradingeconomics.com/calendar"
        f"?d1={date_from}&d2={date_to}&importance=2"   # importance=2: 별2개+별3개 모두 포함
    )

    cal_html = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        context.add_cookies(cookies)
        # importance 쿠키를 2로 오버라이드 (2+3성 이벤트 모두 표시)
        context.add_cookies([{
            "name": "calendar-importance",
            "value": "2",
            "domain": ".tradingeconomics.com",
            "path": "/",
        }])
        # 국가 필터도 쿠키로 강제해서 렌더 단계부터 US/KR만 요청
        context.add_cookies([{
            "name": "calendar-countries",
            "value": "usa,kor",
            "domain": ".tradingeconomics.com",
            "path": "/",
        }])
        page = context.new_page()

        print(f"  브라우저 실행 중... {calendar_url}")
        try:
            page.goto(calendar_url, wait_until="networkidle", timeout=40_000)
        except PWTimeout:
            print("  경고: 타임아웃 — 현재 렌더된 DOM 사용")

        # 로그인 리다이렉트 감지
        if any(kw in page.url.lower() for kw in ("login", "register", "signin")):
            browser.close()
            raise ValueError(
                "쿠키 만료 — 재발급 필요\n"
                "tradingeconomics.com 재로그인 후 F12 Console에서\n"
                "copy(document.cookie) 실행 → .env의 TE_COOKIES에 붙여넣기"
            )

        # 캘린더 테이블 HTML 추출
        try:
            cal_html = page.inner_html("#calendar") or ""
        except Exception:
            pass
        if not cal_html:
            try:
                cal_html = page.inner_html(".calendar-table") or ""
            except Exception:
                pass

        browser.close()

    if not cal_html:
        print("  경고: 캘린더 HTML을 가져오지 못했습니다.")
        return []

    print(f"  캘린더 HTML: {len(cal_html):,}자")
    records = parse_calendar_html(cal_html, date_from, date_to)
    return records

# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    today_str = date.today().strftime("%Y-%m-%d")
    today = date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    next_saturday = last_monday + timedelta(days=19)
    date_from = last_monday.strftime("%Y-%m-%d")
    date_to   = next_saturday.strftime("%Y-%m-%d")

    print(f"수집 기간: {date_from} ~ {date_to}")
    print(f"대상 국가: {len(COUNTRIES)}개\n")

    try:
        records = fetch_calendar(date_from, date_to)
    except (ValueError, RuntimeError) as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    if not records:
        print("\n수집된 데이터가 없습니다.")
        return

    df = pd.DataFrame(records)

    # 안전망: 혹시 남은 값이 있으면 한 번 더 US/KR만 유지
    if "Country" in df.columns:
        df = df[df["Country"].astype(str).str.lower().isin(TARGET_COUNTRIES)]
        df = df[df["Country"] != ""]

    # 중복 제거
    if "Date" in df.columns and "Event" in df.columns:
        df = df.drop_duplicates(subset=["Date", "Event"])

    # Previous 값이 비어있는 행 제거
    if "Previous" in df.columns:
        prev = df["Previous"].astype(str).str.strip()
        df = df[prev.notna() & (~prev.isin(["", "nan", "None", "—"]))]

    df = df.reset_index(drop=True)

    # 국가별 건수 출력
    print("\n국가별 건수:")
    if "Country" in df.columns:
        for country, cnt in df["Country"].value_counts().items():
            print(f"  {str(country):25s}: {cnt}건")

    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"economic_calendar_{today_str}.csv",
    )
    df.to_csv(out_path, index=False, encoding="utf-8-sig")

    print(f"\n총 수집 건수: {len(df)}건")
    print(f"CSV 저장 완료: {out_path}")


if __name__ == "__main__":
    main()
