"""Generates WoWZA/icon.tga (the addon icon) and companion/wowza.ico (the app icon):
a warm sparkle on a dark round badge."""
import struct
import zlib
from pathlib import Path

SIZE, SS = 64, 4  # addon icon size, supersampling factor
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "WoWZA" / "icon.tga"
ICO = ROOT / "companion" / "wowza.ico"


def lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(len(a)))


def sample(x, y):
    """RGBA (0-1 floats) at a point in [-1, 1] space."""
    r = (x * x + y * y) ** 0.5
    if r > 0.97:
        return (0, 0, 0, 0)
    # badge: radial gradient, warm center to dark edge, thin gold rim
    color = lerp((0.55, 0.24, 0.13), (0.12, 0.06, 0.05), min(r / 0.97, 1) ** 1.4)
    if r > 0.88:
        color = (0.80, 0.62, 0.30)
    # four-point sparkle (astroid), plus a small one top-right
    for cx, cy, size in ((0.0, 0.0, 0.70), (0.42, -0.42, 0.24)):
        dx, dy = abs(x - cx) / size, abs(y - cy) / size
        v = dx ** (2 / 3) + dy ** (2 / 3)
        if v <= 1:
            glow = 1 - v
            color = lerp((1.0, 0.70, 0.40), (1.0, 0.97, 0.88), min(glow * 2.2, 1))
    return (*color, 1.0)


def render(size):
    """Rows of RGBA pixels (top row first) at the given size."""
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(SS):
                for sx in range(SS):
                    x = ((px + (sx + 0.5) / SS) / size) * 2 - 1
                    y = ((py + (sy + 0.5) / SS) / size) * 2 - 1
                    r, g, b, a = sample(x, y)
                    acc[0] += r * a; acc[1] += g * a; acc[2] += b * a; acc[3] += a
            a = acc[3] / (SS * SS)
            r, g, b = ((acc[i] / acc[3]) if acc[3] else 0 for i in range(3))
            row += bytes((int(r * 255), int(g * 255), int(b * 255), int(a * 255)))
        rows.append(bytes(row))
    return rows


def png(size):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(bytes(1) + row for row in render(size))  # filter byte 0 before each row
    return (bytes([0x89]) + b"PNG\r\n" + bytes([0x1A]) + b"\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    # Addon icon: 64x64 32-bit TGA, bottom-left origin, BGRA.
    pixels = bytearray()
    for row in reversed(render(SIZE)):
        for i in range(0, len(row), 4):
            r, g, b, a = row[i:i + 4]
            pixels += bytes((b, g, r, a))
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, SIZE, SIZE, 32, 8)
    OUT.write_bytes(header + pixels)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")

    # Companion icon: .ico holding PNG images, for the exe and its window.
    sizes = (16, 24, 32, 48, 64, 256)
    images = [png(s) for s in sizes]
    entries, offset = b"", 6 + 16 * len(sizes)
    for s, img in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
    ICO.write_bytes(struct.pack("<HHH", 0, 1, len(sizes)) + entries + b"".join(images))
    print(f"wrote {ICO} ({ICO.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
