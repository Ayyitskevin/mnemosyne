"""Command-line entry: `python -m mnemosyne build <folder>` then `... serve`.

adduser — create an account (and adopt any pre-accounts dogfood albums).
build   — run a folder through the whole pipeline (ingest -> look -> arrange).
serve   — start the web preview at http://localhost:8000.
export  — render a laid-out album to a print-ready PDF.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from mnemosyne import albums, auth, config, db, pipeline


def _resolve_owner(conn, email: str | None) -> int:
    """Pick the owner for a CLI build. With --owner-email, look that account up.
    Without it, fall back to the sole account if there's exactly one (the common
    dogfood case); refuse to guess when there are zero or several."""
    if email:
        user = auth.get_user_by_email(conn, email)
        if user is None:
            sys.exit(f"no account for {email!r} — create one with: mnemosyne adduser {email}")
        return user["id"]
    rows = conn.execute("SELECT id FROM users ORDER BY id").fetchall()
    if len(rows) == 1:
        return rows[0]["id"]
    if not rows:
        sys.exit("no accounts yet — create one first with: mnemosyne adduser <email>")
    sys.exit("multiple accounts exist — say which with --owner-email <email>")


def main() -> None:
    parser = argparse.ArgumentParser(prog="mnemosyne")
    sub = parser.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("adduser", help="create an account")
    u.add_argument("email", help="login email")

    b = sub.add_parser("build", help="design an album from a folder of photos")
    b.add_argument("folder", help="path to the gallery folder")
    b.add_argument("--name", default=None, help="album name (defaults to folder name)")
    b.add_argument("--owner-email", default=None,
                   help="account that owns the album (defaults to the sole account)")

    s = sub.add_parser("serve", help="serve the web preview")
    s.add_argument("--port", type=int, default=8000)

    e = sub.add_parser("export", help="render a laid-out album to a print-ready PDF")
    e.add_argument("album_id", type=int, help="album id to export")
    e.add_argument("--out", default=None, help="output path (defaults to album-<id>.pdf)")

    args = parser.parse_args()

    if args.cmd == "adduser":
        conn = db.connect(config.DB_PATH)
        db.migrate(conn)
        pw = getpass.getpass("password: ")
        if pw != getpass.getpass("confirm:  "):
            sys.exit("passwords didn't match")
        try:
            user = auth.create_user(conn, args.email, pw)
        except ValueError as exc:
            sys.exit(str(exc))
        adopted = albums.adopt_orphans(conn, user["id"])
        print(f"created account {user['email']} (id {user['id']})")
        if adopted:
            print(f"adopted {adopted} pre-existing album(s) into this account")
    elif args.cmd == "build":
        conn = db.connect(config.DB_PATH)
        db.migrate(conn)
        owner_id = _resolve_owner(conn, args.owner_email)
        name = args.name or Path(args.folder).expanduser().name
        result = pipeline.build_album(
            conn, name=name, source_dir=args.folder, owner_id=owner_id
        )
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
