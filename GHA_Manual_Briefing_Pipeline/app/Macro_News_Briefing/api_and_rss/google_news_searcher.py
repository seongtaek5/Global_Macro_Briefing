import feedparser
import api_and_rss.helpers as helpers
import time
from datetime import datetime, timedelta, timezone
import api_and_rss.find_duplicate_articles as find_duplicate_articles
import settings
import logging

model = find_duplicate_articles

RSS_LINK_1 = "https://news.google.com/rss/search?q=" 
RSS_LINK_2 = "&hl=en-US&gl=US&ceid=US:en"
RETRIES = settings.RETRIES
SLEEP_TIME = settings.SLEEP_TIME
HOUR_WINDOW = settings.HOUR_WINDOW

_COUNTERS = {
    "encountered": 0,
    "analyzed": 0,
}

def reset_counters():
    _COUNTERS["encountered"] = 0
    _COUNTERS["analyzed"] = 0

def get_counters():
    return dict(_COUNTERS)



def is_within_past_n_hours(dt: datetime, hours: int = HOUR_WINDOW, now_utc: datetime | None = None) -> bool:
    """입력 시각이 기준 시각 대비 최근 N시간 구간에 있으면 True를 반환한다."""
    if dt.tzinfo is None:
        raise ValueError("dt must be timezone-aware")

    now = now_utc or datetime.now(timezone.utc)
    window_start = now - timedelta(hours=hours)

    dt_utc = dt.astimezone(timezone.utc)
    return window_start <= dt_utc <= now


def search_google_rss(keyword_map, preexisting_entries, now_utc: datetime | None = None):
    """회사별 검색어를 사용해 Google RSS를 조회하고 조건을 만족한 기사 목록을 반환한다.

    입력:
    - keyword_map: {회사명: '검색어1&검색어2'} 형태의 매핑
    - preexisting_entries: 기존 수집 기사 목록
    - now_utc: 시간 필터 기준 시각

    출력:
    - 필터와 dedup을 통과한 기사 dict 리스트

    동작:
    - 키워드 매칭, 시간 윈도우, 제외 소스, embedding dedup을 순서대로 적용한다.
    """
    logging.info("starting google RSS search...")
    keywords = keyword_map.keys()
    for i in range(RETRIES):
        try:
            list_of_filtered_feeds = []
            already_added_titles = [entry["title"] for entry in preexisting_entries]
            already_added_embeddings = {entry["embedding"]: entry["title"] for entry in preexisting_entries}

            for keyword in keywords:
                search_terms = [t.strip() for t in keyword_map[keyword].split("&") if t.strip()]
                key_terms = [t.strip().lower().replace(" ", "") for t in keyword_map[keyword].split("&") if t.strip()]

                for search_term in search_terms:
                    query = search_term.replace(" ", "+")
                    link = RSS_LINK_1 + query + RSS_LINK_2
                    logging.debug("searching. query=%s | link=%s",
                                  query,
                                  link)
                    try:
                        feed = feedparser.parse(link)
                        entries = feed['entries']
                        for entry in entries:
                            try:
                                _COUNTERS["encountered"] += 1
                                entry_info = {'title' : "",
                                            'author' : "unspecified",
                                            'date' : "",
                                            'lede' : "",
                                            'AI_summary' : "Didn't run yet",
                                            'link' : "",
                                            'rss_used': "",
                                            'source' : "",
                                            'keywords_used' : [],
                                            'embedding': None}
                                
                                if 'title' in entry:
                                    entry_info['title'] = entry['title']
                                    entry_info['embedding'] = model.get_embedding(entry_info['title'].rsplit(" - ", 1)[0])
                                if 'author' in entry:
                                    entry_info['author'] = entry['author']
                                if 'published' in entry:
                                    dt_utc = helpers.convert_rss_to_utc_dt(entry['published'])
                                    entry_info['_date_dt'] = dt_utc
                                    entry_info['date'] = dt_utc.strftime("%Y-%m-%d %H:%M:%S")
                                if 'summary' in entry:
                                    entry_info['lede'] = entry['summary']
                                if 'link' in entry:
                                    entry_info['link'] = entry['link']
                                if 'source' in entry:
                                    entry_info['source'] = entry['source']['title']
                                entry_info['rss_used'] = link

                                # key 값이 제목 또는 lede(summary)에 있는지 확인
                                title_norm = entry_info['title'].rsplit(" - ", 1)[0].lower().replace(" ", "")
                                lede_norm = entry.get('summary', '').lower().replace(" ", "")
                                _COUNTERS["analyzed"] += 1
                                if not any(kt in title_norm or kt in lede_norm for kt in key_terms):
                                    if settings.VERBOSE:
                                        logging.info("key not found in title or lede. key_terms=%s | title=%s",
                                                     key_terms, entry_info['title'])
                                    continue

                                if '_date_dt' not in entry_info:
                                    continue
                                if not is_within_past_n_hours(entry_info['_date_dt'], now_utc=now_utc):
                                    if settings.VERBOSE:
                                        logging.info(
                                            "Skipped article (due to time window)| "
                                            "title=%s | now_utc=%s | article_utc=%s",
                                            entry_info["title"],
                                            datetime.now(timezone.utc).isoformat(),
                                            entry_info['_date_dt'].astimezone(timezone.utc).isoformat(),
                                        )
                                    continue

                                in_list, match = model.is_article_in_list(entry_info['embedding'], already_added_embeddings.keys())
                                if in_list:
                                    logging.debug(
                                        "Duplicate embedding detected | title=%s | similarity=%s",
                                        entry_info["title"],
                                        already_added_embeddings[match],
                                    )
                                    continue

                                entry_info['keywords_used'].append(keyword)

                                real_url = helpers.get_article_url(entry['link'])
                                real_url_lower = str(real_url).lower()
                                if any(domain.lower() in real_url_lower for domain in getattr(settings, "EXCLUDED_DOMAINS", [])):
                                    logging.info(
                                        "Skipped article (excluded domain) | domain_list=%s | link=%s | title=%s",
                                        getattr(settings, "EXCLUDED_DOMAINS", []),
                                        real_url,
                                        entry_info['title'],
                                    )
                                    continue

                                entry_info['AI_summary'] = "REPLACE_REAL_HERE"
                                entry_info['link'] = real_url

                                list_of_filtered_feeds.append(entry_info)
                                already_added_titles.append(entry_info['title'])
                                already_added_embeddings[entry_info['embedding']] = entry_info['title']
                                logging.info("finally added from google RSS.(also added to embeddings) | title=%s",
                                             entry_info['title'])
                                continue
                            except Exception as e:
                                if settings.GUARDRAILS:
                                    logging.debug("article invalid. skipping source... err=%s",
                                                  e)
                                    continue
                                else:
                                    raise
                    except Exception as e:
                        if settings.GUARDRAILS:
                            logging.debug("RSS Source invalid. skipping source... err=%s",
                                            e)
                            continue
                        else:
                            raise
                    time.sleep(0.2)
            return list_of_filtered_feeds
        
    
        except Exception as e:
            if settings.GUARDRAILS:
                logging.debug("all loops failed. starting again after sleep. err=%s",
                e)
                time.sleep(SLEEP_TIME)
            else:
                raise
    return []
