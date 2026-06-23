"""Command-line entry: `python -m mnemosyne build <folder>` then `... serve`.

build  — run a folder through the whole pipeline (ingest -> look -> arrange).
serve  — start the web preview at http://localhost:8000.
export — render a laid-out album to a print-ready PDF.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemosyne import config, db, pipeline


def main() -> None:
    parser = argparse.ArgumentParser(prog="mnemosyne")
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="design an album from a folder of photos")
    b.add_argument("folder", help="path to the gallery folder")
    b.add_argument("--name", default=None, help="album name (defaults to folder name)")

    s = sub.add_parser("serve", help="serve the web preview")
    s.add_argument("--port", type=int, default=8000)

    e = sub.add_parser("export", help="render an album to a print-ready PDF")
    e.add_argument("album_id", type=int, help="album id to export")
    e.add_argument("--out", default=None, help="output path (defaults to album-<id>.pdf)")

    args = parser.parse_args()

    if args.cmd == "build":
        conn = db.connect(config.DB_PATH)
        db.migrate(conn)
        name = args.name or Path(args.folder).expanduser().name
        result = pipeline.build_album(conn, name=name, source_dir=args.folder)
        print(
            f"album #{result['album_id']}: looked at {result['looked']} photos, "
            f"laid out {result['spreads']} spreads"
        )
        print("now run:  python -m mnemosyne serve   ->   http://localhost:8000")
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("mnemosyne.main:app", host="0.0.0.0", port=args.port)
    elif args.cmd == "export":
        from mnemosyne import export

        conn = db.connect(config.DB_PATH)
        db.migrate(conn)
        out = args.out or f"album-{args.album_id}.pdf"
        path = export.export_album(conn, args.album_id, out)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
