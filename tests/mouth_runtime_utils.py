"""嘴型运行时测试共用的临时 SKU 构造（非 test_ 开头，pytest 不收集）。"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / "desktop_pet"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from anim import PetAsset  # noqa: E402
from pet_window import PetWindow  # noqa: E402

BASE_SRC = ROOT / "assets" / "custom" / "bgy_001_pet.png"


def make_sku(tmp: Path, *, mouth: bool, bad: str | None = None):
    d = tmp / ("with" if mouth else "without")
    d.mkdir(parents=True)
    shutil.copyfile(BASE_SRC, d / "pet.png")
    im = Image.open(d / "pet.png").convert("RGBA")
    h, w = np.array(im).shape[:2]
    item = {"stem": "pet", "files": ["pet.png"]}
    bbox = None
    if mouth:
        name = "pet_mouth_smile.png"
        if bad == "size":
            ov = np.zeros((10, 10, 4), np.uint8)
            ov[..., 3] = 255
            Image.fromarray(ov, "RGBA").save(d / name)
        elif bad == "corrupt":
            (d / name).write_bytes(b"not a png")
        elif bad == "missing":
            pass
        elif bad == "transparent":
            Image.fromarray(np.zeros((h, w, 4), np.uint8), "RGBA").save(d / name)
        else:
            ov = np.zeros((h, w, 4), np.uint8)
            x0, x1 = w // 2 - 8, w // 2 + 8
            y0, y1 = int(h * 0.68), int(h * 0.68) + 10
            ov[y0:y1, x0:x1] = (255, 0, 0, 255)
            Image.fromarray(ov, "RGBA").save(d / name)
            bbox = (x0, x1, y0, y1)
        item["mouth_dir"] = name
    (d / "manifest.json").write_text(
        json.dumps({"schema_version": 2, "items": [item]}), encoding="utf-8")
    return d, PetAsset.load(d / "pet.png"), item, bbox


def win(asset: PetAsset, item: dict | None = None, height: int = 320):
    return PetWindow(asset, height, animate=False, blink=True, gaze=True,
                     land=False, hair=False, item=item)
