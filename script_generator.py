"""Phase 2: 台本生成モジュール.

収集済みニュースデータからJinja2テンプレートを使って
911-plan互換のMarkdown台本を生成する。
LLM API（Gemini/Claude）でテンプレートの可変部分のみを生成（コスト最適化）。
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import re

import jinja2
import pykakasi

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ruby post-processing (auto-annotate kanji with furigana)
# ---------------------------------------------------------------------------

_kks = pykakasi.kakasi()

# Existing ruby pattern: 漢字(ふりがな)
_EXISTING_RUBY_RE = re.compile(r"([\u4e00-\u9fff\u3400-\u4dbf々]+)\([ぁ-ん]+\)")

# Kanji range + 々 (repetition mark)
_KANJI_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf々]+")

# Basic kanji (小1-2 level) that don't need ruby
_BASIC_KANJI = frozenset(
    "一二三四五六七八九十百千万円年月日時分秒上下左右中大小山川田"
    "人口目手足耳本文字学校先生子女男力気天火水金土木花草虫犬猫"
    "石竹糸車音雨空星村町名正出入立休早白青赤玉王何今見行来言"
)


def add_ruby_to_text(text: str) -> str:
    """Add ruby annotations to kanji that don't already have them."""
    # Collect spans that already have ruby (don't touch these)
    protected: list[tuple[int, int]] = []
    for m in _EXISTING_RUBY_RE.finditer(text):
        protected.append((m.start(), m.end()))

    def _is_protected(start: int, end: int) -> bool:
        for ps, pe in protected:
            if start < pe and end > ps:
                return True
        return False

    parts: list[str] = []
    last_end = 0

    for m in _KANJI_RE.finditer(text):
        start, end = m.start(), m.end()
        parts.append(text[last_end:start])

        kanji_word = m.group()

        # Check if this span (or overlapping) is already ruby-annotated
        # Look ahead for '(' to detect existing ruby that spans beyond kanji
        ruby_match = _EXISTING_RUBY_RE.match(text, start)
        if ruby_match:
            parts.append(text[start:ruby_match.end()])
            last_end = ruby_match.end()
            continue

        if _is_protected(start, end):
            parts.append(kanji_word)
            last_end = end
            continue

        # Skip if all basic kanji
        if all(c in _BASIC_KANJI or c == "々" for c in kanji_word):
            parts.append(kanji_word)
            last_end = end
            continue

        # Get reading via pykakasi
        items = _kks.convert(kanji_word)
        reading = "".join(item["hira"] for item in items)

        if reading and reading != kanji_word:
            parts.append(f"{kanji_word}({reading})")
        else:
            parts.append(kanji_word)
        last_end = end

    parts.append(text[last_end:])
    return "".join(parts)


def load_config(config_path: Path = Path("config.json")) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def load_news(news_path: Path) -> dict:
    with open(news_path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# LLM-based script generation
# ---------------------------------------------------------------------------

SCRIPT_SYSTEM_PROMPT_TEMPLATE = """\
あなたは「ポンテ」というかわいいAIロボットキャラクターです。
小中学生向けYouTubeチャンネル「ポンテのAI教室」で{topic}ニュースを紹介します。

ポンテの口調ルール:
- やさしくてわかりやすい言葉を使う
- 「〜だよ！」「〜なんだ！」「〜してみよう！」のような親しみやすい口調
- ちょっとドジなところがある（たまに言い間違えたり）
- 難しい言葉は必ずかんたんな言葉で言い換える
- 専門用語には「むずかしい言葉だけど、つまり〜ってことだよ！」と補足
- 文字数は各セリフ100-150文字程度

ルビ記法ルール:
- 小学3年生以上で習う漢字にはふりがなを付ける
- 形式: 漢字(ふりがな) 例: 人工知能(じんこうちのう)、発表(はっぴょう)
- ひらがな・カタカナ・小1-2の基本漢字にはルビ不要

JSON形式のみで回答（説明文不要）。
"""

# Backward compatibility
SCRIPT_SYSTEM_PROMPT = SCRIPT_SYSTEM_PROMPT_TEMPLATE.format(topic="AI")


def generate_article_scripts(
    articles: list[dict],
    llm_config: dict,
    topic: str = "AI",
) -> dict:
    """Generate intro and explanation scripts for each article."""
    from api_utils import LLMClient

    client = LLMClient(llm_config)
    max_tokens = llm_config.get(llm_config.get("provider", "gemini"), {}).get("max_tokens_script", 1500)
    system_prompt = SCRIPT_SYSTEM_PROMPT_TEMPLATE.format(topic=topic)

    prompt_parts = []
    for i, article in enumerate(articles, 1):
        prompt_parts.append(
            f"ニュース{i}:\n"
            f"  タイトル: {article.get('title_ja', article['title'])}\n"
            f"  要約: {article.get('summary_ja', article.get('summary', ''))}\n"
            f"  ソース: {article.get('source', '')}"
        )

    user_prompt = (
        "以下のニュースそれぞれについて、ポンテの口調で紹介文と解説文を生成してください。\n"
        "また、エンディングのセリフも1つ生成してください。\n"
        "さらに、ニュース全体から重要な専門用語を3〜5個抽出し、keywordsとして返してください。\n"
        "漢字にはルビ記法 漢字(ふりがな) を使ってください。\n\n"
        + "\n\n".join(prompt_parts)
        + '\n\nJSON形式で回答:\n'
        '{\n'
        '  "articles": [\n'
        '    {"intro": "紹介セリフ（100-150文字）", "explanation": "解説セリフ（100-150文字）"}\n'
        '  ],\n'
        '  "ending": "エンディングセリフ（100文字程度）",\n'
        '  "keywords": [\n'
        '    {"term": "用語(ふりがな)", "reading": "ふりがな", "desc": "ポンテ口調の解説（50文字程度）"}\n'
        '  ]\n'
        '}'
    )

    text = client.generate(system_prompt, user_prompt, max_tokens)
    text = text.strip()

    if "{" in text:
        json_str = text[text.index("{"):text.rindex("}") + 1]
        return json.loads(json_str)
    raise ValueError(f"Failed to parse script generation response: {text[:200]}")


# ---------------------------------------------------------------------------
# Fallback script generation (no API required)
# ---------------------------------------------------------------------------

_EXPLANATION_CLOSINGS = [
    "みんなの生活(せいかつ)にも関係(かんけい)してくるかもしれないね！",
    "これからどうなるか楽(たの)しみだね！",
    "知(し)っておくと友達(ともだち)にも教(おし)えてあげられるよ！",
    "ポンテもびっくりしちゃった！",
    "世界(せかい)がどんどん変(か)わっていくね！",
    "みんなはどう思(おも)う？",
    "大人(おとな)もびっくりするニュースだよね！",
    "未来(みらい)がワクワクするね！",
]

_INTRO_VARIATIONS = [
    "次のニュースはこれだよ！{title}っていうニュースなんだ！",
    "続(つづ)いてはこちら！{title}だよ！",
    "お次(つぎ)はこのニュース！{title}！チェックしてみよう！",
    "どんどんいくよー！{title}っていうニュースだよ！",
    "さあ次のニュース！{title}！",
]


_KEYWORD_POOL = [
    {"term": "人工知能(じんこうちのう)", "desc": "コンピュータが人間(にんげん)みたいに考(かんが)える技術(ぎじゅつ)だよ！"},
    {"term": "機械学習(きかいがくしゅう)", "desc": "データからルールを自分(じぶん)で見(み)つける方法(ほうほう)なんだ！"},
    {"term": "アルゴリズム", "desc": "問題(もんだい)を解(と)くための手順(てじゅん)のことだよ！"},
    {"term": "ニューラルネットワーク", "desc": "人間(にんげん)の脳(のう)のしくみをまねしたAIの仕組(しく)みだよ！"},
    {"term": "自然言語処理(しぜんげんごしょり)", "desc": "AIが人間(にんげん)の言葉(ことば)を理解(りかい)する技術(ぎじゅつ)のことだよ！"},
    {"term": "大規模言語(だいきぼげんご)モデル", "desc": "たくさんの文章(ぶんしょう)を学(まな)んで会話(かいわ)できるAIのことだよ！"},
    {"term": "生成(せいせい)AI", "desc": "文章(ぶんしょう)や画像(がぞう)を新(あたら)しく作(つく)り出(だ)せるAIだよ！"},
    {"term": "チャットボット", "desc": "AIが人間(にんげん)みたいにおしゃべりしてくれるプログラムだよ！"},
    {"term": "クラウドコンピューティング", "desc": "インターネット上(じょう)のコンピュータを借(か)りて使(つか)う仕組(しく)みだよ！"},
    {"term": "API", "desc": "いろんなプログラム同士(どうし)がおしゃべりするための約束事(やくそくごと)だよ！"},
    {"term": "データサイエンス", "desc": "たくさんのデータから役立(やくだ)つ情報(じょうほう)を見(み)つける学問(がくもん)だよ！"},
    {"term": "ディープラーニング", "desc": "何層(なんそう)もの計算(けいさん)を重(かさ)ねて学(まな)ぶAIの方法(ほうほう)だよ！"},
    {"term": "IoT", "desc": "家電(かでん)やセンサーがインターネットにつながる仕組(しく)みだよ！"},
    {"term": "ロボティクス", "desc": "ロボットを作(つく)ったり動(うご)かしたりする技術(ぎじゅつ)だよ！"},
    {"term": "音声認識(おんせいにんしき)", "desc": "AIが人(ひと)の声(こえ)を聞(き)き取(と)って文字(もじ)にする技術(ぎじゅつ)だよ！"},
    {"term": "画像認識(がぞうにんしき)", "desc": "AIが写真(しゃしん)や動画(どうが)の中身(なかみ)を理解(りかい)する技術(ぎじゅつ)だよ！"},
    {"term": "プロンプト", "desc": "AIに指示(しじ)を出(だ)すための文章(ぶんしょう)のことだよ！"},
    {"term": "ファインチューニング", "desc": "AIを特定(とくてい)の目的(もくてき)に合(あ)わせて調整(ちょうせい)することだよ！"},
    {"term": "エッジコンピューティング", "desc": "データをクラウドに送(おく)らず手元(てもと)の機器(きき)で処理(しょり)する方法(ほうほう)だよ！"},
    {"term": "オープンソース", "desc": "プログラムの中身(なかみ)を誰(だれ)でも見(み)られるようにすることだよ！"},
    {"term": "自動運転(じどううんてん)", "desc": "AIが車(くるま)を自分(じぶん)で運転(うんてん)してくれる技術(ぎじゅつ)だよ！"},
]


def generate_article_scripts_fallback(articles: list[dict]) -> dict:
    """Generate scripts from article data without API calls."""
    import hashlib

    script_articles = []
    for i, article in enumerate(articles):
        title = article.get("title_ja", article.get("title", "AIニュース"))
        summary = article.get("summary_ja", article.get("summary", ""))[:100]
        # 末尾の句点を除去してから結合（「。。」防止）
        summary = summary.rstrip("。")
        intro = _INTRO_VARIATIONS[i % len(_INTRO_VARIATIONS)].format(title=title)
        closing = _EXPLANATION_CLOSINGS[i % len(_EXPLANATION_CLOSINGS)]
        script_articles.append({
            "intro": intro,
            "explanation": f"{summary}。{closing}",
        })

    # 日付ごとに異なるキーワードを選出（記事タイトルからハッシュでシード生成）
    seed_text = "".join(a.get("title", "") for a in articles)
    seed = int(hashlib.md5(seed_text.encode()).hexdigest(), 16)
    pool = list(_KEYWORD_POOL)
    selected = []
    for _ in range(3):
        idx = seed % len(pool)
        selected.append(pool.pop(idx))
        seed = seed // len(pool) + idx

    return {
        "articles": script_articles,
        "ending": "今日のニュースはここまで！みんな、また明日(あした)ね！チャンネル登録(とうろく)、よろしくね！バイバーイ！",
        "keywords": selected,
    }


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_script(
    news_data: dict,
    script_data: dict,
    template_path: Path,
    date_str: str,
    keywords: list[dict] | None = None,
    **kwargs,
) -> str:
    """Render the final script using Jinja2 template."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_path.parent)),
        autoescape=False,
    )
    template = env.get_template(template_path.name)

    articles = []
    for i, article in enumerate(news_data.get("articles", [])):
        script_articles = script_data.get("articles", [])
        script_item = script_articles[i] if i < len(script_articles) else {}
        articles.append({
            "title_ja": article.get("title_ja", article.get("title", f"ニュース{i+1}")),
            "intro": script_item.get("intro", "このニュースを紹介するよ！"),
            "explanation": script_item.get("explanation", "すごいニュースだね！"),
            "source": article.get("source", ""),
            "link": article.get("link", ""),
        })

    # 金曜日は「また明日」→「また来週」に置換
    from datetime import datetime as _dt
    try:
        _is_friday = _dt.strptime(date_str, "%Y%m%d").weekday() == 4
    except (ValueError, KeyError):
        _is_friday = False

    # Append URL guide to ending if not already present
    ending = script_data.get("ending", "今日のニュースはここまで！また明日ね、バイバーイ！")
    if _is_friday:
        ending = ending.replace("また明日", "また来週(らいしゅう)")
    url_guide = "概要欄(がいようらん)にニュースの元(もと)の記事(きじ)リンクを載(の)せているから、気(き)になった人(ひと)はチェックしてみてね！"
    if "概要欄" not in ending:
        ending = ending.rstrip("！!") + "！" + url_guide

    return template.render(
        articles=articles,
        ending=ending,
        date=date_str,
        keywords=keywords or script_data.get("keywords", []),
        topic=kwargs.get("topic", "AI"),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_script(
    news_path: Path,
    config_path: Path = Path("config.json"),
    script_type: str = "daily",
    topic: str = "",
) -> Path:
    """Generate a script from news data. Returns path to output markdown."""
    config = load_config(config_path)
    llm_cfg = config.get("llm", {})
    state_dir = Path(config["paths"]["state_dir"])
    templates_dir = Path(config["paths"]["templates_dir"])

    news_data = load_news(news_path)
    date_str = news_data.get("date", datetime.now(timezone.utc).strftime("%Y%m%d"))
    topic_label = topic if topic else news_data.get("topic", "AI")

    # Generate scripts via LLM (with fallback)
    try:
        script_data = generate_article_scripts(news_data["articles"], llm_cfg, topic=topic_label)
    except Exception as e:
        logger.warning("LLM script generation failed, using fallback: %s", e)
        script_data = generate_article_scripts_fallback(news_data["articles"])

    # Render with Jinja2 template
    template_name = f"{script_type}_script.j2"
    template_path = templates_dir / template_name
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    script_md = render_script(news_data, script_data, template_path, date_str, topic=topic_label)

    # Apply ruby post-processing: auto-annotate kanji missing furigana
    lines = script_md.split("\n")
    processed_lines = []
    for line in lines:
        if line.startswith("**セリフ**"):
            processed_lines.append(add_ruby_to_text(line))
        else:
            processed_lines.append(line)
    script_md = "\n".join(processed_lines)

    # 二重句点「。。」を「。」に修正
    script_md = script_md.replace("。。", "。")

    # Save
    output_path = state_dir / f"script_{date_str}.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(script_md)

    logger.info("Generated script: %s (%d chars)", output_path, len(script_md))
    return output_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Script Generator")
    parser.add_argument("--news", required=True, help="Path to news JSON file")
    parser.add_argument("--config", default="config.json", help="Config file path")
    parser.add_argument("--type", default="daily", choices=["daily", "weekly"])
    args = parser.parse_args()
    path = generate_script(Path(args.news), Path(args.config), args.type)
    print(f"Output: {path}")


if __name__ == "__main__":
    main()
