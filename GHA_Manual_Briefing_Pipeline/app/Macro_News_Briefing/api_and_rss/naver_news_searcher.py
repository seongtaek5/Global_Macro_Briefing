import html
import os
import re
import logging
import requests
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import settings
import api_and_rss.find_duplicate_articles as find_duplicate_articles

HOUR_WINDOW = settings.HOUR_WINDOW
FUTURE_TOLERANCE_MINUTES = 30

_COUNTERS = {
    "encountered": 0,
    "analyzed": 0,
}


def reset_counters():
    _COUNTERS["encountered"] = 0
    _COUNTERS["analyzed"] = 0


def get_counters():
    return dict(_COUNTERS)


def _strip_html(text: str) -> str:
    """Naver API 문자열에서 HTML 태그를 제거해 반환한다."""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _is_within_window(dt: datetime, now_utc: datetime) -> bool:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    window_start = now_utc - timedelta(hours=HOUR_WINDOW)
    window_end = now_utc + timedelta(minutes=FUTURE_TOLERANCE_MINUTES)
    return window_start <= dt_utc <= window_end


def _parse_pub_date_to_utc(pub_date_str: str) -> datetime | None:
    try:
        dt_utc = parsedate_to_datetime(pub_date_str)
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        return dt_utc.astimezone(timezone.utc)
    except Exception:
        return None


def _get_and_check_duplicate_embedding(title: str, existing_embeddings: dict, threshold: float = settings.SIM_THRESHOLD) -> tuple[bool, object]:
    """제목의 embedding을 계산하고 기존 embedding 집합과 유사도를 비교해 dedup 여부를 반환한다."""
    emb = find_duplicate_articles.get_korean_embedding(title)
    is_dup, _match = find_duplicate_articles.is_article_in_list(emb, existing_embeddings.keys())
    return is_dup, emb


def search_naver_news(
    keyword_map: dict[str, str],
    preexisting_entries: list[dict],
    now_utc: datetime | None = None,
) -> list[dict]:
    """Naver News API에서 회사별 검색어를 조회해 조건을 만족한 기사 목록을 반환한다.

    입력:
    - keyword_map: {회사명: '검색어1&검색어2'} 형태의 매핑
    - preexisting_entries: 기존 수집 기사 목록
    - now_utc: 시간 필터 기준 시각

    출력:
    - 필터와 dedup을 통과한 기사 dict 리스트

    동작:
    - 회사별 키워드 조회 후 시간 윈도우, 제목 매칭, embedding dedup을 적용한다.
    """
    client_id = os.environ.get("NAVER_CLIENT_ID", "")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        logging.warning("NAVER_CLIENT_ID or NAVER_CLIENT_SECRET not set; skipping Naver search")
        return []

    now = now_utc or datetime.now(timezone.utc)
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    page_size = 100
    max_start = 1000

    preexisting_embeddings_by_company: dict[str, dict] = {
        company_name: {} for company_name in keyword_map.keys()
    }
    for entry in preexisting_entries:
        t = entry.get("title", "")
        if not t:
            continue
        keywords_used = entry.get("keywords_used") or []
        for company_name in keywords_used:
            if company_name in preexisting_embeddings_by_company:
                emb = find_duplicate_articles.get_korean_embedding(t)
                preexisting_embeddings_by_company[company_name][emb] = t
    results: list[dict] = []

    for company, search_string in keyword_map.items():
        queries = [q.strip() for q in search_string.split("&") if q.strip()]
        company_embeddings = dict(preexisting_embeddings_by_company.get(company, {}))

        for query in queries:
            logging.info("naver search | company=%s | query=%s", company, query)
            all_terms = [t.strip().lower().replace(" ", "") for t in search_string.split("&") if t.strip()]

            for start in range(1, max_start + 1, page_size):
                try:
                    resp = requests.get(
                        "https://openapi.naver.com/v1/search/news.json",
                        headers=headers,
                        params={
                            "query": query,
                            "display": page_size,
                            "start": start,
                            "sort": "date",
                        },
                        timeout=15,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except Exception:
                    logging.exception(
                        "Naver API request failed | query=%s | start=%s",
                        query,
                        start,
                    )
                    break

                items = data.get("items", [])
                if not items:
                    logging.info(
                        "naver page empty | company=%s | query=%s | start=%s",
                        company,
                        query,
                        start,
                    )
                    break

                newest_dt_utc = None
                for probe in items:
                    newest_dt_utc = _parse_pub_date_to_utc(probe.get("pubDate", ""))
                    if newest_dt_utc is not None:
                        break

                if newest_dt_utc is not None and not _is_within_window(newest_dt_utc, now):
                    logging.info(
                        "naver page out of time window; stopping query | company=%s | query=%s | start=%s | newest=%s",
                        company,
                        query,
                        start,
                        newest_dt_utc.isoformat(),
                    )
                    break

                for item in items:
                    _COUNTERS["encountered"] += 1

                    raw_title = _strip_html(item.get("title", ""))
                    description = _strip_html(item.get("description", ""))
                    link = item.get("originallink") or item.get("link", "")
                    pub_date_str = item.get("pubDate", "")

                    if not raw_title or not link:
                        continue

                    dt_utc = _parse_pub_date_to_utc(pub_date_str)
                    if dt_utc is None:
                        logging.debug("Could not parse date | pubDate=%s", pub_date_str)
                        continue

                    _COUNTERS["analyzed"] += 1

                    if not _is_within_window(dt_utc, now):
                        continue

                    title_lower = raw_title.lower().replace(" ", "")
                    if not any(term in title_lower for term in all_terms):
                        continue

                    in_list, emb = _get_and_check_duplicate_embedding(raw_title, company_embeddings)
                    if in_list:
                        logging.debug("Duplicate naver title (embedding) | title=%s", raw_title)
                        continue

                    entry_info = {
                        "title": raw_title,
                        "author": "unspecified",
                        "date": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                        "_date_dt": dt_utc,
                        "lede": description,
                        "AI_summary": "Didn't run yet",
                        "link": link,
                        "source": "Naver News",
                        "rss_used": "naver_api",
                        "keywords_used": [company],
                        "is_google_rss": False,
                    }

                    results.append(entry_info)
                    company_embeddings[emb] = raw_title
                    logging.info("finally added from naver | company=%s | title=%s", company, raw_title)

                if len(items) < page_size:
                    logging.info(
                        "naver last page reached | company=%s | query=%s | start=%s | items=%s",
                        company,
                        query,
                        start,
                        len(items),
                    )
                    break

    return results
    
