"""Phase 3: スライド画像生成モジュール.

台本MarkdownをパースしてJinja2 HTMLテンプレートでスライドを生成し、
Playwrightで1920x1080のPNG画像にレンダリングする。
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

import base64

import jinja2

logger = logging.getLogger(__name__)

# Expression image names and mapping
EXPRESSIONS = ["normal", "surprised", "thinking", "clumsy"]

_WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]
_WEEKDAY_CLASSES = ["day-mon", "day-tue", "day-wed", "day-thu", "day-fri", "day-sat", "day-sun"]

# ルビ記法:
#   漢字(ふりがな) → <ruby>漢字<rt>ふりがな</rt></ruby>
#   English(カタカナ) → <ruby>English<rt>カタカナ</rt></ruby>
_RUBY_RE = re.compile(
    r"([\u4e00-\u9fff\u3400-\u4dbf々]+)\(([ぁ-んァ-ヶー]+)\)"
    r"|"
    r"([A-Za-z0-9][\w\s\-\.]*[A-Za-z0-9]|[A-Za-z0-9])\(([ァ-ヶー]+)\)"
)

# キーワード箇条書き: - **term**: desc
_KEYWORD_ITEM_RE = re.compile(r"^-\s+\*\*(.+?)\*\*:\s*(.+)$", re.MULTILINE)


def format_date_display(date_str: str) -> str:
    """Convert YYYYMMDD to '2026年5月12日（月）' format."""
    from datetime import datetime
    dt = datetime.strptime(date_str, "%Y%m%d")
    weekday = _WEEKDAYS_JA[dt.weekday()]
    return f"{dt.year}年{dt.month}月{dt.day}日（{weekday}）"


def get_day_class(date_str: str) -> str:
    """Return CSS class for the day of the week (e.g. 'day-mon')."""
    from datetime import datetime
    try:
        dt = datetime.strptime(date_str, "%Y%m%d")
        return _WEEKDAY_CLASSES[dt.weekday()]
    except (ValueError, KeyError):
        return "day-mon"


def is_friday(date_str: str) -> bool:
    """Check if the given YYYYMMDD date is a Friday."""
    from datetime import datetime
    try:
        return datetime.strptime(date_str, "%Y%m%d").weekday() == 4
    except (ValueError, KeyError):
        return False


def apply_ruby(text: str) -> str:
    """Convert ruby notation to HTML <ruby> tags.

    Supports:
      漢字(ふりがな) → <ruby>漢字<rt>ふりがな</rt></ruby>
      English(カタカナ) → <ruby>English<rt>カタカナ</rt></ruby>
    """
    def _replace(m):
        if m.group(1):  # kanji + hiragana
            return f"<ruby>{m.group(1)}<rt>{m.group(2)}</rt></ruby>"
        else:  # English + katakana
            return f"<ruby>{m.group(3)}<rt>{m.group(4)}</rt></ruby>"
    return _RUBY_RE.sub(_replace, text)


_RUBY_EXTRACT_RE = re.compile(
    r"([\u4e00-\u9fff\u3400-\u4dbf々]+)\(([ぁ-んァ-ヶー]+)\)"
    r"|"
    r"([A-Za-z0-9][\w\s\-\.]*[A-Za-z0-9]|[A-Za-z0-9])\(([ァ-ヶー]+)\)"
)


def _extract_ruby_dict(text: str) -> dict[str, str]:
    """セリフからルビ記法を抽出して {漢字: ふりがな, English: カタカナ} 辞書を返す."""
    ruby_dict: dict[str, str] = {}
    for m in _RUBY_EXTRACT_RE.finditer(text):
        if m.group(1):  # 漢字(ふりがな or カタカナ)
            ruby_dict[m.group(1)] = m.group(2)
        elif m.group(3):  # English(カタカナ)
            ruby_dict[m.group(3)] = m.group(4)
    return ruby_dict


def _apply_ruby_dict(text: str, ruby_dict: dict[str, str]) -> str:
    """タイトルテキストにルビ辞書を適用する。長いキーから順に置換."""
    for word in sorted(ruby_dict, key=len, reverse=True):
        if word in text:
            text = text.replace(word, f"{word}({ruby_dict[word]})")
    return text


def _parse_keyword_items(body: str) -> list[dict]:
    """Parse markdown bullet list '- **term**: desc' into list of dicts."""
    items = []
    for m in _KEYWORD_ITEM_RE.finditer(body):
        items.append({"term": m.group(1).strip(), "desc": m.group(2).strip()})
    return items


# 911-plan互換の正規表現
_SLIDE_HEADER_RE = re.compile(r"^###\s+スライド(\d+)[：:]\s*(.+)$", re.MULTILINE)
_SERIF_TEXT_RE = re.compile(r"「((?:[^「」]|「[^」]*」)*)」", re.DOTALL)


def load_config(config_path: Path = Path("config.json")) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def parse_slides(script_path: Path) -> list[dict]:
    """Parse script markdown into slide data."""
    text = script_path.read_text(encoding="utf-8")

    # Strip narration section if present
    narration_marker = "## 語りパート"
    if narration_marker in text:
        text = text[:text.index(narration_marker)]

    headers = list(_SLIDE_HEADER_RE.finditer(text))
    if not headers:
        raise ValueError(f"No slide headers found in {script_path}")

    slides = []
    for i, match in enumerate(headers):
        number = int(match.group(1))
        title = match.group(2).strip()
        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[start:end]

        serif_match = _SERIF_TEXT_RE.search(body)
        serif = serif_match.group(1) if serif_match else ""

        slides.append({
            "number": number,
            "title": title,
            "serif": serif,
            "body": body.strip(),
            "is_opening": "オープニング" in title,
            "is_ending": "エンディング" in title,
            "is_index": "ニュース一覧" in title,
            "is_keyword": "今日のキーワード" in title,
        })

    return slides


def classify_slide(slide: dict, total_slides: int) -> str:
    """Determine slide type for template rendering."""
    if slide["is_opening"]:
        return "opening"
    if slide["is_index"]:
        return "index"
    if slide.get("is_keyword"):
        return "keyword"
    if slide["is_ending"]:
        return "ending"
    return "news"


def _load_ponte_expressions(config: dict) -> dict[str, str]:
    """Load all 4 expression PNGs as base64 data URIs. Returns dict keyed by expression name."""
    assets_dir = Path(config["paths"]["assets_dir"]).resolve()
    ponte_dir = assets_dir / "ponte"
    expressions = {}
    for name in EXPRESSIONS:
        img_path = ponte_dir / f"ponte_{name}.png"
        if img_path.exists():
            img_data = base64.b64encode(img_path.read_bytes()).decode()
            expressions[name] = f"data:image/png;base64,{img_data}"
        else:
            logger.warning("Expression image not found: %s", img_path)
    return expressions


def _get_expression(slide_type: str, news_counter: int) -> str:
    """Determine which expression to use based on slide type and news counter.

    Mapping:
    - opening / index / keyword: normal
    - news (odd counter = intro): surprised
    - news (even counter = explanation): thinking
    - ending: clumsy
    """
    if slide_type == "ending":
        return "clumsy"
    if slide_type == "news":
        return "surprised" if news_counter % 2 == 1 else "thinking"
    return "normal"


def render_slide_html(
    slide: dict,
    slide_type: str,
    template_path: Path,
    config: dict,
    news_number: int = 0,
    index_items: list[str] | None = None,
    keyword_items: list[dict] | None = None,
    bg_variant: str = "",
    ponte_expressions: dict[str, str] | None = None,
    news_counter: int = 0,
    og_image: str = "",
) -> str:
    """Render a single slide to HTML."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_path.parent)),
        autoescape=False,
    )
    template = env.get_template(template_path.name)

    channel = config.get("channel", {})

    # Select expression-specific image if available, else fall back to ponte.png
    expression = _get_expression(slide_type, news_counter)
    if ponte_expressions and expression in ponte_expressions:
        ponte_image = ponte_expressions[expression]
    else:
        assets_dir = Path(config["paths"]["assets_dir"]).resolve()
        ponte_image_path = assets_dir / "ponte" / "ponte.png"
        if ponte_image_path.exists():
            img_data = base64.b64encode(ponte_image_path.read_bytes()).decode()
            ponte_image = f"data:image/png;base64,{img_data}"
        else:
            ponte_image = ""

    date_str = config.get("_date_display", config.get("_date", ""))

    # セリフ中のルビ記法を辞書化 → タイトルの漢字に適用（手修正を優先）
    from script_generator import add_ruby_to_text
    serif_text = slide["serif"][:300] if slide["serif"] else ""
    ruby_dict = _extract_ruby_dict(serif_text)
    title_with_ruby = _apply_ruby_dict(slide["title"], ruby_dict)
    title_html = apply_ruby(add_ruby_to_text(title_with_ruby))
    text_html = apply_ruby(serif_text)

    # Apply ruby to keyword items
    rendered_keywords = []
    if keyword_items:
        for kw in keyword_items:
            rendered_keywords.append({
                "term": apply_ruby(kw["term"]),
                "desc": apply_ruby(kw["desc"]),
            })

    # 金曜日は「また来週ね！」にする
    ending_message = "また来週(らいしゅう)ね！" if is_friday(config.get("_date", "")) else "また明日ね！"
    ending_message_html = apply_ruby(ending_message)

    # 曜日テーマクラス
    day_class = get_day_class(config.get("_date", ""))

    return template.render(
        slide_type=slide_type,
        title=title_html,
        text=text_html,
        news_number=news_number,
        channel_name=channel.get("name", "ポンテのAI教室"),
        tagline=channel.get("tagline", "むずかしいAIを、かんたんに。"),
        date=date_str,
        ponte_image=ponte_image,
        index_heading="TODAY'S AI NEWS",
        index_items=index_items or [],
        keyword_items=rendered_keywords,
        bg_variant=bg_variant,
        og_image=og_image,
        ending_message=ending_message_html,
        day_class=day_class,
    )


async def render_html_to_png(
    html_content: str,
    output_path: Path,
    width: int = 1920,
    height: int = 1080,
) -> None:
    """Render HTML string to PNG using Playwright."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": width, "height": height})
        await page.set_content(html_content, wait_until="networkidle")
        await page.screenshot(path=str(output_path), full_page=False)
        await browser.close()


async def generate_slides_async(
    script_path: Path,
    config_path: Path = Path("config.json"),
    date_str: str = "",
    shorts: bool = False,
) -> Path:
    """Generate all slide images from a script. Returns images directory."""
    from playwright.async_api import async_playwright

    config = load_config(config_path)
    state_dir = Path(config["paths"]["state_dir"])
    templates_dir = Path(config["paths"]["templates_dir"])

    if shorts:
        template_path = templates_dir / "slide_shorts.html"
        resolution = config.get("video", {}).get("shorts_resolution", [1080, 1920])
    else:
        template_path = templates_dir / "slide.html"
        resolution = config.get("video", {}).get("resolution", [1920, 1080])

    if date_str:
        config["_date"] = date_str
    else:
        # Extract from script filename
        stem = script_path.stem
        config["_date"] = stem.replace("script_", "")

    # Formatted date for display (e.g. "2026年5月13日（水）")
    try:
        config["_date_display"] = format_date_display(config["_date"])
    except (ValueError, KeyError):
        config["_date_display"] = config["_date"]

    slides = parse_slides(script_path)
    images_dir = state_dir / ("images_shorts" if shorts else "images")
    images_dir.mkdir(parents=True, exist_ok=True)

    # Shortsではインデックススライドのみスキップ（キーワードは残す）
    if shorts:
        slides = [s for s in slides if not s.get("is_index")]

    # Load expression images once
    ponte_expressions = _load_ponte_expressions(config)

    # Load OG images from news JSON (keyed by 1-based article index)
    og_images: dict[int, str] = {}
    date_key = config.get("_date", date_str)
    news_json_path = state_dir / f"news_{date_key}.json"
    if news_json_path.exists():
        import json as _json
        with open(news_json_path, encoding="utf-8") as f:
            news_data = _json.load(f)
        for idx, article in enumerate(news_data.get("articles", []), 1):
            og_path = article.get("og_image", "")
            if og_path:
                og_file = Path(og_path)
                if og_file.exists():
                    img_data = base64.b64encode(og_file.read_bytes()).decode()
                    suffix = og_file.suffix.lstrip(".")
                    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(suffix, "jpeg")
                    og_images[idx] = f"data:image/{mime};base64,{img_data}"

    # インデックススライド用: ニュースタイトル一覧を事前抽出（セリフのルビを優先）
    from script_generator import add_ruby_to_text
    news_titles = []
    for s in slides:
        if (not s["is_opening"] and not s["is_ending"] and not s["is_index"]
                and not s.get("is_keyword")
                and "のポイント" not in s["title"]):
            rd = _extract_ruby_dict(s["serif"])
            t = _apply_ruby_dict(s["title"], rd)
            news_titles.append(apply_ruby(add_ruby_to_text(t)))

    news_counter = 0
    # Track which article number we're on (each article = 2 slides: intro + point)
    article_index = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(
            viewport={"width": resolution[0], "height": resolution[1]},
        )

        for slide in slides:
            slide_type = classify_slide(slide, len(slides))
            if slide_type == "news":
                news_counter += 1
                # Odd = intro slide (1,3,5...), even = explanation slide (2,4,6...)
                if news_counter % 2 == 1:
                    article_index += 1

            # Background variant for news slides (cycles through 5 colors)
            bg_variant = f"bg-{(news_counter - 1) % 5 + 1}" if slide_type == "news" else ""

            # OG image for this article (show on both intro and explanation slides)
            og_image = og_images.get(article_index, "") if slide_type == "news" else ""

            # Parse keyword items from markdown body for keyword slides
            kw_items = None
            if slide_type == "keyword":
                kw_items = _parse_keyword_items(slide.get("body", ""))

            html = render_slide_html(
                slide, slide_type, template_path, config, news_counter,
                index_items=news_titles if slide_type == "index" else None,
                keyword_items=kw_items,
                bg_variant=bg_variant,
                ponte_expressions=ponte_expressions,
                news_counter=news_counter,
                og_image=og_image,
            )

            await page.set_content(html, wait_until="networkidle")
            output_path = images_dir / f"slide_{slide['number']}.png"
            await page.screenshot(path=str(output_path), full_page=False)
            logger.info("Generated slide: %s", output_path)

        await browser.close()

    logger.info("Generated %d slides in %s", len(slides), images_dir)
    return images_dir


def generate_slides(
    script_path: Path,
    config_path: Path = Path("config.json"),
    date_str: str = "",
    shorts: bool = False,
) -> Path:
    """Synchronous wrapper for slide generation."""
    import asyncio
    return asyncio.run(generate_slides_async(script_path, config_path, date_str, shorts))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Slide Generator")
    parser.add_argument("--script", required=True, help="Path to script markdown")
    parser.add_argument("--config", default="config.json", help="Config file path")
    parser.add_argument("--date", default="", help="Date string for display")
    parser.add_argument("--shorts", action="store_true", help="Generate Shorts (9:16) slides")
    args = parser.parse_args()
    path = generate_slides(Path(args.script), Path(args.config), args.date, args.shorts)
    print(f"Output: {path}")


if __name__ == "__main__":
    main()
