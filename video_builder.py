"""Phase 4: 動画生成モジュール.

911-planのgenerate_video.pyを再利用して、
スライド画像＋台本から動画（MP4）を生成する。

台本パースは912-plan独自のslide_generator.parse_slides()を使用する。
911-planの_SERIF_TEXT_RE（lazy match）はネスト括弧（例: 「「Cowork」を...」）で
セリフが最初の」で切断されるバグがあるため、改良版正規表現を持つ
slide_generator.parse_slides()の結果をSlideEntryに変換して使う。
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def load_config(config_path: Path = Path("config.json")) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def setup_engine_path(config: dict) -> Path:
    """Add 911-plan to sys.path and return its directory."""
    engine_dir = Path(config["paths"]["video_engine_dir"]).resolve()
    if not engine_dir.exists():
        raise FileNotFoundError(f"Video engine directory not found: {engine_dir}")
    engine_str = str(engine_dir)
    if engine_str not in sys.path:
        sys.path.insert(0, engine_str)
    return engine_dir


def build_engine_config(config: dict, images_dir: Path, script_path: Path, output_dir: Path) -> Path:
    """Create a temporary config.json for 911-plan's Config.from_json()."""
    engine_config = {
        "tts": {
            "provider": config["tts"]["provider"],
            "language": config["tts"]["language"],
            "voice": config["tts"]["voice"],
            "speaking_rate": config["tts"]["speaking_rate"],
            "pitch": config["tts"]["pitch"],
            "options": config["tts"]["options"],
        },
        "video": config["video"],
        "bgm": {
            **config["bgm"],
            "file": str(Path(config["bgm"]["file"]).resolve()) if config["bgm"].get("file") else "",
        },
        "paths": {
            "images_dir": str(images_dir.resolve()),
            "script_file": str(script_path.resolve()),
            "output_dir": str(output_dir.resolve()),
        },
    }

    # Write temp config next to output dir so relative paths resolve correctly
    tmp_config = output_dir / "_temp_engine_config.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(tmp_config, "w", encoding="utf-8") as f:
        json.dump(engine_config, f, ensure_ascii=False, indent=2)

    return tmp_config


def _slides_to_entries(slides: list[dict], gv_module) -> list:
    """slide_generator.parse_slides()の結果をgv.SlideEntryのリストに変換する.

    slide_generator.parse_slides()は改良版正規表現
    ``r"「((?:[^「」]|「[^」]*」)*)」"`` を使うため、
    ネスト括弧（例: 「「Cowork」を発表」）を正しく抽出できる。
    911-planのparse_script()で使われるlazy matchパターン
    ``r"「(.+?)」"`` は最初の」で停止するためネスト括弧を切断するバグがある。

    Args:
        slides: slide_generator.parse_slides()が返すdictリスト。
                各dictは {"number", "title", "serif", "is_opening",
                          "is_ending", "is_index"} を持つ。
        gv_module: インポート済みのgenerate_videoモジュール。
                   SlideEntryクラスおよび演出指示除去ロジックに使用する。

    Returns:
        gv.SlideEntryのリスト。
    """
    entries = []
    for slide in slides:
        serif = slide["serif"]
        if serif:
            # 括弧内の演出指示を除去（例: 「（3秒の間の後）あなたの...」）
            # 911-planのparse_script()と同じ処理を適用する
            text = re.sub(r"（[^）]*）", "", serif).strip()
            # 英語(カタカナ) → カタカナに置換（VOICEVOXがカタカナを読む）
            # 例: OpenAI(オープンエーアイ) → オープンエーアイ
            text = re.sub(
                r"([A-Za-z0-9][\w\s\-\.]*[A-Za-z0-9]|[A-Za-z0-9])\(([ァ-ヶー]+)\)",
                r"\2", text,
            )
            # 漢字のルビ記法を除去: '人工知能(じんこうちのう)' → '人工知能'
            # TTS（VOICEVOX）は漢字を自力で読めるのでルビ不要
            text = re.sub(r"\([ぁ-ん]+\)", "", text)
            entries.append(
                gv_module.SlideEntry(
                    number=slide["number"],
                    title=slide["title"],
                    text=text,
                    is_silent=False,
                )
            )
        else:
            entries.append(
                gv_module.SlideEntry(
                    number=slide["number"],
                    title=slide["title"],
                    text="",
                    is_silent=True,
                )
            )
    return entries


def build_video(
    date_str: str,
    config_path: Path = Path("config.json"),
    shorts: bool = False,
) -> Path:
    """Build video from slides + script using 911-plan engine.

    Returns path to the generated MP4 file.
    """
    config = load_config(config_path)
    state_dir = Path(config["paths"]["state_dir"])
    output_dir = Path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    script_path = state_dir / f"script_{date_str}.md"
    images_dir = state_dir / ("images_shorts" if shorts else "images")

    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")
    if not images_dir.exists():
        raise FileNotFoundError(f"Images directory not found: {images_dir}")

    # Setup 911-plan import
    engine_dir = setup_engine_path(config)

    # Override resolution for Shorts
    if shorts:
        config = {**config, "video": {
            **config["video"],
            "resolution": config["video"].get("shorts_resolution", [1080, 1920]),
        }}

    # Create engine config
    tmp_config = build_engine_config(config, images_dir, script_path, output_dir)

    try:
        # Import 911-plan modules
        import generate_video as gv

        # Import 912-plan slide_generator for correct nested-bracket parsing.
        # slide_generator uses r"「((?:[^「」]|「[^」]*」)*)」" which handles
        # nested 「」 brackets. gv.parse_script() uses r"「(.+?)」" (lazy match)
        # which stops at the first 」 and truncates serif text in nested cases.
        import slide_generator as sg

        # Load config
        engine_cfg = gv.Config.from_json(tmp_config)

        # Parse script via slide_generator (improved regex), then convert to SlideEntry
        raw_slides = sg.parse_slides(script_path)
        # Shortsではインデックススライドのみスキップ（キーワードは残す）
        if shorts:
            raw_slides = [s for s in raw_slides if not s.get("is_index")]
        entries = _slides_to_entries(raw_slides, gv)
        logger.info("Parsed %d slides from script (via slide_generator)", len(entries))

        # Generate video
        result = gv.process_slides(entries, engine_cfg)

        if result is None:
            raise RuntimeError("Video generation returned None")

        # Rename to dated filename
        suffix = "shorts" if shorts else "daily"
        final_path = output_dir / f"{date_str}_{suffix}.mp4"
        if result != final_path:
            if final_path.exists():
                final_path.unlink()
            result.rename(final_path)

        logger.info("Video generated: %s", final_path)
        return final_path

    finally:
        # Cleanup temp config
        if tmp_config.exists():
            tmp_config.unlink()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Video Builder")
    parser.add_argument("--date", required=True, help="Date string YYYYMMDD")
    parser.add_argument("--config", default="config.json", help="Config file path")
    parser.add_argument("--shorts", action="store_true", help="Build Shorts (9:16) video")
    args = parser.parse_args()
    path = build_video(args.date, Path(args.config), args.shorts)
    print(f"Output: {path}")


if __name__ == "__main__":
    main()
