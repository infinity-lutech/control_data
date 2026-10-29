#!/usr/bin/env python3
"""Export Control Center sets from Figma into <out>/<subject>/<subject>_<stt>/ (layout of control/animal/animal_1).

Only sets below the text "Link Animation" on page "Control Center" are used. Per set (stt N):
  infomationN -> "subject" text -> folder name
  banner_N    -> banner.webp @2x            bg_N  -> data/bg.webp @1x
  ic_on/ic_off (nearest) -> data/bg_ic_on/off.webp @2x
  "bg" 432x195 next to ic_1 (nearest) -> data/bg_focus.webp @2x
  items_N     -> remaining data/*.webp (see ITEMS)
Every PNG render is saved as lossless WebP; data/control_center_config.json is built from CONFIG.
The 10 .pag in <anim>/<stt>/ (see PAGS) are copied flat into data/, then data/ is zipped to data.zip.

Usage:
  python export_figma_cc.py --only 2 [--out DIR] [--anim DIR] [--dry-run]
Token: set env FIGMA_TOKEN.
"""
import argparse
import copy
import io
import json
import math
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

FIGMA_TOKEN = ""  # set env FIGMA_TOKEN (never commit it)

FILE_KEY = "2OD8WoqtHsqDLi3QqnIcmC"
PAGE_ID = "49:582"
ANCHOR_TEXT = "Link Animation"
MAX_PAIR_DISTANCE = 3000  # px on canvas; rows are ~2600px apart
TEXT_COLOR = "#CCFFFFFF"

# items_N child name -> (how, scale, output name)
#   self:  export the child itself      inner: its single child frame      moon: its "ic_moon" descendant
ITEMS = {
    **{n: ("self", 2, n) for n in (
        "bg_top_left", "bg_top_right", "brightness_bg", "brightness_track", "volume_bg", "volume_track")},
    **{n: ("self", 1, n) for n in (
        "ic_sound_on", "ic_sound_off", "ic_prev", "ic_play", "ic_next", "ic_bright_ness")},
    **{n: ("inner", 1, n) for n in (
        "ic_flash_on", "ic_flash_off", "ic_clock", "ic_calculator", "ic_camera", "ic_recording",
        "ic_record_on", "ic_record_off", "ic_battery", "ic_screenshot", "ic_setting",
        "ic_rotate_on", "ic_rotate_off", "ic_ring_on", "ic_ring_off",
        "ic_wifi_on", "ic_wifi_off", "ic_bluetooth_on", "ic_bluetooth_off",
        "ic_mobile_network_on", "ic_mobile_network_off")},
    "ic_air_plan_on": ("inner", 1, "ic_air_plane_on"),
    "ic_air_plan_off": ("inner", 1, "ic_air_plane_off"),
    "ic_focus_on": ("moon", 1, "ic_focus_on"),
    "ic_focus_off": ("moon", 1, "ic_focus_off"),
}

# <anim>/<stt>/<icon>/<icon>_off|on.pag -> data/<icon>_off|on.pag
PAGS = [f"{i}/{i}_{s}.pag" for i in ("ic_flash", "ic_focus", "ic_record", "ic_ring", "ic_rotate") for s in ("off", "on")]


def _item(id_, off, on=None, row=None, col=None, enable=False):
    d = {"type": "item", "id": id_, "iconStateOffUrl": off}
    if on:
        d["iconStateOnUrl"] = on
    d["backgroundStateOffUrl"] = "bg_ic_off.webp"
    if on:
        d["backgroundStateOnUrl"] = "bg_ic_on.webp"
    if enable:
        d["enable"] = True
    if row is not None:
        d["row"], d["col"] = row, col
    return d


def _group_item(id_, off, on, enable=False):
    d = _item(id_, off, on, enable=enable)
    del d["type"]
    return d


CONFIG = {
    "version": 1,
    "styleName": "",
    "glassTheme": False,
    "backgroundUrl": "bg.webp",
    "backgroundBlur": 0.0,
    "itemsStyle": [
        {"type": "group", "id": "network_group", "backgroundUrl": "bg_top_left.webp", "row": 0, "col": 0, "items": [
            _group_item("cellular", "ic_mobile_network_off.webp", "ic_mobile_network_on.webp"),
            _group_item("airplane", "ic_air_plane_off.webp", "ic_air_plane_on.webp", enable=True),
            _group_item("wifi", "ic_wifi_off.webp", "ic_wifi_on.webp", enable=True),
            _group_item("bluetooth", "ic_bluetooth_off.webp", "ic_bluetooth_on.webp"),
        ]},
        {"type": "media", "id": "media", "backgroundUrl": "bg_top_right.webp",
         "title": "Unknown", "artis": "Unknown", "titleColor": TEXT_COLOR, "artisColor": TEXT_COLOR,
         "icPrev": "ic_prev.webp", "icPlay": "ic_play.webp", "icNext": "ic_next.webp", "row": 0, "col": 2},
        _item("rotate", "ic_rotate_off.webp", "ic_rotate_on.webp", 2, 0, enable=True),
        _item("ringtone", "ic_ring_off.webp", "ic_ring_on.webp", 2, 1, enable=True),
        {"type": "slider", "id": "brightness", "iconUrl": "ic_bright_ness.webp", "iconColor": "#FFFFFF",
         "backgroundUrl": "brightness_bg.webp", "foregroundUrl": "brightness_track.webp", "row": 2, "col": 2},
        {"type": "slider", "id": "volume", "iconUrl": "ic_sound_on.webp", "iconMuteUrl": "ic_sound_off.webp",
         "iconColor": "#FFFFFF", "backgroundUrl": "volume_bg.webp", "foregroundUrl": "volume_track.webp",
         "row": 2, "col": 3},
        {"type": "tile", "id": "focus", "enable": True, "label": "Do Not Disturb", "labelColor": "#FFFFFF",
         "iconStateOffUrl": "ic_focus_off.webp", "iconStateOnUrl": "ic_focus_on.webp",
         "iconBackgroundOffUrl": "bg_ic_off.webp", "iconBackgroundOnUrl": "bg_ic_on.webp",
         "backgroundStateOffUrl": "bg_focus.webp", "row": 3, "col": 0},
        _item("flashlight", "ic_flash_off.webp", "ic_flash_on.webp", 4, 0, enable=True),
        _item("clock", "ic_clock.webp", row=4, col=1),
        _item("calculator", "ic_calculator.webp", row=4, col=2),
        _item("camera", "ic_camera.webp", row=4, col=3),
        _item("recorder", "ic_recording.webp", row=5, col=0),
        _item("screen_recorder", "ic_record_off.webp", "ic_record_on.webp", 5, 1, enable=True),
        _item("battery", "ic_battery.webp", row=5, col=2),
        _item("screenshot", "ic_screenshot.webp", row=5, col=3),
        _item("setting", "ic_setting.webp", row=6, col=0),
    ],
}
EXPECTED_DATA = {f"{v[2]}.webp" for v in ITEMS.values()} | {
    "bg.webp", "bg_ic_on.webp", "bg_ic_off.webp", "bg_focus.webp", "control_center_config.json"}


class FatalError(Exception):
    pass


def http_get(url, headers=None, retries=6):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=180) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                raise FatalError(f"HTTP {e.code} (token/quyền truy cập file?)")
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries - 1:
                raise
            wait = int(e.headers.get("Retry-After") or 0) or 2 ** (attempt + 1)
        except OSError:
            if attempt == retries - 1:
                raise
            wait = 2 ** (attempt + 1)
        print(f"  retry sau {wait}s", flush=True)
        time.sleep(wait)


class Figma:
    def __init__(self, token):
        self.headers = {"X-Figma-Token": token}

    def get(self, path):
        return json.loads(http_get(f"https://api.figma.com/v1/{path}", self.headers))

    def nodes(self, ids, depth):
        out = {}
        for i in range(0, len(ids), 50):
            out.update(self.get(f"files/{FILE_KEY}/nodes?ids={','.join(ids[i:i + 50])}&depth={depth}")["nodes"])
        return {k: v["document"] for k, v in out.items() if v}

    def render(self, ids, scale, batch):
        urls = {}
        for i in range(0, len(ids), batch):
            res = self.get(f"images/{FILE_KEY}?ids={','.join(ids[i:i + batch])}&format=png&scale={scale}")
            if res.get("err"):
                raise RuntimeError(res["err"])
            urls.update(res["images"])
        return urls


def box(n):
    b = n["absoluteBoundingBox"]
    return b["x"], b["y"], b["width"], b["height"]


def dist(a, b):
    return math.hypot(box(a)[0] - box(b)[0], box(a)[1] - box(b)[1])


def parse_only(spec):
    wanted = set()
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        wanted.update(range(int(a), int(b or a) + 1))
    return wanted


def discover_sets(page, wanted):
    """Return ({stt: parts}, errors). parts: info/items/bg/banner/ic_on/ic_off/focus nodes."""
    kids = page["children"]
    anchor = next((n for n in kids if n["type"] == "TEXT" and n.get("characters", "").strip() == ANCHOR_TEXT), None)
    if anchor is None:
        raise FatalError(f'Không thấy text "{ANCHOR_TEXT}"')
    below = [n for n in kids if n.get("absoluteBoundingBox") and box(n)[1] > box(anchor)[1]]

    numbered, loose = {}, {"ic_on": [], "ic_off": [], "focus": []}
    for n in below:
        m = re.fullmatch(r"(items|bg|banner|infomation)_?(\d+)", n["name"].strip())
        if m:
            numbered.setdefault((m[1], int(m[2])), []).append(n)
        elif n["name"] in ("ic_on", "ic_off"):
            loose[n["name"]].append(n)
        elif n["name"] == "bg" and round(box(n)[2]) == 432 and round(box(n)[3]) == 195:
            loose["focus"].append(n)

    sets, errors = {}, {}
    for (kind, stt), cands in sorted(numbered.items(), key=lambda kv: kv[0][1]):
        if kind != "items" or (wanted and stt not in wanted):
            continue
        if len(cands) > 1:
            errors[stt] = f"{len(cands)} frame items_{stt}"
            continue
        items = cands[0]
        parts, missing = {"items": items}, []
        for k in ("bg", "banner", "infomation"):
            c = numbered.get((k, stt), [])
            if len(c) != 1:
                missing.append(f"{k}_{stt} x{len(c)}")
            else:
                parts["info" if k == "infomation" else k] = c[0]
        ref = parts.get("banner", items)
        for k, cands_k in loose.items():
            best = min(cands_k, key=lambda n: dist(n, ref), default=None)
            if best is None or dist(best, ref) > MAX_PAIR_DISTANCE:
                missing.append(f"{k} gần nhất cách {dist(best, ref):.0f}px" if best else f"{k} không có")
            else:
                parts[k] = best
        if missing:
            errors[stt] = "; ".join(missing)
        else:
            sets[stt] = parts
    return sets, errors


def text_of(node):
    return (node.get("characters") or "") + "\n".join(text_of(c) for c in node.get("children", []))


def find(node, name):
    for c in node.get("children", []):
        if c["name"] == name:
            return c
        hit = find(c, name)
        if hit:
            return hit
    return None


def resolve_assets(info, items):
    """-> (subject, [(node_id, scale, relative dest)]); raises ValueError on a broken set."""
    m = re.search(r'"subject"\s*:\s*"([^"]+)"', text_of(info))
    if not m:
        raise ValueError("không đọc được subject")
    by_name = {}
    for c in items.get("children", []):
        by_name.setdefault(c["name"], []).append(c)
    jobs, bad = [], []
    for name, (how, scale, out) in ITEMS.items():
        cands = by_name.get(name, [])
        if len(cands) != 1:
            bad.append(f"{name} x{len(cands)}")
            continue
        node = cands[0]
        if how == "inner":
            inner = [c for c in node.get("children", []) if c.get("visible", True)]
            if len(inner) != 1:
                bad.append(f"{name} có {len(inner)} con")
                continue
            node = inner[0]
        elif how == "moon":
            node = find(node, "ic_moon")
            if node is None:
                bad.append(f"{name} thiếu ic_moon")
                continue
        jobs.append((node["id"], scale, f"data/{out}.webp"))
    if bad:
        raise ValueError("items: " + ", ".join(bad))
    return m[1].split(",")[0].strip(), jobs  # "Love, Heart" -> "Love"


def build_config(style_name):
    cfg = copy.deepcopy(CONFIG)
    cfg["styleName"] = style_name
    return json.dumps(cfg, indent=4, ensure_ascii=False) + "\n"


def save_webp(url, dest):
    with Image.open(io.BytesIO(http_get(url))) as im:
        im = im.convert("RGBA")
        if im.getextrema()[3][0] == 255:  # fully opaque (bg) -> RGB like the hand-made sample
            im = im.convert("RGB")
        im.save(dest, format="WEBP", lossless=True, method=6, exact=True)


def export_set(figma, folder, jobs, pag_dir, workers):
    """Render + download one set into .<name>.tmp, then rename to folder. Returns the error, or None if OK."""
    tmp = folder.with_name(f".{folder.name}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "data").mkdir(parents=True)
    try:
        urls = figma.render([n for n, s, _ in jobs if s == 1], scale=1, batch=100)
        urls.update(figma.render([n for n, s, _ in jobs if s == 2], scale=2, batch=10))
        missing = [rel for nid, _, rel in jobs if not urls.get(nid)]
        if missing:
            raise RuntimeError(f"không render được {missing[0]}")

        def save(job):
            nid, _, rel = job
            try:
                save_webp(urls[nid], tmp / rel)
            except Exception as e:
                raise RuntimeError(f"{rel}: {e}") from e

        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(save, jobs))
        (tmp / "data" / "control_center_config.json").write_text(build_config(folder.name), encoding="utf-8")
        for rel in PAGS:
            shutil.copyfile(pag_dir / rel, tmp / "data" / Path(rel).name)
        # Entries at the zip root: the app unzips straight into themes/<id>/ and expects the config there
        shutil.make_archive(str(tmp / "data"), "zip", root_dir=tmp / "data")
        shutil.rmtree(tmp / "data")
        tmp.rename(folder)
    except Exception as e:  # Ctrl+C is not caught: .tmp stays and the next run cleans it
        shutil.rmtree(tmp, ignore_errors=True)
        return str(e)
    return None


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(here.parent / "control"))
    ap.add_argument("--anim", default=str(here.parent / "Animation Control Center"), help="chứa <stt>/ic_*/*.pag")
    ap.add_argument("--only", help="stt, ví dụ 2 hoặc 1,5-10")
    ap.add_argument("--dry-run", action="store_true", help="chỉ in kết quả ghép bộ, không tải ảnh")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    token = os.environ.get("FIGMA_TOKEN") or FIGMA_TOKEN
    figma, out, anim = Figma(token), Path(args.out), Path(args.anim)
    wanted = parse_only(args.only) if args.only else set()

    print("Đọc page...", flush=True)
    page = figma.nodes([PAGE_ID], depth=1)[PAGE_ID]
    sets, errors = discover_sets(page, wanted)

    details = figma.nodes([p[k]["id"] for p in sets.values() for k in ("info", "items")], depth=4)
    plan = {}  # stt -> (folder, jobs)
    for stt, p in sets.items():
        try:
            subject, jobs = resolve_assets(details[p["info"]["id"]], details[p["items"]["id"]])
        except (ValueError, KeyError) as e:
            errors[stt] = str(e)
            continue
        missing = [rel for rel in PAGS if not (anim / str(stt) / rel).is_file()]
        if missing:
            errors[stt] = f"thiếu {len(missing)} .pag, vd {anim.name}/{stt}/{missing[0]}"
            continue
        jobs += [(p["banner"]["id"], 2, "banner.webp"), (p["bg"]["id"], 1, "data/bg.webp"),
                 (p["ic_on"]["id"], 2, "data/bg_ic_on.webp"), (p["ic_off"]["id"], 2, "data/bg_ic_off.webp"),
                 (p["focus"]["id"], 2, "data/bg_focus.webp")]
        name = f"{subject.lower()}_{stt}"
        plan[stt] = (out / subject.lower() / name, jobs)
        print(f"  stt {stt}: {subject!r} -> {subject.lower()}/{name} | ic_on {p['ic_on']['id']} "
              f"ic_off {p['ic_off']['id']} bg_focus {p['focus']['id']} ({len(jobs)} ảnh)")

    if wanted - set(plan) - set(errors):
        print(f"Không thấy items_N cho stt: {sorted(wanted - set(plan) - set(errors))}")
    if errors:
        report(plan, errors, set(), "chưa chạy")
        sys.exit("Có bộ lỗi, dừng trước khi tải. Xử lý xong rồi chạy lại.")
    existing = [str(f) for f, _ in plan.values() if f.exists()]
    if existing:
        sys.exit("Folder đã tồn tại, dừng (không tải gì):\n  " + "\n  ".join(existing))
    if args.dry_run:
        report(plan, errors, set(), "dry-run")
        return

    done = set()
    for stt in sorted(plan):
        folder, jobs = plan[stt]
        err = export_set(figma, folder, jobs, anim / str(stt), args.workers)
        print(f"stt {stt}: {'lỗi' if err else 'OK'}", flush=True)
        if err:
            errors[stt] = err
            report(plan, errors, done, "chưa chạy")
            sys.exit("Dừng. Xử lý xong rồi chạy tiếp: --only " + ",".join(map(str, sorted(set(plan) - done))))
        done.add(stt)
    report(plan, errors, done, "chưa chạy")


def report(plan, errors, done, pending):
    print("\nstt | folder | kết quả")
    for stt in sorted(set(plan) | set(errors)):
        folder = plan[stt][0].name if stt in plan else "-"
        result = "OK" if stt in done else f"lỗi: {errors[stt]}" if stt in errors else pending
        print(f"{stt} | {folder} | {result}")


if __name__ == "__main__":
    try:
        main()
    except FatalError as e:
        sys.exit(f"Lỗi: {e}")
