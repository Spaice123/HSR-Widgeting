# Honkai: Star Rail — Discord Dynamic Profile Widget

A port of **[MeYashverma/Genshin-Stats](https://github.com/MeYashverma/Genshin-Stats)** to **Honkai: Star Rail**. Auto-updates a Discord Dynamic Profile Widget with your public HSR stats and a rotating showcase character, entirely on GitHub Actions — no VPS, no server, no database.

This document doubles as a summary of the research behind the port: how the original works, the exact constraint that makes HSR harder than Genshin, and how this repo works around it.

---

## TL;DR

- The original Genshin widget works because **Enka.Network exposes rich public data for Genshin** (Adventure Rank, Spiral Abyss, Imaginarium Theatre, etc.) with **no login**.
- For HSR, **Enka's public data is thinner**. It gives Trailblaze level, Equilibrium level, achievements, Simulated Universe progress, collection counts, and your showcase characters — but **not** Memory of Chaos / Pure Fiction / Apocalyptic Shadow clear data. That data lives in a game packet Enka does not query, which is exactly what the Discord thread concluded ("i can do a 4 stat one with just eq level, tb level, achievements and sim uni").
- So the port is **two tiers**:
  - **Tier A (default, no login):** Enka.Network — the faithful, cookie-free port. Delivers everything Enka has for HSR.
  - **Tier B (optional, HoyoLab cookie):** adds Memory of Chaos / Pure Fiction / Apocalyptic Shadow via `genshin.py`. This is the *only* way to get those, and it requires a (non-permanent) HoyoLab cookie — use an alt if you prefer.

---

## How the original Genshin-Stats works

A single Node script (`enkaUser.js`) run on a GitHub Actions cron:

1. Fetches your **public** profile from Enka.Network (`enka.network/api/uid/{UID}?info`) — no login.
2. Picks one **showcase character** using a time-derived slot (`Date.now() / ROTATE_HOURS`), so rotation needs no state file.
3. Resolves that character's portrait + name from Enka's official mapping files (`characters.json`, `loc.json`, `pfps.json`), so new characters are supported automatically.
4. Packs everything into a Discord **Dynamic Profile Widget** payload (`type` 1 = text, 2 = number, 3 = image).
5. `PATCH`es it to `discord.com/api/v9/applications/{APP_ID}/users/{USER_ID}/identities/0/profile` with a bot token.

The whole thing is stateless and free to run on GitHub's scheduler.

## The HSR constraint (verified against the live API)

Enka **does** have an HSR endpoint: `enka.network/api/hsr/uid/{UID}`. A live pull of `detailInfo` returns:

| Available (Tier A) | Not available from Enka |
|---|---|
| `nickname`, `signature`, `friendCount` | Memory of Chaos (floor/stars) |
| `level` → **Trailblaze Level** | Pure Fiction |
| `worldLevel` → **Equilibrium Level** | Apocalyptic Shadow |
| `recordInfo.achievementCount` | Warp / gacha history |
| `recordInfo.maxRogueChallengeScore` → **Simulated Universe** max world | Active days, real-time notes (stamina) |
| `recordInfo.avatarCount` / `equipmentCount` → collection | |
| `avatarDetailList` → showcase characters (for the rotating portrait) | |

`recordInfo.challengeInfo` comes back **empty** — confirming that MoC/PF/APC clear data simply is not in Enka's HSR response. That is the "4-stat" ceiling the thread ran into, and it is a data-source limitation, not a coding one.

## Clear stats: the definitive verdict

The thread's lead — *"there is apparently a packet that shows clear history but enka unfortunately doesn't query it"* — was investigated and is **dead**:

- The showcase packet **used to** carry `recordInfo.challengeInfo` (`scheduleMaxLevel` = current MoC floor, `noneScheduleMaxLevel` = Forgotten Hall) — this is exactly the packet MiHoMo queried.
- HoYo **removed it server-side**. The [MiHoMo API docs](https://march7th.xiaohei.moe/en/resource/mihomo_api.html) now annotate both `recordInfo.challengeInfo` (raw) and `space_info.memory_data` (parsed) with *"This field has been removed"*.
- A live Enka pull (2026-07) confirms: `"challengeInfo": {}`. There is nothing left for Enka — or anyone — to query without login.

**So: no cookie-free source for clear stats exists.** The HoyoLab battle-record API is the only path — but it has a redeeming property that makes it much more palatable:

### The cookie doesn't have to be yours (alt-account mode)

The record API serves **any** player's clear stats to **any** logged-in cookie, as long as the *target* UID's battle records are public (this is why genshin.py has a dedicated `DataNotPublic` error, retcode 10102). Setup:

1. On your **main** HoyoLab account: Profile → Settings → Privacy Settings → enable **Battle Chronicle** ("show my battle records to others").
2. Create a **throwaway** HoyoLab account (no game account needed). Log into hoyolab.com with it, grab its `ltuid_v2` / `ltoken_v2` cookies, and use those as the GitHub secrets.

Your main's cookie never leaves your machine; the burner has nothing to lose if it leaks or expires. Rate limit is 30 UIDs/cookie/day (retcode 10101) — irrelevant for a widget cron. This is the "can use alts too" note from the thread, made concrete.

## Solution

### Tier A — Enka only (default, no login)

`hsrUser.js` mirrors the original exactly, swapping the data sources for HSR:

- **Stats:** `enka.network/api/hsr/uid/{UID}` (public).
- **Character name + portrait:** [`Mar-7th/StarRailRes`](https://github.com/Mar-7th/StarRailRes), the community-standard HSR asset index, keyed directly by `avatarId` and auto-updating for new characters (equivalent to what Enka's mapping files do for Genshin). It also resolves the Trailblazer's `{NICKNAME}` placeholder and eidolon (`rank`) labels.
- **Rotation:** identical time-slot logic over `avatarDetailList`.

This is ready to run and is what most people should use.

### Tier B — HoyoLab cookie (optional, for MoC / PF / APC)

If you want the endgame clears, `optional-hoyolab/hsr_hoyolab.py` uses **[`genshin.py`](https://github.com/thesadru/genshin.py)** to fetch:

- `get_starrail_challenge` → Memory of Chaos
- `get_starrail_pure_fiction` → Pure Fiction
- `get_starrail_apc_shadow` → Apocalyptic Shadow
- `get_anomaly_arbitration` → Anomaly Arbitration (stars + best medal)
- `get_starrail_user` → active days, etc.

It writes `hoyo_stats.json`, which `hsrUser.js` merges into the **same single** Discord PATCH (so the two steps never clobber each other).

### Cookie auto-refresh (set-and-forget)

The `ltoken_v2` cookie expires — but HoyoLab's app login also issues an **`stoken`**, a refresh token that stays valid until the account's password changes. This repo uses it so the cookie **never goes stale**:

1. **Once, locally:** `pip install -r optional-hoyolab/requirements.txt && python optional-hoyolab/get_stoken.py` — it logs in with email + password (a Geetest captcha, if triggered, opens at `localhost:5000`; an email verification code may be prompted). It prints three values.
2. **Set them as GitHub secrets:** `HOYO_LTUID_V2`, `HOYO_MID`, `HOYO_STOKEN`.
3. Done. On every run, `hsr_hoyolab.py` mints a brand-new `ltoken_v2` from the stoken (`fetch_cookie_with_stoken_v2`, headless, no captcha). If the stoken is ever revoked (password change), the run fails with a message telling you to re-run step 1.

The login step is local-only by design: captchas and verification emails can't be handled on a headless runner. Pair this with a **throwaway alt** account and the stored stoken guards nothing of value.

**Trade-offs, stated plainly:**

- Requires HoyoLab credentials once — but they can be a **throwaway alt's** (see "alt-account mode" above); your main only needs its battle records set to public.
- Without a stoken, the fallback static `HOYO_LTOKEN_V2` cookie expires periodically. `hsrUser.js` has a freshness guard (`HOYO_STATS_MAX_AGE_H`, default 48h): if the Python step has been failing, stale clear stats are dropped from the widget instead of silently displayed.
- The script prints actionable errors for the common failures: `DataNotPublic` (target records private), `InvalidCookies` (refresh secrets), revoked stoken (re-run `get_stoken.py`).

---

## Widget fields to bind (Discord widget designer)

| Field | Type | Example | Tier |
|---|---|---|---|
| `nickname` | Text | seria | A |
| `uid` | Text | UID 809162009 | A |
| `world` | Text | Asia • EQ 6 | A |
| `tb_str` / `tb` | Text / Number | Trailblaze Level / 70 | A |
| `eq_str` / `eq` | Text / Number | Equilibrium / 6 | A |
| `ach_str` / `ach` | Text | Achievements / 597 | A |
| `su_str` / `su` | Text | Simulated Universe / World 9 | A |
| `col_str` / `col` | Text | Collection / 48 chars • 83 LCs | A |
| `sig` | Text | "…" | A |
| `mini` | Text | seria: TB 70 | A |
| `image` | Image | rotating character portrait | A |
| `char` | Text | Firefly • Lv. 80 • E1 | A |
| `moc_str` / `moc` | Text | Memory of Chaos / Floor 12 (36★) | B |
| `pf_str` / `pf` | Text | Pure Fiction / Floor 4 (12★) | B |
| `apc_str` / `apc` | Text | Apocalyptic Shadow / Boss 4 (12★) | B |
| `aa_str` / `aa` | Text | Anomaly Arbitration / 14★ • Gold | B |
| `days_str` / `days` | Text / Number | Active Days / 812 | B |

---

## Setup

### Automated (recommended): the setup wizard

Nearly everything below is automated by one script:

```bash
pip install -r setup_requirements.txt
python setup_wizard.py
```

You enter your HSR UID, the **throwaway** HoyoLab credentials, the Discord app credentials, and a GitHub PAT (classic, scopes `repo` + `workflow`). The wizard logs into HoyoLab once (stoken for permanent cookie auto-refresh), validates your UID on Enka and checks your battle records are public, test-PATCHes the Discord widget, creates the GitHub repo, uploads all files, encrypts and sets every secret, triggers the first run, and writes a local `.env`. Every step is skippable (press Enter).

Only three things stay manual, because no API exists for them: creating the Discord application + Dynamic Profile Widget (step 2 below), setting your main's HoyoLab Battle Chronicle to public, and enabling "Show Character Details" in-game.

### Manual setup

1. **Fork** this repo.
2. **Create a Discord application + Dynamic Profile Widget.** Follow aamiaa's [widget creation script](https://gist.github.com/aamiaa/7cdd590e3949cd654758bc90bcb4710b) (automatic) or chloecinders' [blog post](https://chloecinders.com/blog/discord-widgets) (manual). Bind the fields above.
3. **Add GitHub secrets** (Settings → Secrets and variables → Actions):

   | Secret | Value | Tier |
   |---|---|---|
   | `HSR_UID` | Your 9-digit HSR UID | A |
   | `DISCORD_CLIENT_ID` | Discord Application ID | A |
   | `DISCORD_USER_ID` | Your Discord User ID | A |
   | `DISCORD_BOT_TOKEN` | Discord Bot Token | A |
   | `HOYO_LTUID_V2` | HoyoLab account id (printed by `get_stoken.py`) | B (optional) |
   | `HOYO_MID` | HoyoLab `mid` (printed by `get_stoken.py`) | B, auto-refresh |
   | `HOYO_STOKEN` | Long-lived stoken (printed by `get_stoken.py`) | B, auto-refresh |
   | `HOYO_LTOKEN_V2` | Static `ltoken_v2` cookie — fallback if no stoken | B, fallback |

   In-game, enable **Show Character Details** so your showcase is public.
4. **Run it:** Actions → *Update HSR Widget* → *Run workflow*. After the first success it updates every 6 hours. The Tier B Python step runs only if `HOYO_LTUID_V2` is set.

**Local dev:**
```bash
npm install
cp .env.example .env   # fill in values
node hsrUser.js
```

## How character rotation works

The displayed character advances one showcase slot every `ROTATE_HOURS` (default 6), derived from the clock:
```
+0h  → Firefly • Lv. 80 • E1
+6h  → Fugue • Lv. 80
+12h → Lingsha • Lv. 80
 ...cycles through your showcase, then repeats
```
Reorder your in-game showcase to change the order. If you lower `ROTATE_HOURS`, lower the workflow cron to match.

## APIs & data sources

- **Enka.Network (HSR):** `https://enka.network/api/hsr/uid/{UID}` — public stats + showcase.
- **StarRailRes (Mar-7th):** character names + portraits, keyed by `avatarId`, auto-updating.
- **Discord Widget API:** `PATCH .../identities/0/profile`.
- **genshin.py (Tier B):** HoyoLab-authenticated MoC / PF / APC.

## Project structure

```
HSR-Stats/
├── hsrUser.js                    # Tier A updater (Enka, no login) + merges Tier B if present
├── setup_wizard.py               # automated setup: HoyoLab login -> GitHub repo/secrets -> first run
├── setup_requirements.txt
├── package.json
├── .env.example
├── .gitignore
├── README.md
├── .github/workflows/update.yml  # cron; optional Python step gated on HOYO_LTUID_V2
└── optional-hoyolab/
    ├── hsr_hoyolab.py            # Tier B: MoC/PF/APC via genshin.py -> hoyo_stats.json
    ├── get_stoken.py             # one-time local login -> long-lived stoken (auto-refresh)
    └── requirements.txt
```

## References

- https://github.com/MeYashverma/Genshin-Stats (original)
- https://github.com/toastylol/Genshin-Stats (upstream base)
- https://enka.network/ · https://github.com/EnkaNetwork/API-docs
- https://github.com/Mar-7th/StarRailRes
- https://github.com/thesadru/genshin.py
- https://discord.com/developers/docs
