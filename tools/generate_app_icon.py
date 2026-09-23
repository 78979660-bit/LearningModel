from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
SIZE = 1024


def scaled(points):
    return tuple(tuple(round(value * SIZE / 256) for value in point) for point in points)


def main() -> None:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = round(8 * SIZE / 256)
    radius = round(54 * SIZE / 256)
    for y in range(margin, SIZE - margin):
        ratio = (y - margin) / (SIZE - 2 * margin)
        color = tuple(
            round(start + (end - start) * ratio)
            for start, end in zip((20, 58, 104), (37, 99, 235))
        ) + (255,)
        draw.rounded_rectangle(
            (margin, margin, SIZE - margin, SIZE - margin),
            radius=radius,
            fill=color,
        )

    left_page = scaled(((55, 143), (83, 138), (105, 143), (128, 155), (128, 216), (103, 204), (79, 201), (55, 205)))
    right_page = scaled(((201, 143), (173, 138), (151, 143), (128, 155), (128, 216), (153, 204), (177, 201), (201, 205)))
    draw.polygon(left_page, fill=(248, 250, 252, 255))
    draw.polygon(right_page, fill=(224, 242, 254, 255))

    line_width = round(7 * SIZE / 256)
    draw.line(scaled(((128, 155), (128, 216))), fill=(56, 189, 248, 255), width=line_width)
    draw.line(scaled(((68, 160), (88, 157), (104, 160), (117, 167))), fill=(56, 189, 248, 255), width=line_width)
    draw.line(scaled(((188, 160), (168, 157), (152, 160), (139, 167))), fill=(56, 189, 248, 255), width=line_width)

    network_width = round(9 * SIZE / 256)
    draw.line(scaled(((83, 111), (128, 79), (173, 111))), fill=(186, 230, 253, 255), width=network_width, joint="curve")
    draw.line(scaled(((128, 79), (128, 127))), fill=(186, 230, 253, 255), width=network_width)
    for x, y, radius_unit in ((83, 111, 15), (128, 79, 17), (173, 111, 15)):
        radius_px = round(radius_unit * SIZE / 256)
        cx = round(x * SIZE / 256)
        cy = round(y * SIZE / 256)
        draw.ellipse(
            (cx - radius_px, cy - radius_px, cx + radius_px, cy + radius_px),
            fill=(248, 250, 252, 255),
        )

    png_path = ROOT / "packaging" / "app-icon-1024.png"
    ico_path = ROOT / "study_app" / "ui" / "assets" / "app.ico"
    image.save(png_path, format="PNG", optimize=True)
    image.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()
