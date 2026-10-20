import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
TASK = ROOT / "tasks/664f8bbc9e2c4b5582083903bd75035b"
PAGE = TASK / "video_16x9/pages/page_0001.png"
FIGURE = TASK / "translation/assets/p03_figure_01.png"
OUT = Path(__file__).resolve().parent / "output"
OUT.mkdir(exist_ok=True)

IVORY = "#f3f0e8"
INK = "#1d1d24"
VIOLET = "#5947e8"
LIME = "#c9f34a"
MUTED = "#6f6d75"
RULE = "#c9c5ba"
FONT_CN = "/System/Library/Fonts/PingFang.ttc"
FONT_CN_BOLD = "/System/Library/Fonts/STHeiti Medium.ttc"
FONT_EN = "/System/Library/Fonts/Helvetica.ttc"

CN = "MCP-Guard：用于保护代理 AI 中模型上下文协议的多级深度防御框架"
EN = "MCP-Guard: A Multi-Stage Defense-in-Depth Framework for Securing Model Context Protocol in Agentic AI"


def font(size, bold=False, en=False):
    path = FONT_EN if en else (FONT_CN_BOLD if bold else FONT_CN)
    return ImageFont.truetype(path, size=size, index=0)


def wrap(draw, text, fnt, width):
    lines, line = [], ""
    for ch in text:
        trial = line + ch
        if line and draw.textbbox((0, 0), trial, font=fnt)[2] > width:
            lines.append(line)
            line = ch
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def multiline(draw, xy, text, fnt, fill, width, spacing=10, max_lines=None, stroke_width=0):
    lines = wrap(draw, text, fnt, width)
    if max_lines:
        lines = lines[:max_lines]
    draw.multiline_text(
        xy, "\n".join(lines), font=fnt, fill=fill, spacing=spacing,
        stroke_width=stroke_width, stroke_fill=fill,
    )
    box = draw.multiline_textbbox(xy, "\n".join(lines), font=fnt, spacing=spacing)
    return box[3]


def grid(draw, width, height, step=56):
    """Subtle editorial grid shared with the browser cover preview."""
    color = "#e7e3da"
    for x in range(0, width + 1, step):
        draw.line((x, 0, x, height), fill=color, width=1)
    for y in range(0, height + 1, step):
        draw.line((0, y, width, y), fill=color, width=1)


def contain(image, box):
    x, y, w, h = box
    scale = min(w / image.width, h / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    return resized, (round(x + (w-resized.width)/2), round(y + (h-resized.height)/2))


def paper_crop(page, size):
    target_w, target_h = size
    scale = max(target_w/page.width, target_h/page.height)
    p = page.resize((round(page.width*scale), round(page.height*scale)), Image.Resampling.LANCZOS)
    return p.crop((0, 0, target_w, target_h)).convert("RGB")


def rotate_paper(image, angle):
    """Rotate a paper layer without filling its expanded corners."""
    return image.convert("RGBA").rotate(
        angle,
        resample=Image.Resampling.BICUBIC,
        expand=True,
        fillcolor=(0, 0, 0, 0),
    )


def paste_with_shadow(canvas, layer, position, blur=22, opacity=62, offset=(8, 12)):
    """Composite an RGBA layer with a shadow derived from its real alpha shape."""
    x, y = position
    alpha = layer.getchannel("A")
    pad = blur * 3
    padded_alpha = Image.new("L", (layer.width + pad * 2, layer.height + pad * 2), 0)
    padded_alpha.paste(alpha, (pad, pad))
    shadow_alpha = padded_alpha.point(lambda value: value * opacity // 255)
    shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(blur))
    shadow = Image.new("RGBA", padded_alpha.size, (18, 17, 22, 0))
    shadow.putalpha(shadow_alpha)
    canvas.paste(shadow, (x + offset[0] - pad, y + offset[1] - pad), shadow)
    canvas.paste(layer, (x, y), layer)


def portrait():
    im = Image.new("RGB", (1200, 1600), IVORY)
    d = ImageDraw.Draw(im)
    grid(d, 1200, 1600)
    d.line((82, 84, 1118, 84), fill=RULE, width=2)
    d.text((82, 48), "ESSAYPOD", font=font(20, True, True), fill=INK)
    d.text((1068, 48), "3:4", font=font(20, True, True), fill=INK)
    d.ellipse((82, 120, 104, 142), fill=LIME)
    d.text((82, 165), "PAPER EXPLAINED · RESEARCH NOTE", font=font(18, True, True), fill=VIOLET)
    bottom = multiline(d, (82, 205), CN, font(78, True), INK, 1010, 6, 4, stroke_width=1)
    multiline(d, (82, bottom+24), EN, font(25, True, True), MUTED, 990, 7, 3)

    page = Image.open(PAGE).convert("RGB")
    paper = rotate_paper(paper_crop(page, (500, 710)), -2)
    paste_with_shadow(im, paper, (615, 560), blur=22, opacity=62)

    card = (82, 900, 970, 1430)
    d.rounded_rectangle(card, 5, fill="#fbfaf6", outline="#a89ff0", width=2)
    d.text((118, 935), "FIG. 01 · SYSTEM OVERVIEW", font=font(17, True, True), fill=VIOLET)
    fig = Image.open(FIGURE).convert("RGB")
    fitted, pos = contain(fig, (120, 990, 810, 370))
    im.paste(fitted, pos)
    d.rectangle((0, 1492, 1200, 1600), fill=INK)
    d.text((82, 1533), "RESEARCH PAPER", font=font(17, True, True), fill="white")
    d.text((480, 1533), "PAPER INSIGHT", font=font(17, True, True), fill=LIME)
    d.text((890, 1533), "READ · LISTEN · WATCH", font=font(14, True, True), fill="white")
    im.save(OUT / "mcp_guard_cover_3x4.png", quality=95)


def landscape():
    im = Image.new("RGB", (1920, 1080), IVORY)
    d = ImageDraw.Draw(im)
    grid(d, 1920, 1080)
    split = 790
    d.line((split, 0, split, 990), fill=RULE, width=2)
    d.text((70, 62), "ESSAYPOD · PAPER EXPLAINED", font=font(19, True, True), fill=INK)
    d.text((685, 62), "16:9", font=font(19, True, True), fill=INK)
    d.line((70, 105, 720, 105), fill=RULE, width=2)
    d.rectangle((70, 155, 172, 164), fill=LIME)
    d.text((70, 200), "RESEARCH PAPER / VISUAL ABSTRACT", font=font(17, True, True), fill=VIOLET)
    bottom = multiline(d, (70, 250), CN, font(64, True), INK, 650, 8, 5, stroke_width=1)
    multiline(d, (70, bottom+32), EN, font(23, True, True), MUTED, 650, 8, 4)
    d.text((70, 790), "AI", font=font(100, True, True), fill=VIOLET)
    d.text((220, 820), "READ\nLISTEN\nWATCH", font=font(18, True, True), fill=INK, spacing=10)

    page = Image.open(PAGE).convert("RGB")
    paper, _ = contain(page, (0, 0, 555, 900))
    paper = rotate_paper(paper, -2)
    paper_x, paper_y = 1315, 105
    paste_with_shadow(im, paper, (paper_x, paper_y), blur=24, opacity=66)
    d.ellipse((1835, 46, 1857, 68), fill=LIME)
    card = (850, 400, 1745, 860)
    d.rounded_rectangle(card, 5, fill="#fbfaf6", outline="#a89ff0", width=2)
    d.text((885, 435), "ARCHITECTURE · FIGURE 01", font=font(18, True, True), fill=VIOLET)
    fig = Image.open(FIGURE).convert("RGB")
    fitted, pos = contain(fig, (885, 485, 825, 320))
    im.paste(fitted, pos)
    d.rectangle((0, 990, 1920, 1080), fill=INK)
    d.text((70, 1024), "PAPER EXPLAINED", font=font(18, True, True), fill="white")
    d.text((775, 1024), "ESSAYPOD · RESEARCH NOTE", font=font(18, True, True), fill=LIME)
    d.text((1660, 1024), "READ · LISTEN · WATCH", font=font(15, True, True), fill="white")
    im.save(OUT / "mcp_guard_cover_16x9.png", quality=95)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render EssayPod social covers")
    parser.add_argument("--task-dir", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.task_dir:
        TASK = args.task_dir.resolve()
        work_dir = (args.work_dir or TASK / "video_16x9").resolve()
        page_candidate = work_dir / "pages/page_0001.png"
        PAGE = page_candidate if page_candidate.is_file() else TASK / "video_16x9/pages/page_0001.png"
        assets_dir = TASK / "translation/assets"
        figures = sorted(assets_dir.glob("p*_figure_*.png"))
        if not figures:
            figures = sorted(assets_dir.glob("*.png"))
        FIGURE = figures[0] if figures else PAGE
        source_md = TASK / "translation/input.md"
        target_md = TASK / "translation/input.zh.md"
        if source_md.is_file():
            EN = next((line[2:].strip() for line in source_md.read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("# ")), EN)
        if target_md.is_file():
            CN = next((line[2:].strip() for line in target_md.read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("# ")), CN)
        OUT = (args.output_dir or work_dir / "covers").resolve()
        OUT.mkdir(parents=True, exist_ok=True)
    portrait()
    landscape()
    print(OUT)
