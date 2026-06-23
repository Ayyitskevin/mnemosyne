# mnemosyne — Phase 0 scope

**The one-line promise:** point mnemosyne at a folder of edited photos from one of your
real galleries, and it hands back a *drafted album* — photos selected, put in story order,
and grouped onto spreads with a hero shot picked for each one — so you can look at it and go
"...whoa, it actually laid out my wedding."

Phase 0 is **not** a product. It's the smallest thing that proves the magic is real, built so
*you* can dogfood it on your own galleries. No accounts, no payments, no customers, no cloud.
If the magic isn't there at this size, no amount of SaaS plumbing later would have saved it.

---

## Why this is the thing worth proving

Album design is real, paid, manual work today — photographers outsource it at ~$40–75/album.
The manual job is four steps:

1. **Cull** — pick which photos make the album (a wedding might have 500 edited; the album
   holds ~60–80).
2. **Sequence** — order them so they tell the story (getting ready → ceremony → portraits →
   reception; a chronological + emotional arc).
3. **Group into spreads** — decide which photos share a two-page spread and look good together.
4. **Lay out the spread** — how many photos on the spread, which one is the big "hero," how
   orientations (tall vs. wide) pack together.

That's the work we're automating. The "magic moment" we're chasing in Phase 0 is steps 2–3
mostly: seeing *your own* photos auto-arranged into a coherent album story.

> **The kill-risk to keep in mind the whole time:** if this feels like "I could've just asked
> ChatGPT," it dies. It only wins because it *looks at the actual images*, knows photography
> shoot structure, and respects album conventions — not because it's a chat box.

---

## Phase 0 scope — what's IN and what's OUT

Being explicit here so we both know when Phase 0 is *done* and don't accidentally keep building.

**IN (build this):**
- Point the app at a local folder of images (one of your galleries).
- For each photo: read its orientation, and use a **vision model** to tag what it is (wide
  establishing shot? close detail? portrait of people?) and score how "hero-worthy" it is.
- **Sequence** the photos into a sensible story order.
- **Group** them into spreads (a handful of photos per spread), picking one **hero** per spread
  and respecting orientation so the spread looks balanced.
- **Render an on-screen preview** of the album — spread by spread — that you can scroll through.

**OUT (explicitly NOT Phase 0 — note it, don't sneak it in):**
- ❌ Print-ready / InDesign / PDF export. (A web preview is enough to prove the magic.)
- ❌ User accounts, login, multi-user, "tenants."
- ❌ Payments / Stripe / pricing.
- ❌ Cloud hosting or cloud AI — Phase 0 runs on your machine, against your fleet.
- ❌ Drag-to-rearrange / manual editing of the layout. (Phase 0 just *proposes*; you judge.)
- ❌ Upload UI, galleries-from-Pixieset integrations, client sharing.

If any of those start feeling necessary, that's the signal Phase 0 worked and we're ready to
talk Phase 1 — not a reason to widen Phase 0.

---

## How it works, step by step (plain version)

Think of it as an assembly line. A folder of photos goes in the left end; an album preview
comes out the right. Four stations:

1. **Ingest.** We read the folder and record each photo (just its file path + width/height —
   that already tells us tall vs. wide). Nothing clever yet.
2. **Look.** For each photo we ask a **vision AI** "what's in this picture, and how strong a
   shot is it?" We save that answer next to the photo. *This is the unfair-advantage step* —
   the app actually sees the image, which is the whole reason it beats a generic chatbot.
3. **Arrange.** We hand the list of photos + their tags to a **reasoning AI** and ask it to put
   them in story order and group them into spreads, marking one hero per spread. Simple rules
   help it (don't put two big heroes on one spread; pair a wide shot with a couple of details).
4. **Show.** We render the spreads as a web page you scroll through — each spread drawn as the
   photos arranged on two pages, hero shown larger.

That's the entire Phase 0 loop: **folder → look → arrange → show.**

---

## The data we store (kept deliberately tiny)

Four little tables (same SQLite database style you already know from Mise/Athena):

- **album** — one per gallery you run through it (a name, when it was made).
- **photo** — one row per image: its file path, width/height, and the AI's notes about it
  (what it is, hero score).
- **spread** — one per two-page spread in the album, in order, with which photo is the hero.
- **placement** — which photos sit on which spread, and in what position.

No more than that. We can always add later; we can't easily un-paint over-engineering.

---

## The AI calls — and why Phase 0 runs LOCAL

This is the part that makes mnemosyne cheap to prove and is genuinely your edge:

- **The "look" step** uses a vision model. You already run **qwen3-vl:32b on mickey** — so in
  Phase 0 every image is analyzed **for free, on your own hardware, fully private.** The promise
  "we don't train on your images" is trivially true here: the images never leave your house.
- **The "arrange" step** uses a local reasoning model (qwen3 / similar on the fleet).

This matters two ways. First, it means you can run Phase 0 over *all* your galleries at zero
marginal cost while we tune it. Second — and this is the teaching point for later — **a real
multi-tenant SaaS can't ship customers your home GPU.** When mnemosyne becomes a product
(Phase 2), the vision/reasoning calls move to *cloud inference* and that cost gets **priced into
the subscription.** Phase 0 deliberately dodges that so we can prove the idea before paying for
anything. "Local first," exactly like the rest of your stack.

---

## Tech stack (all stuff you've already built on)

- **Python + FastAPI** — the app, same as Mise and Athena.
- **Jinja + a dash of HTMX** — the preview web page.
- **SQLite** — the four small tables above.
- **Your Ollama fleet on mickey** — the vision + reasoning calls.

Nothing new to learn structurally; this is the pattern you've already drilled, pointed at a new
problem.

---

## Definition of done for Phase 0 (how we know to stop)

1. I can run one command, give it a folder of your photos, and open a web page.
2. That page shows the photos **selected, in story order, grouped onto spreads, with a hero
   per spread** — generated by the AI actually looking at the images, not random.
3. You scroll it and have an honest gut reaction: *is the magic there or not?*

That gut reaction is the real deliverable. It tells us whether mnemosyne is worth Phase 1.

---

## What comes after (so you can see the road — we are NOT building these yet)

- **Phase 1:** a real layout engine (sizing, full-bleeds, multiple templates), the ability to
  nudge/rearrange, and **export to PDF** you could actually send to a print lab.
- **Phase 2:** the SaaS turn — accounts, cloud inference priced into a subscription, Stripe,
  "we don't train on your images" terms, and a way for *other* photographers to drop their
  galleries in. This is the first point where money and other people's data enter — and the
  point where the [commitment-class] rules (real money/legal logic → policy + CPA/lawyer first)
  kick in.

---

## Open questions for Kevin (before I scaffold code)

1. **Which gallery do we test on first?** Ideally a real, finished one you'd recognize a good
   album from — a wedding or a full F&B shoot. Where do those edited JPEGs live on disk?
2. **Album shape:** do you have a target album in mind (e.g. ~20 spreads, ~3 photos/spread), or
   should Phase 0 just pick something sensible and let you react to it?
3. **Run it where?** Phase 0 is local — happy for it to live here on mickey hitting the fleet,
   same as Athena. Confirm that's the right home.

Answer these and the next step is a Phase-0 skeleton: the app boots, takes a folder, and walks
the four stations end to end.
