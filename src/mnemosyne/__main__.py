"""Command-line entry: `python -m mnemosyne build <folder>` then `... serve`.

adduser — create an account (and adopt any pre-accounts dogfood albums).
build   — run a folder through the whole pipeline (ingest -> look -> arrange).
serve   — start the web preview at http://localhost:8000.
export  — render a laid-out album to a print-ready PDF.
cost    — show the cloud-inference COGS ($/album) for one album.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from mnemosyne import albums, auth, config, db, evaluate, pipeline, themes, usage


def _fmt_cost(cost: float | None) -> str:
    """A dollar figure, or an honest 'unknown' when no price is set — never a fake
    $0.00 (CLAUDE.md R12). Points at the knob that turns pricing on."""
    if cost is None:
        return "unknown (set MNEMOSYNE_GROK_PRICE_PROMPT/COMPLETION_PER_M in .env)"
    return f"${cost:.4f}"


def _print_cost_report(report: dict) -> None:
    if report["calls"] == 0:
        print("no billed cloud calls recorded (local backends are free, "
              "so this album cost nothing to compute)")
        return
    print(f"album #{report['album_id']} COGS: {report['calls']} billed call(s), "
          f"{report['total_tokens']} tokens, {_fmt_cost(report['cost_usd'])}")
    for st in report["stages"]:
        print(f"  {st['stage']:<8} {st['calls']:>4} call(s)  "
              f"{st['total_tokens']:>8} tok  {_fmt_cost(st['cost_usd'])}")


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
    b.add_argument("--theme", default="food",
                   help="gallery type for vision + arrange prompts "
                        f"({', '.join(themes.THEMES)})")

    s = sub.add_parser("serve", help="serve the web preview")
    s.add_argument("--port", type=int, default=8000)

    e = sub.add_parser("export", help="render a laid-out album to a print-ready PDF")
    e.add_argument("album_id", type=int, help="album id to export")
    e.add_argument("--out", default=None, help="output path (defaults to album-<id>.pdf)")

    co = sub.add_parser("cost", help="show cloud-inference COGS for an album")
    co.add_argument("album_id", type=int, help="album id to price")

    ev = sub.add_parser(
        "eval", help="compare Mnemosyne's layout vs the Mise baseline (beat-the-baseline harness)"
    )
    ev.add_argument(
        "--gallery", default=None,
        help="fixture gallery name to evaluate (default: every built-in fixture)",
    )
    ev.add_argument(
        "--album", type=int, default=None,
        help="evaluate a real album by id (reads its processed photos) instead of fixtures",
    )
    ev.add_argument("--keeper-floor", type=float, default=0.0,
                    help="cull photos whose keeper_score is below this (0..1)")
    ev.add_argument("--target-count", type=int, default=None,
                    help="cap the album to this many photos (keep the strongest)")
    ev.add_argument("--out", default=None, help="write the comparison markdown to a file")

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
            conn,
            name=name,
            source_dir=args.folder,
            owner_id=owner_id,
            gallery_theme=themes.normalize_theme(args.theme),
        )
        print(
            f"album #{result['album_id']}: looked at {result['looked']} photos, "
            f"laid out {result['spreads']} spreads"
        )
        # If this run used a billed cloud backend, surface what it cost right here —
        # that's the Gate 3 number (a real gallery's $/album). A free local run
        # records nothing and prints the "cost nothing" line.
        _print_cost_report(usage.album_cost_report(conn, result["album_id"]))
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
    elif args.cmd == "cost":
        conn = db.connect(config.DB_PATH)
        db.migrate(conn)
        _print_cost_report(usage.album_cost_report(conn, args.album_id))
    elif args.cmd == "eval":
        _run_eval(args)


def _eval_album_photos(album_id: int) -> list[dict]:
    """A real album's processed photos as evaluate-shaped dicts. Reads hero_score
    (Mnemosyne's stored signal); a missing keeper_score falls back to hero, so this
    works whether or not the Mise-signal columns are present."""
    conn = db.connect(config.DB_PATH)
    db.migrate(conn)
    rows = conn.execute(
        "SELECT id, hero_score, scene, width, height FROM photos "
        "WHERE album_id = ? AND scene IS NOT NULL ORDER BY id",
        (album_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "hero_potential": r["hero_score"],
            "scene": r["scene"],
            "width": r["width"],
            "height": r["height"],
        }
        for r in rows
    ]


def _run_eval(args) -> None:
    opts: dict = {}
    if args.keeper_floor:
        opts["keeper_floor"] = args.keeper_floor
    if args.target_count is not None:
        opts["target_count"] = args.target_count

    if args.album is not None:
        photos = _eval_album_photos(args.album)
        if not photos:
            sys.exit(f"album {args.album} has no processed photos to evaluate")
        galleries = {f"album-{args.album}": photos}
    else:
        galleries = evaluate.fixture_galleries()
        if args.gallery:
            if args.gallery not in galleries:
                sys.exit(
                    f"no such fixture gallery {args.gallery!r} "
                    f"(have: {', '.join(galleries)})"
                )
            galleries = {args.gallery: galleries[args.gallery]}

    docs = []
    for name, photos in galleries.items():
        comparison = evaluate.compare(photos, **opts)
        docs.append(evaluate.render_markdown(comparison, title=f"Album comparison — {name}"))
    out = "\n\n---\n\n".join(docs)

    if args.out:
        Path(args.out).write_text(out)
        print(f"wrote {args.out}")
    else:
        print(out)


if __name__ == "__main__":
    main()
