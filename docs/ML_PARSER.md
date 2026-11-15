# Trained parser model (ML_PARSER) — how to set it up on your machine

VoiceVector can parse controller transmissions with a small trained model (a CRF tagger) in front
of the existing regex parser. It is **off by default**: without the steps below, nothing changes.

The model only marks which words are the callsign, the heading, the level and so on. Every value
is still produced and range-checked by the existing grammar, and anything it can't validate falls
back to the regex parser and then Claude Haiku, exactly as today. So it can never send an
unchecked instruction to the sim.

The model file (`models/crf.pkl`, about 220 KB) and the training data are **not in git**
(both are gitignored). Each machine builds its own in about 2 minutes, or copies the file from
someone who has built it.

---

## 1. Prerequisites
- The repo pulled to the latest `main`.
- The Python virtual environment set up as usual (`.venv`, Python 3.12).
- Internet access for the one-off data download (about 2 MB).

Install the four new packages (they are in `requirements.txt`):
```bash
.venv/bin/pip install -r requirements.txt
```
(If you use `uv`: `uv pip install --python .venv/bin/python -r requirements.txt`.)

The new packages are `datasets`, `sklearn-crfsuite`, `pandas` and `pyarrow`.

## 2. Build the model (about 2 minutes)
Run from the repo root, in this order:
```bash
.venv/bin/python -m tools.train.fetch_uwb
```
Downloads the text of the UWB-ATCC radio transcripts from Hugging Face. It reads only the `id`
and `text` columns, about 2 MB, never the 712 MB of audio. Expect "wrote 13201 lines".

```bash
.venv/bin/python -m tools.train.weak_label
```
Labels about 1,900 real controller lines and generates 3,000 synthetic Gatwick lines. Expect
`"real_kept": 1879` (give or take, if the dataset changes) and `"synthetic": 3000`.

```bash
.venv/bin/python -m tools.train.train_crf
```
Trains the model (about 25 s), writes `models/crf.pkl` and `data/train/report.json`, and prints
the report.

**Quicker alternative:** copy `models/crf.pkl` from Tudor's laptop into `models/` in your clone
and skip step 2. Steps 3 and 4 still apply.

## 3. Check it works
```bash
.venv/bin/python -m pytest server/tests/voice/test_ml_parser.py -q
```
Expect `7 passed`. Without a model file, the model-dependent tests show as skipped rather than
failed; that means step 2 didn't produce `models/crf.pkl`.

Then open `data/train/report.json` and check roughly these numbers:
- `span_f1.dev_real` about 0.98
- `infer_ms_p50` well under 1 ms
- `real_data_used: true` (if `false`, the download in step 2 failed and it trained on synthetic
  lines only — still works, but re-run `fetch_uwb` when you have internet)

## 4. Turn it on
Start the server with the flag set:
```bash
ML_PARSER=1 ML_CONF=0.8 .venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8000
```
- `ML_PARSER=1` turns the model on; leave it out (or `ML_PARSER=0`) for today's behaviour.
- `ML_CONF` is the confidence the model needs before its result is used where the regex parser
  couldn't resolve a line (default 0.80).
- `ML_MODEL=/path/to/crf.pkl` points at a model file somewhere else (optional).

**How to see it working:** open the pipeline trace (`T`) and look at the `parse` stage's detail.
With the flag on it shows `parser: "ml"` or `"regex"`, plus `ml_conf` and `ml_ms`. With the
flag off those fields are absent.

## 5. How it decides (so you know what to expect)
For each controller transmission, when `ML_PARSER=1`:
1. The regex parser runs as always.
2. The model tags the same words, and its spans are converted by the existing grammar and
   callsign resolver.
3. If both agree → that result (marked `ml`).
4. If the regex parser fully resolved the line but the model disagrees → **the regex result
   wins**.
5. If the regex parser could not resolve the line and the model's spans all validate with
   confidence ≥ `ML_CONF` → the model's result is used.
6. Otherwise → the regex result, and the existing Haiku fallback decides as before.

Corrections ("negative …"), say-again, confirm and broadcast transmissions always use the regex
parser.

## 6. Troubleshooting
| Symptom | Cause / fix |
|---|---|
| Log line `ML parser disabled: cannot load models/crf.pkl` | No model file: do step 2 (or copy the file). The server keeps running on the regex parser. |
| `ModuleNotFoundError: sklearn_crfsuite` | Step 1 not done in this `.venv`. |
| `fetch_uwb: download failed …` | No internet or Hugging Face unreachable. It exits cleanly; `weak_label` then trains on synthetic lines only. Re-run later. |
| Tests show "skipped" | No `models/crf.pkl` in this clone. |
| Want to switch it off quickly | Restart without `ML_PARSER=1`. Nothing else to undo. |

## 7. Rules
- **Don't commit** `models/` or `data/train/*.jsonl` (gitignored on purpose). `data/train/report.json`,
  `labels.json` and `weak_label_stats.json` are committed so the numbers are visible.
- **Licence:** the real transcripts are UWB-ATCC, CC BY-NC-SA 4.0 (Šmídl et al., University of
  West Bohemia). Non-commercial use with credit; keep the credit in the README and slides.
- **Live demo:** keep `ML_PARSER` off unless Tudor has run the full demo script three times with
  it on and it behaved identically. Otherwise present it as "trained, evaluated, running in
  shadow mode".
- **How to describe the accuracy:** the real lines were labelled by our own regex grammar, so the
  0.98 span F1 means the model learned to agree with it on unseen recordings, not independent
  accuracy.

## 8. Files
| File | What it does |
|---|---|
| `tools/train/fetch_uwb.py` | Downloads transcript text (no audio) → `data/train/raw.jsonl` |
| `tools/train/weak_label.py` | Labels real lines, generates synthetic ones, splits → `data/train/*.jsonl` |
| `tools/train/train_crf.py` | Trains and evaluates → `models/crf.pkl`, `data/train/report.json` |
| `server/voice/ml_tagger.py` | Loads the model, tags tokens, validates spans, makes the decision |
| `server/voice/parser/__init__.py` | The one hook at the end of `parse()` (only when `ML_PARSER=1`) |
| `server/tests/voice/test_ml_parser.py` | Flag-off identity, never-crash, flag-on golden set, latency |
