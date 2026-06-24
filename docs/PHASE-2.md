# mnemosyne — Phase 2 scope (the SaaS turn)

**The one-line promise:** a photographer *who isn't you* drops their gallery into mnemosyne
in the cloud, gets the same drafted-album magic, edits and exports it — and pays for it —
without you ever shipping them your home GPU or seeing your costs run away.

Phase 0 proved the magic on your own galleries (local, free, private). Phase 1 made the draft
**editable and exportable** (layout engine, nudges, PDF). Phase 2 is the turn where mnemosyne
stops being your private tool and becomes a **product other people pay for.**

That single turn crosses **three lines at once**, and each has a failure mode that simply did
not exist in Phase 0/1:

- **Money** enters — billing, refunds, taxes, terms. Get this wrong and it's a legal/financial
  problem, not a bug.
- **Other people's data** enters — someone else's wedding photos. A data-isolation mistake is a
  *breach*, not a glitch.
- **Someone else's hardware** enters — a real product can't run on mickey, so the AI calls move
  to paid cloud inference and become a **cost per album** you have to price for.

> **The kill-risk still rules everything (carried from Phase 0):** the day this feels like "I
> could've just asked ChatGPT," it dies. Phase 2 must *keep* the unfair advantage — it looks at
> the actual images, knows shoot structure, respects album conventions — while adding the
> plumbing. Plumbing is not the product; the magic is. Don't let the SaaS scaffolding dilute it.

---

## Sequence the cheap gates BEFORE the heavy plumbing

The biggest Phase-2 mistake would be to build auth + billing for an audience of zero. Your own
stated strategy — and the right one — is **validate demand before multi-tenant plumbing.** So
Phase 2 opens with three cheap, high-leverage gates that de-risk everything downstream:

1. **Demand signal.** A landing page + waitlist (or a pre-pay/"founding photographer" offer) put
   in front of a real photographer community. If nobody wants in, we learned it for the cost of a
   web page instead of a month of billing code.
2. **The OBA self-check.** Ten minutes in the SoFi handbook for an *outside-business-activity*
   clause. You're a licensed insurance rep at a financial firm, so this is a possible **disclosure**
   obligation — NOT employer ownership of photography software (zero nexus to insurance). It's a
   self-check, not a blocker, but it belongs *before* real revenue lands.
3. **Cloud-inference cost model.** The number that sets pricing. Today vision + arrange run free on
   mickey; in production they're a paid API call per photo. We need a rough **$/album COGS** before
   we can name a price, because the price has to clear the cost with margin.

None of these are "real engineering." All three shape what we build. Do them first.

---

## Phase 2 scope — what's IN and what's OUT

Being explicit so we know when Phase 2 is *done* and don't sprawl into Phase 3.

**IN (build this):**
- **Accounts** — sign up / log in; each photographer sees only their own albums.
- **Gallery upload** — drop a folder of edited JPEGs through the browser into object storage
  (replacing Phase 0/1's "point at a local path").
- **Cloud inference** — the "look" (vision) and "arrange" (reasoning) steps run on a cloud API,
  with per-call **cost tracking** so we can see real COGS per album.
- **Billing** — a Stripe subscription (or per-album charge — see open questions) with plan limits.
- **Trust terms** — a real privacy policy + "we don't train on your images" data handling, and ToS.
- **The Phase-1 editor, per tenant** — the layout engine, spread/hero/photo nudges, and PDF export
  we already built, now scoped to a logged-in user's gallery.

**OUT (explicitly NOT Phase 2 — note it, don't sneak it in):**
- ❌ Pixieset / Lightroom / cloud-gallery integrations. Manual upload first; integrations later.
- ❌ Print-lab fulfillment or taking a cut of print sales. That's a *different product* (plutus).
- ❌ Teams / multi-seat studios / white-label / agency accounts. Single photographer per account.
- ❌ A mobile app. Responsive web is enough.
- ❌ A template marketplace or "store." The engine picks templates; users don't buy them.
- ❌ Advanced AI editing (move photos between spreads, restyle). Phase-1 nudges are the editor.

If any of those start feeling necessary, that's a Phase-3 conversation — not a reason to widen 2.

---

## The build, in dependency order (and WHY this order)

Think of it as five workstreams stacked so each one stands on the one below it.

1. **Storage turn** *(foundation — everything sits on it).* Phase 0/1 stores each photo as a local
   file **path**. A multi-tenant app can't do that — uploads have to land in **object storage**
   (e.g. Cloudflare R2 / S3) and the `photos` row holds a storage *key*, not a disk path. This
   touches `ingest`, the `photos` model, and the `/photo/{id}` route (now a signed URL or proxy).
   *Why first:* every other slice reads or writes photos; build the new home before the tenants.
2. **Cloud inference.** Swap the local Ollama calls in `vision.py` / `arrange.py` for a cloud
   vision + reasoning API, behind the same internal interface so the pipeline doesn't care where
   the model lives. Add **cost tracking** per call. This is also where the "we don't train on your
   images" data path is chosen (an API/provider mode that contractually doesn't train on inputs).
   *Note:* Grok is already building an **argus vision-delegation adapter** in this repo — coordinate
   so the cloud-vision swap and the argus path don't reinvent each other.
3. **Accounts + multi-tenancy** *(the scary one).* Auth (sign-up/login/session) and — the part that
   matters most — **every query scoped to the owning user.** `albums` gets a `user_id`; every read
   filters by it; there is no code path that returns another tenant's data. *Why this is the risk:*
   in Phase 0/1 a leak is impossible (one user). Here, one missing `WHERE user_id = ?` is a breach.
4. **Payments** *(commitment-class — gated).* Stripe subscription, plan limits, metering against the
   COGS from gate #3. **This is real money/legal logic**, so per the commitment-class rule it gets a
   short policy doc + CPA/lawyer review of the terms/refund/tax pieces **before** it ships — not a
   build-then-ask.
5. **Product surface.** The connective tissue: onboarding, the upload UI, the Phase-1 album editor,
   export, and (lightweight) a way to share/download the finished album. Mostly assembly once 1–4
   exist.

**Shipped in-repo (partial Workstream 5):**
- Share links with expiring client view + PDF download
- `MNEMOSYNE_PUBLIC_URL` for pasteable share URLs behind a tunnel (`scripts/wire-public-url.sh`)
- Cloud-inference COGS on the album page and albums index (pairs with `mnemosyne cost` CLI)
- Runtime strip on `/albums` + `/healthz` backends (vision/arrange/storage)
- **Regenerate layout** — re-run arrange only (keeps vision scores; replaces spreads/manual nudges)
- Failed albums: inline retry from the index; copy button on share links
- **Gallery themes** — food / wedding / general / event steer vision + arrange prompts per album
- **Plutus cross-sell** — optional `plutus_offer_url` on an album; share view shows an "Order prints" CTA when set (`MNEMOSYNE_PLUTUS_URL` for path-only links)

**Prod bootstrap (in-repo):**
- `.env.example` — SaaS env template (secrets off-repo)
- `scripts/wire-r2.sh` — flip `MNEMOSYNE_STORAGE_BACKEND=r2` + verify bucket
- `scripts/wire-public-url.sh` — `MNEMOSYNE_PUBLIC_URL` for share links
- `scripts/install-service.sh` + `ops/mnemosyne-user.service` — user systemd unit
- `scripts/validate-env.sh` — fail fast on placeholder/missing prod secrets
- `scripts/run-cogs-benchmark.sh` — one gallery through grok + `mnemosyne cost` report
- `scripts/bootstrap-prod.sh` — validate → optional R2/URL → install service
- `/healthz` storage probe (R2 credentials or local upload dir writable)
- **Trust surface** — `/privacy` + `/terms` (no-training promise, tenant isolation); linked from landing + signup
- **Deploy packaging** — `Dockerfile`, `docker-compose.yml`, `fly.toml`
- **Account lifecycle** — password reset, account delete (`/account`)
- **Stripe scaffold** — `billing.py` + checkout/portal/webhook routes (`MNEMOSYNE_STRIPE_ENABLED`, off by default)
- **Plutus auto-link** — `POST /albums/{id}/plutus-generate` via Plutus API (`MNEMOSYNE_PLUTUS_API_TOKEN`)
- **MinIO wire** — `scripts/wire-minio.sh` for local S3-compatible storage dogfood
- **Dogfood kit** — `scripts/dogfood-invite.sh` (tailnet invite + signup URLs)
- **Fly deploy** — `scripts/deploy-fly.sh` (after `flyctl auth login` + real R2 creds)

**Dogfood / deploy gates (operator):**
1. `sudo tailscale set --operator=$USER` then `bash scripts/wire-public-url.sh --tailscale` (HTTPS share links)
2. Invite photographer via Tailscale admin → share output of `scripts/dogfood-invite.sh`
3. For public cloud: `R2_ACCOUNT_ID=… R2_ACCESS_KEY_ID=… R2_SECRET_ACCESS_KEY=… bash scripts/wire-r2.sh` then `flyctl auth login` + `bash scripts/deploy-fly.sh`

---

## The data we add (kept as tiny as we can)

Phase 0/1 had four tables (album, photo, spread, placement). Phase 2 adds the SaaS spine:

- **user** — one per photographer: identity + auth. `album` gains a `user_id` so every album has an
  owner. *This single foreign key is the backbone of tenant isolation.*
- **photo** changes — `path` (local disk) becomes a **storage key** (object-store object), plus
  whatever the upload flow needs (original filename, content type, size).
- **usage / cost** — enough to see inference spend per album (calls made, tokens/images, $), so COGS
  is observable, not a surprise on the cloud bill (R14: automation stays observable).
- **subscription** — the link between a user and their Stripe plan/status (or lean entirely on
  Stripe as the source of truth and store only the customer/sub id).

We can always add more; we can't easily un-paint over-engineering. Same discipline as Phase 0.

---

## From local fleet to cloud inference — the COGS teaching

This is the architectural heart of Phase 2 and the line PHASE-0.md flagged from the start.

In Phase 0/1, vision (`qwen3-vl:32b`) and arrange (`qwen3.6:35b`) run on **mickey, for free,
fully private** — which is exactly why "we don't train on your images" was trivially true (they
never left the house). A product **cannot ship customers your home GPU.** So in Phase 2 those
calls move to **cloud inference**, and every album now costs real money to design.

That changes two things:
- **Pricing is downstream of COGS.** If an album costs $X of inference to draft, the price has to
  clear $X with margin. We can't name a price until we measure $X — hence the cost-model gate.
- **The privacy promise needs a real basis.** "We don't train on your images" stops being free
  truth and becomes a **provider/contract choice** (an API mode that doesn't retain or train on
  inputs) plus a retention policy on the stored originals. This is a feature, not boilerplate — it's
  part of the unfair advantage for a photographer trusting you with a client's wedding.

---

## Tech stack (what's new on top of the Phase 0/1 stack)

- **Object storage** — Cloudflare R2 or S3 for uploaded galleries (you already run Cloudflare for
  Mise's tunnel, so R2 is a natural fit).
- **Auth** — sessions + password, or OAuth (Google) — open question below.
- **Stripe** — subscriptions/billing (same vendor pattern as Mise's Studio money work).
- **A real host** — not mickey. A small cloud host (Fly / Railway / a VPS) behind Cloudflare. The
  app stays Python + FastAPI + SQLite (or a step up to Postgres if multi-tenant concurrency argues
  for it — decide when we get there, don't pre-optimize).
- **Cloud inference provider** — the vision + reasoning API — open question below.

---

## The gates that are NOT optional

- **Commitment-class** (money + legal logic): Stripe billing, ToS, privacy policy, "we don't train"
  terms, refunds, sales tax → short policy doc + **CPA/lawyer review before shipping the money
  path.** This narrows, it does not skip, build work.
- **Security** (other people's photos): tenant isolation is the headline, plus the same honest-risk
  discipline as the Mise hardening pass — least-privilege storage access, signed URLs, no public
  buckets, secrets in `.env` never in the repo/vault.
- **W2 / OBA**: the handbook self-check above, done before revenue.

---

## Definition of done for Phase 2 (how we know to stop)

1. A photographer who **isn't you** can sign up, pay, and log in.
2. They upload one of their real galleries through the browser.
3. mnemosyne drafts the album on **cloud** inference (cost visible to you), they edit it with the
   Phase-1 nudges, and export a print-ready PDF.
4. They see *only* their own galleries — and you'd be **comfortable** with the privacy terms and the
   per-album margin.

When a stranger can run the full loop and you're at ease with the money and the trust — Phase 2 is done.

---

## What comes after (the road, NOT building yet)

- **Phase 3+ candidates:** Pixieset/Lightroom import, client-sharing/proofing, studio/team accounts,
  and the cross-sell into the sibling products (plutus for print revenue, argus as the shared vision
  API). These are growth, not the SaaS turn itself.

---

## Open questions for Kevin (the forks that shape the build)

1. **Cloud inference provider + cost target.** Which API for vision + reasoning (Claude vision is the
   natural default given the stack), and what's the **$/album ceiling** that still leaves margin?
2. **Storage + host.** Cloudflare R2 + a small cloud host behind your existing CF setup, or somewhere
   else? (Explicitly *not* mickey/flow — this is the first thing that must live in real cloud.)
3. **Pricing shape.** Per-album one-time charge, monthly subscription, or hybrid (e.g. a free trial
   album, then pay)? This decides whether we build metering or simple charges.
4. **Auth.** Email + password, or Google OAuth? (OAuth is less to secure ourselves; password is fewer
   moving parts.)
5. **Where do we validate?** Which photographer community/channel for the waitlist test?

Answer the forks and the first real build step is the **storage turn** — uploads landing in object
storage — with the demand-validation page running in parallel so we're proving the market while we
lay the foundation.
