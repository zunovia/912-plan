"""表情バリエーション.pngから4表情を個別PNGに切り出すユーティリティ.

スプライトシート（4表情横並びカード）から各キャラクター部分を切り出し、
白背景を透過処理して個別PNGとして保存する。
一度だけ実行すればOK。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image


EXPRESSIONS = ["normal", "surprised", "thinking", "clumsy"]

SPRITE_PATH = Path("assets/ponte/表情バリエーション.png")
OUTPUT_DIR = Path("assets/ponte")


def extract_expressions(
    sprite_path: Path = SPRITE_PATH,
    output_dir: Path = OUTPUT_DIR,
) -> list[Path]:
    """Split the 4-expression sprite sheet into individual PNGs.

    The sprite sheet has a title row at the top and 4 character cards
    arranged horizontally. Each card contains the character above and
    a label below. We crop only the character portion of each card.
    """
    img = Image.open(sprite_path).convert("RGBA")
    w, h = img.size

    # Skip top title and bottom label text
    # Character body occupies roughly y=13%..47% of the image
    # (47% cuts off before the expression label text like "通常", "驚き" etc.)
    top_crop = int(h * 0.13)
    bottom_crop = int(h * 0.47)

    # Small left/right margins to avoid outer border
    left_margin = int(w * 0.02)
    right_margin = int(w * 0.02)
    usable_width = w - left_margin - right_margin

    card_width = usable_width // 4

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    for i, name in enumerate(EXPRESSIONS):
        x0 = left_margin + i * card_width
        x1 = x0 + card_width
        y0 = top_crop
        y1 = bottom_crop

        card = img.crop((x0, y0, x1, y1))

        # Make near-white pixels transparent
        pixels = card.load()
        cw, ch = card.size
        for py in range(ch):
            for px in range(cw):
                r, g, b, a = pixels[px, py]
                # If pixel is very light (near white/light gray), make transparent
                if r > 225 and g > 225 and b > 235 and a > 200:
                    pixels[px, py] = (r, g, b, 0)

        out_path = output_dir / f"ponte_{name}.png"
        card.save(out_path, "PNG")
        paths.append(out_path)
        print(f"Saved: {out_path}")

    return paths


if __name__ == "__main__":
    extract_expressions()
    print("Done! Expression PNGs saved to assets/ponte/")
