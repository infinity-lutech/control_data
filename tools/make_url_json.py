#!/usr/bin/env python3
"""Write copies of <out>/data.json with absolute URLs (base + relative path) for each host in BASES.

Usage:
  python make_url_json.py [--out DIR]
"""
import argparse
import json
from pathlib import Path

BASES = {
    "data_github.json": "https://raw.githubusercontent.com/infinity-lutech/control_data/main/",
    "data_cdn.json": "https://cdn-storage.lutech.vn/app/orbit_control_center/",
}


def with_base(data, base):
    url = lambda p: base + p if p else ""  # empty icon stays ""
    return [{**c, "icon": url(c["icon"]),
             "items": [{**i, "previewUrl": url(i["previewUrl"]), "resourceUrl": url(i["resourceUrl"])}
                       for i in c["items"]]}
            for c in data]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "control"))
    out = Path(ap.parse_args().out)
    data = json.loads((out / "data.json").read_text(encoding="utf-8"))
    for name, base in BASES.items():
        (out / name).write_text(json.dumps(with_base(data, base), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Đã ghi {out / name}")


if __name__ == "__main__":
    main()
