"""Phase 1: AIニュース収集モジュール.

RSSフィードとGoogle Custom Search APIからAIニュースを収集し、
LLM API（Gemini/Claude）でスコアリング・要約してTOP N記事を選定する。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from email.utils import parsedate_to_datetime

import re

import feedparser
import httpx

logger = logging.getLogger(__name__)

# Maximum age (in hours) for an article to be considered "fresh"
_MAX_ARTICLE_AGE_HOURS = 48

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config(config_path: Path = Path("config.json")) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# RSS collection
# ---------------------------------------------------------------------------

def collect_from_rss(feeds: list[str], max_per_feed: int = 10) -> list[dict]:
    """Collect articles from RSS feeds."""
    articles = []
    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_feed]:
                articles.append({
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:500],
                    "published": entry.get("published", ""),
                    "source": feed.feed.get("title", url),
                })
        except Exception:
            logger.warning("Failed to parse RSS feed: %s", url, exc_info=True)
    logger.info("Collected %d articles from %d RSS feeds", len(articles), len(feeds))
    return articles


# ---------------------------------------------------------------------------
# Google Custom Search
# ---------------------------------------------------------------------------

def collect_from_google_cse(
    queries: list[str],
    api_key: str,
    engine_id: str,
) -> list[dict]:
    """Collect articles via Google Custom Search API."""
    articles = []
    with httpx.Client(timeout=15) as client:
        for query in queries:
            try:
                resp = client.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params={
                        "key": api_key,
                        "cx": engine_id,
                        "q": query,
                        "num": 5,
                        "dateRestrict": "d1",
                    },
                )
                resp.raise_for_status()
                for item in resp.json().get("items", []):
                    articles.append({
                        "title": item.get("title", ""),
                        "link": item.get("link", ""),
                        "summary": item.get("snippet", ""),
                        "published": "",
                        "source": "Google CSE",
                    })
            except Exception:
                logger.warning("Google CSE query failed: %s", query, exc_info=True)
    logger.info("Collected %d articles from Google CSE", len(articles))
    return articles


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate(articles: list[dict]) -> list[dict]:
    """Remove duplicate articles by URL."""
    seen: set[str] = set()
    unique = []
    for a in articles:
        url = a["link"]
        if url and url not in seen:
            seen.add(url)
            unique.append(a)
    return unique


# ---------------------------------------------------------------------------
# OG image extraction
# ---------------------------------------------------------------------------

_OG_IMAGE_RE = re.compile(
    r'<meta\s+(?:[^>]*?)property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_IMAGE_RE_ALT = re.compile(
    r'<meta\s+(?:[^>]*?)content=["\']([^"\']+)["\']\s+property=["\']og:image["\']',
    re.IGNORECASE,
)


def fetch_og_images(
    articles: list[dict],
    output_dir: Path,
) -> None:
    """Fetch og:image for each article and save to output_dir.

    Adds 'og_image' key to each article dict with the local file path,
    or empty string if not found.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=15, follow_redirects=True) as client:
        for i, article in enumerate(articles):
            article["og_image"] = ""
            link = article.get("link", "")
            if not link:
                continue

            try:
                # Fetch the HTML page (only first 50KB to find meta tags)
                resp = client.get(link, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                html = resp.text[:50000]

                # Extract og:image URL
                match = _OG_IMAGE_RE.search(html) or _OG_IMAGE_RE_ALT.search(html)
                if not match:
                    logger.info("No og:image found for: %s", article.get("title", "")[:50])
                    continue

                img_url = match.group(1)

                # Download the image
                img_resp = client.get(img_url)
                img_resp.raise_for_status()

                # Determine extension from content-type
                ct = img_resp.headers.get("content-type", "")
                ext = ".jpg"
                if "png" in ct:
                    ext = ".png"
                elif "webp" in ct:
                    ext = ".webp"

                img_path = output_dir / f"og_{i + 1}{ext}"
                img_path.write_bytes(img_resp.content)
                article["og_image"] = str(img_path)
                logger.info("OG image saved: %s", img_path.name)

            except Exception:
                logger.warning("Failed to fetch OG image for: %s", link[:80], exc_info=False)


# ---------------------------------------------------------------------------
# Date filtering
# ---------------------------------------------------------------------------

def _parse_published_date(date_str: str) -> datetime | None:
    """Try to parse various date formats from RSS published fields."""
    if not date_str:
        return None
    # Try RFC 2822 (e.g. "Mon, 12 Jan 2026 11:30:00 GMT")
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        pass
    # Try ISO 8601 (e.g. "2026-05-12T12:30:33-04:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def filter_by_freshness(
    articles: list[dict],
    max_age_hours: int = _MAX_ARTICLE_AGE_HOURS,
    reference_time: datetime | None = None,
) -> list[dict]:
    """Remove articles older than max_age_hours.

    Articles with unparseable dates are kept (benefit of the doubt),
    but logged as warnings.
    """
    if reference_time is None:
        reference_time = datetime.now(timezone.utc)

    fresh = []
    for article in articles:
        pub_date = _parse_published_date(article.get("published", ""))
        if pub_date is None:
            # Can't determine age — keep with warning
            logger.debug("No parseable date, keeping: %s", article.get("title", "")[:60])
            fresh.append(article)
            continue

        # Ensure timezone-aware comparison
        if pub_date.tzinfo is None:
            pub_date = pub_date.replace(tzinfo=timezone.utc)

        age_hours = (reference_time - pub_date).total_seconds() / 3600
        if age_hours <= max_age_hours:
            fresh.append(article)
        else:
            logger.info(
                "Filtered out (%.0fh old): %s",
                age_hours, article.get("title", "")[:60],
            )

    logger.info("Freshness filter: %d → %d articles (max %dh)", len(articles), len(fresh), max_age_hours)
    return fresh


# ---------------------------------------------------------------------------
# LLM-based scoring
# ---------------------------------------------------------------------------

SCORING_SYSTEM_PROMPT_TEMPLATE = """\
あなたは{topic}ニュースの評価者です。小中学生向けYouTubeチャンネル「ポンテのAI教室」のために、
ニュース記事を以下の基準で1-10のスコアで評価してください。

評価基準:
- 話題性（一般の人にも関係あるか）: 1-10
- わかりやすさ（小中学生に説明できるか）: 1-10
- 面白さ（視聴者が興味を持つか）: 1-10

3つの平均を最終スコアとしてください。
日本語のタイトルと50文字以内の要約も生成してください。

JSON形式のみで回答（説明文不要）:
{{"score": 数値, "title_ja": "日本語タイトル", "summary_ja": "50文字以内の要約"}}
"""


def score_articles_with_llm(
    articles: list[dict],
    llm_config: dict,
    topic: str = "AI",
) -> list[dict]:
    """Score and summarize articles using LLM API."""
    from api_utils import LLMClient
    import time

    client = LLMClient(llm_config)
    scored = []
    max_tokens = llm_config.get(llm_config.get("provider", "gemini"), {}).get("max_tokens_scoring", 200)
    scoring_prompt = SCORING_SYSTEM_PROMPT_TEMPLATE.format(topic=topic)

    for idx, article in enumerate(articles):
        if idx > 0:
            time.sleep(0.5)  # Gentle rate limiting

        try:
            user_prompt = (
                f"タイトル: {article['title']}\n"
                f"ソース: {article['source']}\n"
                f"概要: {article['summary']}"
            )
            text = client.generate(scoring_prompt, user_prompt, max_tokens)
            text = text.strip()

            if "{" in text:
                json_str = text[text.index("{"):text.rindex("}") + 1]
                result = json.loads(json_str)
                article["score"] = result.get("score", 0)
                article["title_ja"] = result.get("title_ja", article["title"])
                article["summary_ja"] = result.get("summary_ja", "")
            else:
                article["score"] = 0
                article["title_ja"] = article["title"]
                article["summary_ja"] = ""

        except Exception:
            logger.warning("Scoring failed for: %s", article["title"], exc_info=True)
            article["score"] = 0
            article["title_ja"] = article["title"]
            article["summary_ja"] = ""

        scored.append(article)
        logger.info("  [%d/%d] score=%s: %s", idx + 1, len(articles), article["score"], article["title"][:50])

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Keyword-based fallback scoring (no API required)
# ---------------------------------------------------------------------------

_HIGH_INTEREST_KEYWORDS = [
    "chatgpt", "openai", "google", "apple", "microsoft", "meta",
    "robot", "autonomous", "breakthrough", "launch", "release",
    "gpt", "gemini", "claude", "llm", "generate", "image",
    "child", "school", "education", "game", "app",
]


def score_articles_fallback(articles: list[dict]) -> list[dict]:
    """Score articles using keyword matching (fallback when API is unavailable)."""
    for article in articles:
        title_lower = (article.get("title", "") + " " + article.get("summary", "")).lower()
        score = sum(2 for kw in _HIGH_INTEREST_KEYWORDS if kw in title_lower)
        score = min(score, 10)
        article["score"] = score
        article["title_ja"] = article["title"]
        article["summary_ja"] = article.get("summary", "")[:50]
    articles.sort(key=lambda x: x["score"], reverse=True)
    logger.info("Fallback scoring complete for %d articles", len(articles))
    return articles


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def collect_news(
    config_path: Path = Path("config.json"),
    date_str: str | None = None,
    topic: str = "",
) -> Path:
    """Run the full news collection pipeline. Returns path to output JSON.

    Args:
        topic: Custom topic keyword (e.g. "車", "宇宙"). If empty, uses default AI news.
    """
    config = load_config(config_path)
    news_cfg = config["news"]
    llm_cfg = config.get("llm", {})
    state_dir = Path(config["paths"]["state_dir"])
    state_dir.mkdir(parents=True, exist_ok=True)

    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")

    articles = []

    if topic:
        # Custom topic mode: skip RSS, use Google CSE with topic queries only
        logger.info("Custom topic mode: '%s'", topic)
        cse_cfg = news_cfg.get("google_cse", {})
        cse_api_key = os.environ.get(cse_cfg.get("api_key_env", ""), "")
        cse_engine_id = os.environ.get(cse_cfg.get("engine_id_env", ""), "")
        if cse_api_key and cse_engine_id:
            topic_queries = [
                f"{topic} ニュース 今日",
                f"{topic} 最新ニュース",
                f"{topic} news today",
            ]
            articles = collect_from_google_cse(topic_queries, cse_api_key, cse_engine_id)
        else:
            logger.warning("Google CSE not configured. Cannot collect custom topic news.")
    else:
        # Default AI news mode: RSS + Google CSE
        articles = collect_from_rss(news_cfg["rss_feeds"])

        cse_cfg = news_cfg.get("google_cse", {})
        cse_api_key = os.environ.get(cse_cfg.get("api_key_env", ""), "")
        cse_engine_id = os.environ.get(cse_cfg.get("engine_id_env", ""), "")
        if cse_api_key and cse_engine_id:
            cse_articles = collect_from_google_cse(
                cse_cfg.get("queries", []),
                cse_api_key,
                cse_engine_id,
            )
            articles.extend(cse_articles)
        else:
            logger.info("Google CSE not configured, skipping")

    # 3. Deduplicate
    articles = deduplicate(articles)
    logger.info("Total unique articles: %d", len(articles))

    # 4. Filter by freshness (reject articles older than 48h)
    max_age = news_cfg.get("max_article_age_hours", _MAX_ARTICLE_AGE_HOURS)
    articles = filter_by_freshness(articles, max_age_hours=max_age)

    # Limit before scoring
    max_articles = news_cfg.get("max_articles", 30)
    articles = articles[:max_articles]

    # 5. Score articles with LLM (fallback to keywords on failure)
    topic_label = topic if topic else "AI"
    try:
        scored = score_articles_with_llm(articles, llm_cfg, topic=topic_label)
    except Exception as e:
        logger.warning("LLM scoring failed, using keyword fallback: %s", e)
        scored = score_articles_fallback(articles)

    # 6. Select top N
    top_n = news_cfg.get("top_n", 5)
    selected = scored[:top_n]

    # 7. Fetch OG images for selected articles
    og_dir = state_dir / "og_images"
    fetch_og_images(selected, og_dir)

    # 8. Save
    output = {
        "date": date_str,
        "topic": topic if topic else "AI",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "total_collected": len(scored),
        "selected_count": len(selected),
        "articles": selected,
    }
    output_path = state_dir / f"news_{date_str}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    logger.info("Saved %d articles to %s", len(selected), output_path)
    return output_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="AI News Collector")
    parser.add_argument("--config", default="config.json", help="Config file path")
    parser.add_argument("--date", default=None, help="Date string YYYYMMDD")
    args = parser.parse_args()
    path = collect_news(Path(args.config), args.date)
    print(f"Output: {path}")


if __name__ == "__main__":
    main()
