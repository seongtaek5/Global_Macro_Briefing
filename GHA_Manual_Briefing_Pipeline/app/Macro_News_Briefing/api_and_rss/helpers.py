import json
import requests
from bs4 import BeautifulSoup
from trafilatura import extract
from newspaper import Article
import api_and_rss.sumy_api as sumy_api
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging


DEFAULT_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

def convert_rss_to_utc_dt(published: str):
    dt = parsedate_to_datetime(published)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)

def now_utc_dt():
    """
    Returns current time as a timezone-aware UTC datetime,
    identical in type/semantics to convert_rss_to_utc_dt(...).
    """
    return datetime.now(timezone.utc)

def contains_korean(text: str) -> bool:
    return any(
        '\uac00' <= ch <= '\ud7a3' or   # Hangul syllables
        '\u1100' <= ch <= '\u11ff' or   # Hangul Jamo
        '\u3130' <= ch <= '\u318f'      # Hangul Compatibility Jamo
        for ch in text
    )

def get_summary(company: str, title: str, real_url: str):
    """기사 URL에서 본문을 추출해 요약 문자열을 반환한다.

    입력:
    - company: 대상 회사명
    - title: 기사 제목
    - real_url: 기사 원문 URL

    출력:
    - 요약 문자열 또는 추출 불가 안내 문자열

    동작:
    - trafilatura, newspaper 순서로 본문 추출을 시도하고 Sumy 요약을 적용한다.
    """
    logging.info(f"trying tranfilatura with: {real_url}")
    text = get_text_tranfilatura(real_url)
    if text is None:
        logging.info("Failed tranfilatura, trying newspaper...")
        try:
            logging.info(f"trying newspaper3k with: {real_url}")
            text = get_text_newspaper(real_url)

        except Exception as e:
            logging.info(f"Newspaper failed: {e}")
            logging.info("No text for this article")
            text = None

    if (text is not None) and text.replace(" ", "").replace("\n", "") == "":
        text = None
    if text is not None:
        try:
            summary = sumy_api.summarize_sumy(text)
        except Exception:
            logging.exception("sumy summarization failed; fallback to raw text slice | url=%s", real_url)
            summary = text[:1500].strip()
        logging.info(f"extraction SUCCESS: {real_url}")
        return summary
    else:
        logging.info(f"extraction FAIL: {real_url}")
        return "Text extraction was not available."

    

def get_text_newspaper(real_url : str):
    """URL을 입력받아 newspaper 파서로 본문 텍스트를 반환한다."""
    article = Article(real_url)
    article.config.browser_user_agent = DEFAULT_BROWSER_HEADERS["User-Agent"]
    article.download()
    article.parse()
    return article.text


def get_text_tranfilatura(real_url):
    """URL을 입력받아 trafilatura 추출 결과 텍스트를 반환한다."""
    try:
        resp = requests.get(real_url, headers=DEFAULT_BROWSER_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception:
        logging.exception("trafilatura prefetch failed | url=%s", real_url)
        return None

    return extract(resp.text)


def get_full_title(url: str) -> str | None:
    """기사 URL에서 전체 제목을 추출해 반환한다.

    입력:
    - url: 기사 URL

    출력:
    - 제목 문자열 또는 None

    동작:
    - newspaper 파서로 제목 필드를 읽는다.
    """
    try:
        article = Article(url)
        article.download()
        article.parse()
        if article.title:
            return article.title
    except Exception as e:
        logging.warning("get_full_title failed for %s: %s", url, e)
    return None


def get_article_url(google_rss_url: str) -> str:
    """Google News RSS 링크를 입력받아 원문 기사 URL을 반환한다.

    입력:
    - google_rss_url: Google News RSS 기사 링크

    출력:
    - 원문 기사 URL 문자열

    동작:
    - 기사 페이지의 data-p를 파싱하고 batchexecute 요청으로 최종 URL을 추출한다.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/132.0.0.0 Safari/537.36"
        )
    }

    resp = requests.get(google_rss_url, headers=headers)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    data_p = soup.select_one("c-wiz[data-p]")
    if not data_p:
        raise Exception("Could not find c-wiz[data-p] element")

    raw_data = data_p.get("data-p")

    try:
        obj = json.loads(raw_data.replace("%.@.", '["garturlreq",'))
    except Exception as e:
        raise Exception(f"Failed to parse data-p JSON: {e}")

    f_req = json.dumps(
        [[["Fbv4je", json.dumps(obj[:-6] + obj[-2:]), "null", "generic"]]]
    )
    payload = {"f.req": f_req}

    post_headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        **headers,
    }

    post_resp = requests.post(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute",
        headers=post_headers,
        data=payload,
    )
    post_resp.raise_for_status()

    cleaned = post_resp.text.replace(")]}'", "")
    parsed = json.loads(cleaned)
    array_string = parsed[0][2]
    article_url = json.loads(array_string)[1]

    return article_url
