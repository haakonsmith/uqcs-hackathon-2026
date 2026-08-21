"""Scratch plasma demo - an animated colour field in the terminal.

Kept from early experiments; not part of the map generator.

Usage:
    python plasma.py
"""

from __future__ import annotations

import colorsys
import math
import sys
import time

import blessed


def scale_255(val: float) -> int:
    return int(round(val * 255))


def rgb_at_xy(term: blessed.Terminal, x: int, y: int, t: float) -> tuple[int, int, int]:
    h, w = term.height, term.width
    hue = (
        4.0
        + (math.sin(x / 16.0) + math.sin(y / 32.0) + math.sin(math.sqrt((x - w / 2.0) * (x - w / 2.0) + (y - h / 2.0) * (y - h / 2.0)) / 8.0 + t * 3))
        + math.sin(math.sqrt(x * x + y * y) / 8.0)
    )
    saturation = y / h
    lightness = x / w
    r, g, b = colorsys.hsv_to_rgb(hue / 8.0, saturation, lightness)
    return scale_255(r), scale_255(g), scale_255(b)


def screen_plasma(term: blessed.Terminal, t: float) -> str:
    result = []
    for y in range(term.height - 1):
        for x in range(term.width):
            result.append(term.on_color_rgb(*rgb_at_xy(term, x, y, t)) + " ")
    return "".join(result)


def main() -> None:
    term = blessed.Terminal()
    start = time.time()

    with term.cbreak(), term.hidden_cursor(), term.fullscreen():
        while True:
            if term.inkey(timeout=0.0):
                return
            print(term.home + screen_plasma(term, time.time() - start), end="")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
