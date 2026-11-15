"""Train the CRF instruction tagger and write models/crf.pkl + data/train/report.json.

    .venv/bin/python -m tools.train.train_crf

Every number in the report comes from this script (span F1 = exact span AND label match).
"""
from __future__ import annotations

import json
import pickle
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.voice.ml_tagger import (MLTagger, bio_to_spans, load_lexicon,  # noqa: E402
                                    token_features)
from server.voice.normalise import normalise_tokens  # noqa: E402

DATA = ROOT / "data" / "train"
MODEL = ROOT / "models" / "crf.pkl"
GOLDEN_REAL = ROOT / "data" / "golden_real.jsonl"
LICENSE = "UWB-ATCC CC BY-NC-SA 4.0 (Šmídl et al., University of West Bohemia)"


def load(name: str) -> list[dict]:
    p = DATA / name
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def span_f1(gold: list[list[str]], pred: list[list[str]]) -> tuple[float, dict[str, float]]:
    tp, fp, fn = defaultdict(int), defaultdict(int), defaultdict(int)
    for g, p in zip(gold, pred):
        gs, ps = set(bio_to_spans(g)), set(bio_to_spans(p))
        for s in ps & gs:
            tp[s[0]] += 1
        for s in ps - gs:
            fp[s[0]] += 1
        for s in gs - ps:
            fn[s[0]] += 1

    def f1(t: int, f_p: int, f_n: int) -> float:
        return 0.0 if t == 0 else 2 * t / (2 * t + f_p + f_n)
    labels = sorted(set(tp) | set(fp) | set(fn))
    overall = f1(sum(tp.values()), sum(fp.values()), sum(fn.values()))
    return round(overall, 4), {lab: round(f1(tp[lab], fp[lab], fn[lab]), 4) for lab in labels}


def main() -> int:
    try:
        import sklearn_crfsuite
    except ImportError:
        print("train_crf: sklearn-crfsuite not installed (pip install sklearn-crfsuite)")
        return 1
    lex = load_lexicon()
    train, dev, dev_real = load("train.jsonl"), load("dev.jsonl"), load("dev_real.jsonl")
    if not train:
        print("train_crf: no training data; run tools.train.weak_label first")
        return 1
    golden = [json.loads(x) for x in GOLDEN_REAL.read_text().splitlines()] \
        if GOLDEN_REAL.exists() else []
    for g in golden:                                     # hand-labelled: tokens+tags or text+tags
        g.setdefault("tokens", normalise_tokens(g.get("text", "")))
    X = [token_features(r["tokens"], lex) for r in train]
    y = [r["tags"] for r in train]
    crf = sklearn_crfsuite.CRF(algorithm="lbfgs", c1=0.1, c2=0.1, max_iterations=150,
                               all_possible_transitions=True)
    t0 = time.perf_counter()
    crf.fit(X, y)
    train_s = time.perf_counter() - t0

    def evaluate(rows: list[dict]) -> tuple[float | None, dict[str, float]]:
        if not rows:
            return None, {}
        pred = crf.predict([token_features(r["tokens"], lex) for r in rows])
        return span_f1([r["tags"] for r in rows], pred)

    f_dev, _ = evaluate(dev)
    f_real, per_label = evaluate(dev_real)
    f_gold, _ = evaluate(golden)
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    labels = sorted({t[2:] for r in train for t in r["tags"] if t != "O"})
    with MODEL.open("wb") as fh:
        pickle.dump({"crf": crf, "labels": labels, "trained_at": time.time()}, fh)

    tagger = MLTagger(MODEL)                             # latency through the real inference path
    sample = [r["tokens"] for r in (dev + train)[:500]]
    times = []
    for toks in sample:
        s = time.perf_counter()
        tagger.tag(toks)
        times.append((time.perf_counter() - s) * 1000)
    times.sort()
    stats = json.loads((DATA / "weak_label_stats.json").read_text()) \
        if (DATA / "weak_label_stats.json").exists() else {}
    report = {
        "model": "CRF (sklearn-crfsuite)",
        "data": {"real_raw": stats.get("real_raw", 0),
                 "real_controller": stats.get("real_controller", 0),
                 "real_kept": stats.get("real_kept", 0), "synthetic": stats.get("synthetic", 0),
                 "train": len(train), "dev": len(dev), "dev_real": len(dev_real),
                 "golden": len(golden)},
        "real_data_used": stats.get("real_kept", 0) > 0,
        "license": LICENSE if stats.get("real_kept", 0) else "synthetic data only (no download)",
        "labels": labels,
        "span_f1": {"dev": f_dev, "dev_real": f_real, "golden": f_gold},
        "per_label_f1_dev_real": per_label,
        "train_seconds": round(train_s, 2),
        "infer_ms_p50": round(statistics.median(times), 3),
        "infer_ms_p90": round(times[int(len(times) * 0.9)], 3),
        "infer_samples": len(times),
    }
    (DATA / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
