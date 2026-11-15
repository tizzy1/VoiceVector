"""Fetch real ATC transcripts (text only) from UWB-ATCC for the parser model.

    .venv/bin/python -m tools.train.fetch_uwb

Reads ONLY the `id` and `text` columns of the dataset's parquet files over HTTP (column
projection), so the ~700 MB of audio is never downloaded. Writes data/train/raw.jsonl:
{"id", "rec_id", "role", "text"}. Role from the id suffix: _AT controller, _PI pilot, PIAT
mixed (dropped). On any network/auth failure it prints why and exits 0; the pipeline then
trains on synthetic lines only and the report says so.

Licence: UWB-ATCC, CC BY-NC-SA 4.0 — Šmídl et al., University of West Bohemia.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "train"
REPO = "Jzuluaga/uwb_atcc"
FILES = ["data/train-00000-of-00002-4a9602acfde9f517.parquet",
         "data/train-00001-of-00002-91082fb03180a296.parquet",
         "data/test-00000-of-00001-3a021115ca23c2a5.parquet"]
LICENSE = "UWB-ATCC CC BY-NC-SA 4.0 (Šmídl et al., University of West Bohemia)"


def role_of(seg_id: str) -> str | None:
    up = seg_id.upper()
    if "PIAT" in up or "ATPI" in up:
        return None
    if re.search(r"_AT(?:_|$|\d)", up) or up.endswith("AT"):
        return "controller"
    if re.search(r"_PI(?:_|$|\d)", up) or up.endswith("PI"):
        return "pilot"
    return None


def rec_of(seg_id: str) -> str:
    """Recording id = the id without its trailing segment index / role / time parts."""
    parts = re.split(r"[_-]", seg_id)
    keep = [p for p in parts if not re.fullmatch(r"\d+|AT|PI|PIAT|ATPI|\d+\.\d+", p.upper())]
    return "_".join(keep) or seg_id


def main() -> int:
    try:
        import fsspec
        import pyarrow.parquet as pq
    except ImportError as e:
        print(f"fetch_uwb: missing dependency ({e}); continuing with synthetic data only")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    try:
        fs = fsspec.filesystem("http")
        for f in FILES:
            url = f"https://huggingface.co/datasets/{REPO}/resolve/main/{f}"
            with fs.open(url, "rb", block_size=1 << 20) as fh:
                pf = pq.ParquetFile(fh)
                print(f, "columns:", pf.schema_arrow.names, "rows:", pf.metadata.num_rows)
                table = pf.read(columns=["id", "text"])
                rows += table.to_pylist()
    except Exception as e:                                   # network, auth, format
        print(f"fetch_uwb: download failed ({type(e).__name__}: {e}); "
              f"continuing with synthetic data only")
        return 0
    out, roles = [], Counter()
    for r in rows:
        role = role_of(str(r["id"]))
        roles[role or "mixed/unknown"] += 1
        if role is None or not str(r.get("text") or "").strip():
            continue
        out.append({"id": r["id"], "rec_id": rec_of(str(r["id"])), "role": role,
                    "text": str(r["text"]).strip()})
    with (OUT / "raw.jsonl").open("w") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("segments:", len(rows), "| by role:", dict(roles))
    for r in out[:5]:
        print("  example:", r["id"], "->", r["rec_id"], r["role"], "|", r["text"][:80])
    print(f"wrote {len(out)} lines to {OUT / 'raw.jsonl'}  ({LICENSE})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
