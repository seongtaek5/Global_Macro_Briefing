import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"), override=True)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(BASE_DIR), "Calendar", ".env"), override=True)

from api_and_rss import helpers
import api_and_rss.big6_search_rss as big6_search
import api_and_rss.google_news_searcher as googlenews_search
import api_and_rss.naver_news_searcher as naver_search
import api_and_rss.filter as llm_filters
import settings


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)


TOPIC_MAP = settings.PAIRS
REVERSE_TOPIC_MAP = settings.REVERSE_PAIRS
MAX_ARTICLE_PER_TOPIC = settings.MAX_ARTICLE_PER_KEYWORD


def _make_json_safe(obj):
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}

    if isinstance(obj, list):
        return [_make_json_safe(v) for v in obj]

    if isinstance(obj, tuple):
        return [_make_json_safe(v) for v in obj]

    if isinstance(obj, set):
        return [_make_json_safe(v) for v in obj]

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()

    if hasattr(obj, "tolist"):
        try:
            return obj.tolist()
        except Exception:
            pass

    if hasattr(obj, "item"):
        try:
            return obj.item()
        except Exception:
            pass

    try:
        json.dumps(obj)
        return obj
    except TypeError:
        return str(obj)


def _extract_region(topic: str) -> str:
    return getattr(settings, "TOPIC_REGION", {}).get(topic, "Other")


def _process_article_for_filter(topic: str, article: dict) -> dict | None:
    title = str(article.get("title", "")).rsplit(" - ", 1)[0]
    title_lower = title.lower()

    if any(
        keyword.strip().lower() in title_lower
        for keyword in getattr(settings, "EXCLUDED_TITLE_KEYWORDS", [])
        if keyword.strip()
    ):
        return None

    new_article_map = {}
    is_korean = article.get("rss_used") == "naver_api"
    if is_korean and title.rstrip().endswith("...") and article.get("link"):
        full_title = helpers.get_full_title(article["link"])
        if full_title:
            title = full_title

    lede_text = str(article.get("lede", "")).strip()
    has_article_body = False
    content_for_filter = lede_text if lede_text else title

    if article.get("link"):
        extracted_summary = helpers.get_summary(topic, title, article["link"])
        if extracted_summary != "Text extraction was not available." and str(extracted_summary).strip():
            content_for_filter = str(extracted_summary).strip()
            has_article_body = True

    news_info = llm_filters.run_news(
        company=topic,
        title=title,
        content=content_for_filter,
        is_google_rss=str(article.get("rss_used", "")).startswith("https://news.google.com/rss/search?q="),
        is_korean=is_korean,
        classify_asset_class=True,
    )

    new_article_map.update(article)
    new_article_map["title"] = title
    new_article_map["topic"] = topic
    new_article_map["region"] = _extract_region(topic)

    is_lede_only = (not has_article_body) and bool(lede_text)
    new_article_map["is_lede_only"] = is_lede_only

    if is_lede_only and lede_text:
        new_article_map["AI_summary"] = llm_filters.summarize_lede_only(topic, title, lede_text)
    elif "summary" in news_info:
        new_article_map["AI_summary"] = news_info["summary"]

    summary_text = str(new_article_map.get("AI_summary", "")).strip()
    lede_text = str(new_article_map.get("lede", "")).strip()
    has_visible_summary = (
        (summary_text not in ("", "Didn't run yet", "REPLACE_REAL_HERE", "SUMMARY ERROR"))
        or bool(lede_text)
    )

    new_article_map["categories"] = []
    new_article_map["newsstate"] = news_info
    new_article_map["usable"] = news_info.get("is_relevant") is True and has_visible_summary

    if not has_visible_summary:
        logging.info(
            "Dropped title-only article (no summary/lede) | topic=%s | title=%s",
            topic,
            title,
        )

    return new_article_map


def _filter_topic_news_sequential(
    topic: str,
    news: list[dict],
    usable_cap: int | None,
) -> list[dict]:
    if not news:
        return []

    filtered_news: list[dict] = []
    usable_count = 0

    for idx, article in enumerate(news):
        if usable_cap is not None and usable_count >= usable_cap:
            break

        try:
            processed = _process_article_for_filter(topic, article)
        except Exception:
            logging.exception(
                "Filter failed | topic=%s | idx=%s | title=%s",
                topic,
                idx,
                article.get("title"),
            )
            processed = None

        if processed is not None:
            filtered_news.append(processed)
            if processed.get("usable") is True:
                usable_count += 1

    return filtered_news


def _filter_all_topics_parallel(
    cumulative_news: dict[str, list[dict]],
    usable_caps: dict[str, int],
    topic_workers: int,
) -> dict[str, list[dict]]:
    if not cumulative_news:
        return {}

    workers = max(1, min(topic_workers, len(cumulative_news)))
    results_by_topic: dict[str, list[dict]] = {}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_topic = {
            executor.submit(
                _filter_topic_news_sequential,
                topic,
                news,
                usable_caps.get(topic),
            ): topic
            for topic, news in cumulative_news.items()
        }

        for future in as_completed(future_to_topic):
            topic = future_to_topic[future]
            try:
                results_by_topic[topic] = future.result()
            except Exception:
                logging.exception("Topic filter failed | topic=%s", topic)
                results_by_topic[topic] = []

    return {topic: results_by_topic.get(topic, []) for topic in cumulative_news.keys()}


def _build_country_view(news_by_topic: dict[str, list[dict]]) -> dict[str, dict]:
    country_view: dict[str, dict] = {}
    section_groups = getattr(settings, "SECTION_GROUPS", {})

    for country in getattr(settings, "SECTION_ORDER", list(section_groups.keys())):
        topics = section_groups.get(country, [])
        country_topics = {}

        for topic in topics:
            topic_articles = [
                a for a in news_by_topic.get(topic, [])
                if isinstance(a, dict) and a.get("usable") is True
            ]
            topic_articles.sort(key=lambda x: str(x.get("date", "")), reverse=True)
            if topic_articles:
                country_topics[topic] = topic_articles

        if country_topics:
            country_view[country] = country_topics

    return country_view


def main() -> None:
    time_now = helpers.now_utc_dt()
    logging.info("current time in UTC: %s", time_now)

    big6_search.reset_counters()
    googlenews_search.reset_counters()
    naver_search.reset_counters()

    cumulative_news: dict[str, list[dict]] = {topic: [] for topic in TOPIC_MAP.keys()}

    big6_keywords = list(REVERSE_TOPIC_MAP.keys())
    big6_data = big6_search.search_all_rss(big6_keywords, now_utc=time_now)

    for article in big6_data:
        for keyword in article.get("keywords_used", []):
            topic = REVERSE_TOPIC_MAP.get(keyword)
            if topic and topic in cumulative_news:
                cumulative_news[topic].append(article)

    logging.info("finished searching major article sites")

    logging.info("starting search using google rss")
    google_data = googlenews_search.search_google_rss(TOPIC_MAP, big6_data, now_utc=time_now)
    for article in google_data:
        for topic in article.get("keywords_used", []):
            if topic not in cumulative_news:
                cumulative_news[topic] = []
            if len(cumulative_news[topic]) < MAX_ARTICLE_PER_TOPIC:
                cumulative_news[topic].append(article)
                time.sleep(0.1)

    logging.info("finished searching google news")

    naver_topics = getattr(settings, "NAVER_PAIRS", {})
    if naver_topics:
        logging.info("starting naver news search for %s topics", len(naver_topics))
        all_preexisting = [art for arts in cumulative_news.values() for art in arts]
        naver_data = naver_search.search_naver_news(naver_topics, all_preexisting, now_utc=time_now)
        for article in naver_data:
            for topic in article.get("keywords_used", []):
                if topic not in cumulative_news:
                    cumulative_news[topic] = []
                if len(cumulative_news[topic]) < MAX_ARTICLE_PER_TOPIC:
                    cumulative_news[topic].append(article)
        logging.info("finished naver news search")

    topic_filter_workers = getattr(settings, "COMPANY_FILTER_MAX_WORKERS", 10)
    usable_caps = getattr(settings, "USABLE_ARTICLE_CAPS", {})
    news_through_filter = _filter_all_topics_parallel(
        cumulative_news=cumulative_news,
        usable_caps=usable_caps,
        topic_workers=topic_filter_workers,
    )

    for topic in news_through_filter:
        before = len([a for a in news_through_filter[topic] if a.get("usable")])
        news_through_filter[topic] = llm_filters.deduplicate_articles(news_through_filter[topic])
        after = len([a for a in news_through_filter[topic] if a.get("usable")])
        if before != after:
            logging.info("Dedup | %s: %d -> %d usable articles", topic, before, after)

    for topic in news_through_filter:
        before = len([a for a in news_through_filter[topic] if a.get("usable")])
        news_through_filter[topic] = llm_filters.apply_final_relevance_pass(
            news_through_filter[topic],
            topic,
        )
        after = len([a for a in news_through_filter[topic] if a.get("usable")])
        if before != after:
            logging.info("Final relevance pass | %s: %d -> %d usable articles", topic, before, after)

    payload = {
        "generated_at_utc": time_now.isoformat(),
        "news_through_filter": news_through_filter,
        "country_view": _build_country_view(news_through_filter),
    }

    output_path = os.path.join(BASE_DIR, "briefing_result.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(_make_json_safe(payload), f, ensure_ascii=False, indent=2)

    big6_counts = big6_search.get_counters()
    google_counts = googlenews_search.get_counters()
    naver_counts = naver_search.get_counters()
    encountered_total = (
        big6_counts["encountered"] + google_counts["encountered"] + naver_counts["encountered"]
    )
    analyzed_total = (
        big6_counts["analyzed"] + google_counts["analyzed"] + naver_counts["analyzed"]
    )
    pre_llm_total = sum(len(v) for v in cumulative_news.values())
    post_llm_total = sum(len(v) for v in news_through_filter.values())
    final_total = sum(1 for v in news_through_filter.values() for a in v if a.get("usable"))

    logging.info(
        "RUN COUNTS | encountered=%s | analyzed=%s | pre_llm=%s | post_llm=%s | final=%s",
        encountered_total,
        analyzed_total,
        pre_llm_total,
        post_llm_total,
        final_total,
    )
    logging.info("Saved output to %s", output_path)


if __name__ == "__main__":
    start = time.perf_counter()
    main()
    end = time.perf_counter()
    logging.debug("Elapsed: %.2f seconds", end - start)
