#!/usr/bin/env python3
"""Six star-field faces for the X3D Background (RGBA PNG; alpha carries brightness).

usage: make_sky.py OUT_DIR [SEED]
Deterministic for a seed. Stars only, no band, so cube seams cannot show.
"""
import os, sys
import numpy as np
from PIL import Image

out = sys.argv[1]; seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20261104
os.makedirs(out, exist_ok=True)
rng = np.random.default_rng(seed)
S = 1024
tints = np.array([[240, 239, 236], [205, 226, 251], [250, 199, 117], [240, 239, 236]], float)
for face in ["front", "back", "left", "right", "top", "bottom"]:
    img = np.zeros((S, S, 4), float)
    n = 2600
    xs, ys = rng.uniform(0, S, n), rng.uniform(0, S, n)
    mag = rng.power(6.0, n)[::-1]          # mostly faint, a few bright
    bright = 0.08 + 0.92 * (1 - mag) ** 3
    sig = 0.55 + 1.1 * bright
    col = tints[rng.choice(4, n, p=[0.62, 0.2, 0.08, 0.1])]
    for x, y, b, s_, c in zip(xs, ys, bright, sig, col):
        r = int(np.ceil(3 * s_))
        x0, x1, y0, y1 = max(0, int(x) - r), min(S, int(x) + r + 1), max(0, int(y) - r), min(S, int(y) + r + 1)
        gx, gy = np.meshgrid(np.arange(x0, x1) + 0.5 - x, np.arange(y0, y1) + 0.5 - y)
        g = b * np.exp(-(gx ** 2 + gy ** 2) / (2 * s_ ** 2))
        img[y0:y1, x0:x1, 3] = np.maximum(img[y0:y1, x0:x1, 3], g)
        img[y0:y1, x0:x1, :3] = np.where(g[..., None] >= img[y0:y1, x0:x1, 3:4] - 1e-9, c, img[y0:y1, x0:x1, :3])
    a = np.clip(img[..., 3] * 0.85, 0, 1)
    rgba = np.dstack([img[..., :3], a * 255]).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(os.path.join(out, f"{face}.png"), optimize=True)
print("ok")
