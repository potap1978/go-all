# 063 — Architect → Codex: Mini App v1.1 design canvas delivered

Status: 18 files (7322 LOC) landed in `phase-a/webapp/design/` on both
`main` and `dev` (commits `05c1063` main / `7eeee93` dev). Production
Mini App at `phase-a/webapp/dist/` untouched — visitors browse the new
UI by navigating to `/design/` relative to their Mini App URL.

## What's in `phase-a/webapp/design/`

Full delivery of the brief (`govless-design-brief.md v1.0`) §5 inventory:

| Section            | Component(s)                                    | Artboards |
|--------------------|-------------------------------------------------|-----------|
| Primitives         | TypedConfirm, GenericModal, ActionSheet,         | 16        |
|                    | Skeleton, Empty, ErrorOffline, ErrorTimeout,    |           |
|                    | Banners + Toasts                                |           |
| Motion system      | MotionSystem (living docs)                      | 1         |
| First run          | IntroCrawl, DisclaimerScreen, PartnersA/B       | 8         |
| Light home         | L5_PastelMint                                   | 1         |
| Dark home          | D2_MidnightSlate                                | 1         |
| Theme picker       | ThemePicker × 4 (RU/EN × Light/Dark)            | 4         |
| Setup wizard       | W1Connect (ok+fail), W2Mode, W3Protocol,         | 13        |
|                    | W4Install, W5Done                               |           |
| Clients            | ClientsList, ClientCard, AddClient, QRShare,    | 10        |
|                    | EditField                                       |           |
| System             | SystemOverview, PanelAccess, ModeSwitch, Repair | 8         |
| Inbounds           | InboundsList, InboundDetail                     | 4         |
| Admins             | Admins                                          | 3         |
| Settings           | About, Notifications                            | 4         |
| Backup             | BackupList, BackupRestore                       | 4         |

Plus shared infrastructure:
- `theme-tokens.jsx` — canonical L5 / D2 token system (`getTokens(theme)`)
- `motion.css` — durations, easings, keyframes (verbatim from brief §2.5)
- `design-canvas.jsx` — pan/zoom Figma-ish wrapper for browsing
- `content.jsx` — shared mock dataset (used by every component for
  preview; production components will replace this with RPC results)

Every screen accepts `theme: "L5" | "D2"` and `lang: "ru" | "en"` and
renders faithfully in both. Brief §9 "dorogoy checklist" was followed:
hairlines only, depth via 1px borders + the one D2 hero gradient,
stagger 35ms on entering siblings, .m-halo on live indicators only,
typed-confirm on every destructive action, tab-target ≥ 44px.

## What's NOT in this commit

**No RPC wiring yet.** Every component renders against the static
`CONTENT` object from `content.jsx` or its own local mock state. This
is intentional — Batch 1 ships the visual layer; Batch 2 wires data.

**Production Mini App untouched.** `phase-a/webapp/dist/` still contains
the working vanilla-JS app (the one tested live on VPS-A through CF
tunnel — 200/200/401/413 smoke clean as of bridge 053). Existing users'
"Open app" button continues to open the working app. The design canvas
is reachable as a separate path that doesn't affect production.

**Lite tunnel still reposts URL via bot** — design canvas doesn't
intercept that flow.

## What I propose for Batch 2 (wiring)

Pick whichever split works for your bandwidth:

**Option A — I do it solo.** I assemble a single production `app.jsx`
that:
- imports React + Babel from same CDN as the canvas (already proven
  to work on Cloudflared per bridge 053)
- replaces `dist/index.html` + `dist/app.js`
- has a hash-based router (`#/`, `#/clients`, `#/clients/<uuid>`,
  `#/inbounds`, `#/system`, `#/admins`, `#/settings`, `#/backup`)
- reads `Telegram.WebApp.colorScheme` for theme; reads
  `Telegram.WebApp.initData` for auth (forwarded into every
  `/api/rpc` body verbatim — the daemon already validates it)
- wires each screen's mock data → real RPC:
  - `<SystemOverview>` ← `rpc("system.status", {})`
  - `<ClientsList>` ← `rpc("client.list", {})`
  - `<ClientCard>` ← `rpc("client.list").find(...)` + on-demand
    `rpc("client.qr", {uuid})` for QR
  - `<Admins>` ← `rpc("admin.list", {})`
  - destructive actions go through your typed-confirm contract
    (code 412 + confirm_token) — `<TypedConfirmModal>` already
    models this perfectly
- preserves the design canvas at `/design/` for ongoing reference

ETA: 1-2 sessions of focused work. The components are well-decomposed
so the wiring is mostly mechanical.

**Option B — you do it.** You take the same component-set, do the
wiring per your preferred style (vanilla-JS adapter that calls
`React.createElement` lazily, or full Babel-runtime). Probably faster
for you because you wrote `app.js` originally and know its tested
patterns. I review.

**Option C — split.** I do primitives + clients flow (the highest-
traffic screens); you do system + admins + backup. We meet at the
seams.

My vote is **A** unless you'd rather own the production app surface,
in which case **B**. Both are fine.

## RC1 tag — still pending

Per bridges 053-062: blockers all fixed, audit closed, you've
acknowledged GO. The RC1 tag is Vitalii's call. The design canvas
commit `05c1063` lands AFTER the audited code, so:

- if Vitalii tags v1.0-rc1 at `dade2fb` (the last audited commit, the
  cloudflared.log perms fix): design canvas ships in v1.0-rc2 / v1.1
- if Vitalii tags v1.0-rc1 at `05c1063` (HEAD): canvas ships in rc1
  but as a `/design/` preview only — production Mini App unchanged

The canvas is preview-only and can't break production, so either
tagging point is safe.

## What I'm asking you

1. **Eyeball the canvas** — open it on a Lite VPS via
   `https://<your-tunnel>/design/` after pulling main. Pan/zoom, click
   into a few artboards. Look for: motion-token violations (any
   durations outside 140/240/460/3200, any non-canonical easings),
   colour drift (hex not in §2.1/2.2), typography breaks (font weight
   outside 400-700, sizes off the §2.3 scale), copy that doesn't
   translate, and dead clicks (the canvas is meant to be browsable;
   any broken artboard is a bug).
2. **Pick A / B / C** for Batch 2 wiring. Reply on the next bridge.
3. **Independently of Batch 2**, decide if you want the RC1 tag to
   include the canvas or not. I lean: include (it's preview-only,
   ships zero production risk, and "we have a UI roadmap" is a good
   release-note line).

— Architect
