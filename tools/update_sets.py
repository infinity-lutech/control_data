#!/usr/bin/env python3
"""Update sets already exported by export_figma_cc.py in <out>/<subject>/<subject>_<stt>/.

Per set (stt N, read from the folder name):
  items_N > ic_focus_off > Text > TEXT fill -> labelColor of id "focus" in data.zip/control_center_config.json
  infomationN "keyword": "a,b,c"            -> keyword ["a", "b", "c"] in <out>/data.json
<out>/data.json is merged: items of the processed sets are inserted/replaced by id, the rest is kept.
A category without ic_cate_* gets "icon": "".

Usage:
  python update_sets.py [--only 1,5-10] [--out DIR] [--dry-run]
"""
import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path

from export_figma_cc import (FIGMA_TOKEN, PAGE_ID, Figma, FatalError, build_data_json, discover_sets,
                             focus_label_color, parse_only, read_info)

CONFIG_NAME = "control_center_config.json"


def disk_sets(out):
    """-> {stt: folder} for every <out>/<cate>/<cate>_<stt>/data.zip."""
    sets = {}
    for zp in out.glob("*/*/data.zip"):
        folder = zp.parent
        m = re.fullmatch(rf"{re.escape(folder.parent.name)}_(\d+)", folder.name)
        if m:
            sets[int(m[1])] = folder
    return sets


def patched_config(zp, color):
    """-> new config bytes, or None if labelColor is already `color`."""
    with zipfile.ZipFile(zp) as z:
        cfg = json.loads(z.read(CONFIG_NAME))
    focus = [i for i in cfg["itemsStyle"] if i.get("id") == "focus"]
    if len(focus) != 1:
        raise ValueError(f'{len(focus)} item id="focus" trong {CONFIG_NAME}')
    if focus[0].get("labelColor") == color:
        return None
    focus[0]["labelColor"] = color
    return (json.dumps(cfg, indent=4, ensure_ascii=False) + "\n").encode("utf-8")


def rewrite_zip(zp, config_bytes):
    tmp = zp.with_name(".data.zip.tmp")
    with zipfile.ZipFile(zp) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            dst.writestr(info, config_bytes if info.filename == CONFIG_NAME else src.read(info))
    os.replace(tmp, zp)


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(here.parent / "control"))
    ap.add_argument("--only", help="stt, ví dụ 2 hoặc 1,5-10")
    ap.add_argument("--dry-run", action="store_true", help="chỉ in kết quả, không ghi gì")
    args = ap.parse_args()

    out = Path(args.out)
    on_disk = disk_sets(out)
    wanted = parse_only(args.only) if args.only else set(on_disk)
    errors = {stt: "không có folder trên disk" for stt in wanted - set(on_disk)}
    wanted &= set(on_disk)
    if not wanted:
        sys.exit("Không có bộ nào để xử lý.")

    figma = Figma(os.environ.get("FIGMA_TOKEN") or FIGMA_TOKEN)
    print("Đọc page...", flush=True)
    page = figma.nodes([PAGE_ID], depth=1)[PAGE_ID]
    sets, figma_errors = discover_sets(page, wanted)
    errors.update(figma_errors)
    details = figma.nodes([p[k]["id"] for p in sets.values() for k in ("info", "items")], depth=4)

    plan = {}  # stt -> {folder, subject, keywords, color, config}
    for stt in sorted(wanted):
        folder = on_disk[stt]
        if stt not in sets:
            errors.setdefault(stt, "không thấy bộ trong Figma")
            continue
        try:
            subject, keywords = read_info(details[sets[stt]["info"]["id"]])
            if subject.lower() != folder.parent.name:
                raise ValueError(f"subject Figma {subject!r} khác folder {folder.parent.name}")
            color = focus_label_color(details[sets[stt]["items"]["id"]])
            config = patched_config(folder / "data.zip", color)
        except (ValueError, KeyError, zipfile.BadZipFile) as e:
            errors[stt] = str(e)
            continue
        plan[stt] = {"folder": folder, "subject": subject, "keywords": keywords, "color": color, "config": config}
        print(f"  stt {stt}: {folder.parent.name}/{folder.name} | labelColor {color}"
              f"{'' if config else ' (giữ nguyên)'} | {len(keywords)} keyword")

    if errors:
        report(plan, errors)
        sys.exit("Có bộ lỗi, dừng trước khi ghi. Xử lý xong rồi chạy lại.")
    if args.dry_run:
        report(plan, errors)
        return

    for p in plan.values():
        if p["config"]:
            rewrite_zip(p["folder"] / "data.zip", p["config"])
    data_json = out / "data.json"
    data_json.write_text(build_data_json(data_json, plan), encoding="utf-8")
    print(f"Đã ghi {data_json}")
    report(plan, errors)


def report(plan, errors):
    print("\nstt | folder | kết quả")
    for stt in sorted(set(plan) | set(errors)):
        folder = plan[stt]["folder"].name if stt in plan else "-"
        print(f"{stt} | {folder} | {'lỗi: ' + errors[stt] if stt in errors else 'OK'}")


if __name__ == "__main__":
    try:
        main()
    except FatalError as e:
        sys.exit(f"Lỗi: {e}")
