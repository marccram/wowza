"""Generates WoWZA/icon.tga (64x64, 32-bit) - a warm sparkle on a dark round badge."""
import struct
from pathlib import Path

SIZE, SS = 64, 4  # output size, supersampling factor
OUT = Path(__file__).resolve().parent.parent / "WoWZA" / "icon.tga"


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


def main():
    pixels = bytearray()
    for py in range(SIZE - 1, -1, -1):  # TGA default origin is bottom-left
        for px in range(SIZE):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(SS):
                for sx in range(SS):
                    x = ((px + (sx + 0.5) / SS) / SIZE) * 2 - 1
                    y = ((py + (sy + 0.5) / SS) / SIZE) * 2 - 1
                    r, g, b, a = sample(x, y)
                    acc[0] += r * a; acc[1] += g * a; acc[2] += b * a; acc[3] += a
            n = SS * SS
            a = acc[3] / n
            r, g, b = ((acc[i] / acc[3]) if acc[3] else 0 for i in range(3))
            pixels += bytes((int(b * 255), int(g * 255), int(r * 255), int(a * 255)))  # BGRA
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, SIZE, SIZE, 32, 8)
    OUT.write_bytes(header + pixels)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
