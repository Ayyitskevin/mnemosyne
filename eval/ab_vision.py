"""A/B harness for the vision step: run a folder of real photos through one
vision backend and record what it saw, how album-cover-worthy it judged each
shot, and how long it took.

The point is a like-for-like comparison: run it once with the local backend
(qwen3-vl) and once with grok (when xAI credits are loaded), then diff the two
JSON files. Both arms go through the SAME mnemosyne.vision.analyze_one path and
the SAME prompt, so the only variable is the model — which is the whole question
the A/B exists to answer ("cheaper AND better vision").

Backend is selected by env BEFORE mnemosyne.config is imported (config reads the
environment once at import), so this script sets it from --backend and then
imports. Per-photo token cost for the grok arm is logged separately by
vision.py to config.ROUTING_LOG; this harness records latency + the judgement.

Usage:
    python eval/ab_vision.py scratch/fnb_gallery --backend ollama
    python eval/ab_vision.py scratch/fnb_gallery --backend grok
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def main() -> None:
    ap = argparse.ArgumentParser(description="A/B one vision backend over a gallery folder.")
    ap.add_argument("folder", help="folder of images to analyze")
    ap.add_argument("--backend", choices=["ollama", "grok", "argus"], default="ollama",
                    help="which vision backend to route through (default: ollama)")
    ap.add_argument("--out", default=None, help="output JSON path (default: scratch/ab_<backend>_<ts>.json)")
    args = ap.parse_args()

    # Select the backend BEFORE importing config — it snapshots env at import time.
    os.environ["MNEMOSYNE_VISION_BACKEND"] = args.backend
    from mnemosyne import config, vision  # noqa: E402  (deliberate: after env is set)

    folder = Path(args.folder)
    images = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not images:
        raise SystemExit(f"no images found in {folder}")

    model = config.GROK_VISION_MODEL if args.backend == "grok" else config.VISION_MODEL
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = Path(args.out) if args.out else Path("scratch") / f"ab_{args.backend}_{ts}.json"

    print(f"backend={args.backend} model={model} folder={folder} n={len(images)}\n")
    results = []
    t_all = time.monotonic()
    for img in images:
        t0 = time.monotonic()
        try:
            r = vision.analyze_one(str(img))
            latency = time.monotonic() - t0
            row = {"file": img.name, "scene": r["scene"], "hero_score": r["hero_score"],
                   "latency_s": round(latency, 2), "error": None}
        except Exception as e:
            latency = time.monotonic() - t0
            row = {"file": img.name, "scene": None, "hero_score": None,
                   "latency_s": round(latency, 2), "error": str(e)[:200]}
        results.append(row)
        mark = "ERR" if row["error"] else f'{row["hero_score"]:.2f}'
        scene = row["error"] or row["scene"]
        print(f'  {img.name:14} {mark:>5}  {row["latency_s"]:>6.2f}s  {scene}')

    ok = [r for r in results if r["error"] is None]
    total = time.monotonic() - t_all
    payload = {
        "backend": args.backend,
        "model": model,
        "folder": str(folder),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "n": len(results),
            "ok": len(ok),
            "errors": len(results) - len(ok),
            "total_latency_s": round(total, 2),
            "mean_latency_s": round(sum(r["latency_s"] for r in ok) / len(ok), 2) if ok else None,
        },
        "results": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    s = payload["summary"]
    print(f'\n{s["ok"]}/{s["n"]} ok · {s["errors"]} errors · '
          f'mean {s["mean_latency_s"]}s/photo · total {s["total_latency_s"]}s')
    print(f"wrote {out_path}")
    if args.backend == "grok":
        print(f"per-photo token cost logged to {config.ROUTING_LOG}")


if __name__ == "__main__":
    main()
