import feedparser
import time
import api_and_rss.rss_feed_links.CNBC_rss as CNBC_rss
import api_and_rss.rss_feed_links.bloomberg_rss as bloomberg_rss
import api_and_rss.rss_feed_links.FT_rss as FT_rss
import api_and_rss.rss_feed_links.WSJ_rss as WSJ_rss
import api_and_rss.rss_feed_links.NYT_rss as NYT_rss
import api_and_rss.rss_feed_links.Reuters_rss as Reuters_rss
from datetime import datetime, timedelta, timezone
import api_and_rss.find_duplicate_articles as find_duplicate_articles
import api_and_rss.helpers as helpers
import logging
import settings

model = find_duplicate_articles
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



def search_all_rss(keywords: list, now_utc: datetime | None = None):
    """여러 RSS 소스를 순회하며 키워드 일치 기사 목록을 수집해 반환한다."""
    news_companies = [WSJ_rss.WSJ_rss,
                        bloomberg_rss.bloomberg_rss,
                        FT_rss.ft_rss,
                        CNBC_rss.cnbc_rss,
                        NYT_rss.nyt_rss,
                        Reuters_rss.reuters_rss]
    
    all_sources = []
    already_added_titles = set()
    already_added_embeddings = {}

    for company in news_companies:
        list_of_filtered_feeds = search_rss(
            keywords,
            company,
            already_added_titles,
            already_added_embeddings,
            now_utc=now_utc,
        )
        all_sources += list_of_filtered_feeds

    return all_sources


def search_rss(
    keywords: list,
    feed_links: list,
    already_added_titles: set,
    already_added_embeddings: dict,
    now_utc: datetime | None = None,
):
    """RSS 링크 목록에서 키워드 일치 기사를 추출하고 embedding으로 dedup해 반환한다."""

    for i in range(RETRIES):
        try:
            list_of_filtered_feeds = []
            for link in feed_links:
                if settings.VERBOSE:
                    logging.info(f"entered: {link}")
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
                                        'keywords_used' : [],
                                        'embedding' : None}
                            
                            if 'title' in entry:
                                entry_info['title'] = entry['title']
                                entry_info['embedding'] = model.get_embedding(entry_info['title'])

                            if 'summary' in entry:
                                entry_info['lede'] = entry['summary']
                            if 'author' in entry:
                                entry_info['author'] = entry['author']
                            if 'published' in entry:
                                dt_utc = helpers.convert_rss_to_utc_dt(entry['published'])
                                entry_info['date'] = dt_utc.strftime("%Y-%m-%d %H:%M:%S")  # keep your string for output
                                entry_info['_date_dt'] = dt_utc  # internal datetime for comparisons
                            if '_date_dt' not in entry_info:
                                continue
                            if 'link' in entry:
                                entry_info['link'] = entry['link']

                            entry_info['rss_used'] = link

                            if is_within_past_n_hours(entry_info['_date_dt'], now_utc=now_utc):
                                in_list, match = model.is_article_in_list(entry_info['embedding'], list(already_added_embeddings.keys()))
                                if not in_list:
                                    will_add = False
                                    matched_keywords = []
                                    _COUNTERS["analyzed"] += 1
                                    for keyword in keywords:
                                        stripped_searchwords = [k.strip().lower().replace(" ", "") for k in keyword.split("&")]

                                        title = entry_info['title'].lower().replace(" ", "")
                                        lede = entry_info['lede'].lower().replace(" ", "")
                                        matched_keywords = [
                                            k for k in stripped_searchwords
                                            if k in title or k in lede
                                        ]

                                        if matched_keywords:
                                            entry_info["keywords_used"].append(keyword)

                                            will_add = True
                                            continue
                                        else:
                                            continue
                                    if will_add == True:
                                        if entry_info['title'] not in already_added_titles:
                                            list_of_filtered_feeds.append(entry_info)
                                            already_added_titles.add(entry_info['title'])
                                            already_added_embeddings[entry_info['embedding']] = entry_info['title']

                                            logging.info(
                                                "article with keyword match found and added. | "
                                                "title=%s | lede=%s | keyword=%s",
                                                entry_info['title'],
                                                entry_info['lede'], 
                                                entry_info["keywords_used"]
                                                
                                            )
                                else:
                                    logging.info(
                                        "Skipped article (duplicate by embedding similarity) | "
                                        "title=%s | matched_title=%s",
                                        entry_info["title"],
                                        already_added_embeddings[match],
                                    )
                            else:
                                if settings.VERBOSE:
                                    logging.info(
                                        "Skipped article (due to time window)| "
                                        "title=%s | now_utc=%s | article_utc=%s",
                                        entry_info["title"],
                                        datetime.now(timezone.utc).isoformat(),
                                        entry_info['_date_dt'].astimezone(timezone.utc).isoformat(),
                                    )
                                continue

                            
                        except Exception as e:
                            if settings.GUARDRAILS:
                                logging.info("Error: %s", e)
                                logging.info("article invalid. skipping source...")
                                continue
                            else:
                                raise


                except Exception as e:
                    if settings.GUARDRAILS:
                        logging.info("Error: %s", e)
                        logging.info("RSS SOURCE INVALID. skipping source...")
                        continue
                    else:
                        raise
                time.sleep(0.3)

            return list_of_filtered_feeds
        
    
        except Exception as e:
            if settings.GUARDRAILS:
                logging.info("Error: %s", e)
                logging.info("retrying...")
                time.sleep(SLEEP_TIME)
            else:
                raise
    return []
