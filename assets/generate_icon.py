"""Generate icon.png and icon.ico from raw Pillow drawing (no SVG dependency)."""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import sys


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded rect background with gradient (approximated by 2-stop linear)
    radius = int(size * 0.22)
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg)

    # Linear gradient: top-left #5B6CFF -> bottom-right #7B3FF2
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            r = int(0x5B + (0x7B - 0x5B) * t)
            g = int(0x6C + (0x3F - 0x6C) * t)
            b = int(0xFF + (0xF2 - 0xFF) * t)
            bg.putpixel((x, y), (r, g, b, 255))

    # Mask for rounded corners
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    img.paste(bg, (0, 0), mask)

    draw = ImageDraw.Draw(img)

    # Globe-like rings (subtle)
    cx, cy = size // 2, size // 2
    r = int(size * 0.33)
    ring_color = (255, 255, 255, 38)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ring_color, width=max(1, size // 128))
    draw.ellipse((cx - r, cy - r // 2, cx + r, cy + r // 2), outline=ring_color, width=max(1, size // 128))
    draw.ellipse((cx - r // 2, cy - r, cx + r // 2, cy + r), outline=ring_color, width=max(1, size // 128))

    # Two letter cards
    card_w = int(size * 0.30)
    card_h = int(size * 0.36)
    card_y = int(size * 0.27)
    card_radius = int(size * 0.055)

    # Left card "A"
    left_x = int(size * 0.22)
    draw.rounded_rectangle(
        (left_x, card_y, left_x + card_w, card_y + card_h),
        radius=card_radius,
        fill=(255, 255, 255, 240),
    )

    # Right card "語"
    right_x = int(size * 0.625)
    draw.rounded_rectangle(
        (right_x, card_y, right_x + card_w, card_y + card_h),
        radius=card_radius,
        fill=(255, 255, 255, 240),
    )

    # Arrow connector between cards
    arrow_y = card_y + card_h // 2
    arrow_color = (255, 255, 255, 220)
    arrow_x1 = left_x + card_w + int(size * 0.02)
    arrow_x2 = right_x - int(size * 0.02)
    arrow_w = max(2, size // 64)
    draw.line((arrow_x1, arrow_y, arrow_x2, arrow_y), fill=arrow_color, width=arrow_w)
    # Arrow head
    head = int(size * 0.04)
    draw.polygon(
        [(arrow_x2, arrow_y - head), (arrow_x2 + head, arrow_y), (arrow_x2, arrow_y + head)],
        fill=arrow_color,
    )

    # Letters - try to find suitable fonts
    def load_font(candidates: list[str], pixel_size: int) -> ImageFont.ImageFont:
        for name in candidates:
            try:
                return ImageFont.truetype(name, pixel_size)
            except OSError:
                continue
        return ImageFont.load_default()

    latin_font = load_font(["seguibl.ttf", "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"], int(card_h * 0.7))
    cjk_font = load_font(["YuGothB.ttc", "msgothic.ttc", "msyh.ttc", "NotoSansJP-Bold.ttf", "NotoSansCJK-Bold.ttc", "DejaVuSans-Bold.ttf"], int(card_h * 0.6))

    # Center "A"
    bbox = latin_font.getbbox("A")
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    ax = left_x + (card_w - text_w) // 2 - bbox[0]
    ay = card_y + (card_h - text_h) // 2 - bbox[1]
    draw.text((ax, ay), "A", fill=(0x5B, 0x6C, 0xFF, 255), font=latin_font)

    # Center "語"
    bbox = cjk_font.getbbox("語")
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    gx = right_x + (card_w - text_w) // 2 - bbox[0]
    gy = card_y + (card_h - text_h) // 2 - bbox[1]
    draw.text((gx, gy), "語", fill=(0x7B, 0x3F, 0xF2, 255), font=cjk_font)

    return img


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    sizes = [16, 32, 48, 64, 128, 256, 512]
    images = {s: draw_icon(s) for s in sizes}

    # PNG outputs
    images[256].save(out_dir / "icon.png")
    images[512].save(out_dir / "icon-512.png")
    images[128].save(out_dir / "icon-128.png")

    # ICO with multiple sizes
    ico_sizes = [(s, s) for s in [16, 32, 48, 64, 128, 256]]
    images[256].save(out_dir / "icon.ico", format="ICO", sizes=ico_sizes)

    print(f"Generated icons in {out_dir}:")
    for name in ("icon.png", "icon-512.png", "icon-128.png", "icon.ico"):
        path = out_dir / name
        size_kb = path.stat().st_size / 1024
        print(f"  {name}: {size_kb:.1f} KB")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    main(out)
