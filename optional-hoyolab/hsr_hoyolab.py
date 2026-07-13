"""
Tier B (optional): pull clear stats — Memory of Chaos / Pure Fiction /
Apocalyptic Shadow — from HoyoLab using genshin.py, and write
hoyo_stats.json next to hsrUser.js.

WHY A COOKIE IS UNAVOIDABLE
---------------------------
HoYo removed challenge data from the in-game showcase packet: the
`recordInfo.challengeInfo` field (MoC floors) is documented as REMOVED in
the MiHoMo API docs, and Enka's HSR endpoint returns `challengeInfo: {}`.
There is no cookie-free source for clear stats anymore. The HoyoLab
battle-record API is the only path.

THE COOKIE DOES NOT HAVE TO BE YOURS (alt-account mode)
-------------------------------------------------------
The record API serves ANY player's clear stats to ANY logged-in cookie,
as long as the *target* UID's battle records are public. So:

  1. Make your main's records public: HoyoLab -> Profile -> Settings ->
     Privacy Settings -> enable "Battle Chronicle" / "Show my battle
     records to others".
  2. Create a throwaway HoyoLab account (no game account needed), grab
     its `ltuid_v2` / `ltoken_v2` cookies, and use those as secrets.

Your main account's cookie never leaves your machine. If the target's
records are private you'll get DataNotPublic (retcode 10102) — fix via
step 1. Rate limit: 30 UIDs per cookie per day (retcode 10101) — far
above what a widget cron needs.

AUTO-REFRESH (recommended): run get_stoken.py ONCE locally to obtain a
long-lived stoken, set HOYO_STOKEN + HOYO_MID + HOYO_LTUID_V2 as secrets,
and this script mints a fresh ltoken_v2 on every run — the cookie never
goes stale again. Without a stoken it falls back to a static
HOYO_LTOKEN_V2 secret (which expires periodically).
"""

import asyncio
import json
import os
import time
from pathlib import Path

import genshin


_MAX_STARS = {"moc": 36, "pf": 12, "apc": 12}  # 12x3 / 4x3 / 4x3


def _fmt(mode, key="moc") -> str | None:
    """Format stars only, like '36⭐/36⭐' (MoC) or '12⭐/12⭐' (PF/APC)."""
    if mode is None or getattr(mode, "has_data", True) is False:
        return None
    stars = getattr(mode, "total_stars", 0) or 0
    return f"{stars}⭐/{_MAX_STARS.get(key, 36)}⭐"


def _score(mode) -> str | None:
    """Score of the HIGHEST stage only (stage 4 if reached), e.g. '38,000 pts'.

    Early PF stages are trivially full-cleared and APC is AV-based with no
    perfect clear, so the final stage's score is the meaningful number.
    Falls back to the highest stage attempted if stage 4 isn't reached.
    """
    if mode is None or getattr(mode, "has_data", True) is False:
        return None
    floors = getattr(mode, "floors", None) or []
    if not floors:
        return None
    top = max(floors, key=lambda f: getattr(f, "id", 0) or 0)  # id rises with stage
    score = getattr(top, "score", 0) or 0
    return f"{score:,} pts" if score else None


def _season_name(mode) -> str:
    """Current season's display name, e.g. 'Gale of Forgetting'."""
    seasons = getattr(mode, "seasons", None) or []
    if seasons:
        return (getattr(seasons[0], "name", "") or "").strip()
    return (getattr(mode, "name", "") or "").strip()


def _cycles(mode) -> int:
    """Cycles CONSUMED across all cleared floors (sum of per-floor round_num)."""
    return sum((getattr(f, "round_num", 0) or 0) for f in (getattr(mode, "floors", None) or []))


def _detail(mode, key) -> str | None:
    """Label line. MoC: 'Season • 36⭐/36⭐ • 48 cycles' (cycles consumed).
    PF:  'Season • 12⭐/12⭐ • 115,640 pts' (points make cycles irrelevant).
    APC: 'Season • 12⭐/12⭐ • 10,451 pts' (AV-based, no cycles)."""
    if mode is None or getattr(mode, "has_data", True) is False:
        return None
    parts = []
    if (name := _season_name(mode)):
        parts.append(name)
    if (stars := _fmt(mode, key)):
        parts.append(stars)
    if key in ("pf", "apc") and (pts := _score(mode)):
        parts.append(pts)
    if key == "moc" and (c := _cycles(mode)):
        parts.append(f"{c} cycles")
    return " • ".join(parts) if parts else None


def _anomaly_parts(data) -> tuple[str | None, str | None]:
    """Parse Anomaly Arbitration from the RAW API payload.

    Returns (total, detail):
      total  -> '7⭐' or '12⭐ • Gold' (mob + boss stars, medal if earned)
      detail -> 'Season • Knights 6⭐/9⭐ • King 1⭐ • 5 cycles'
                (knight stages 1-3 = mob_stars, max 9; king = boss_stars;
                 cycles = battle_num of the current season's record)

    Prefers the BEST-RECORD brief (all-time best). Season name and cycles
    are attached from the season record that matches the brief's stars, so
    they describe the same run; if no record matches, stars/medal show alone.
    Falls back to the newest season record if the brief is empty.
    """
    if not data:
        print("Anomaly Arbitration: API returned an empty payload.")
        return None, None

    recs = [r for r in (data.get("challenge_peak_records") or [])
            if r.get("has_challenge_record")]
    brief = data.get("challenge_peak_best_record_brief") or {}

    mob = brief.get("mob_stars") or 0
    boss = brief.get("boss_stars") or 0
    medal_raw = brief.get("challenge_peak_rank_icon_type") or ""
    rec = None
    if mob or boss or medal_raw:
        # best record: find the season record it came from (matching stars)
        rec = next((r for r in recs
                    if (r.get("mob_stars") or 0) == mob
                    and (r.get("boss_stars") or 0) == boss), None)
    elif recs:
        # no brief: fall back to the newest season record
        rec = recs[0]
        mob = rec.get("mob_stars") or 0
        boss = rec.get("boss_stars") or 0

    if rec is not None and not medal_raw:
        medal_raw = ((rec.get("boss_record") or {}).get("challenge_peak_rank_icon_type") or "")
    medal = medal_raw.replace("_", " ").strip().title()
    if not (mob or boss or medal):
        print("Anomaly Arbitration: no clear record in response; keys:", list(data.keys()))
        return None, None

    total = f"{mob + boss}⭐ • {medal}" if medal else f"{mob + boss}⭐"

    parts = []
    season = (((rec or {}).get("group") or {}).get("name_mi18n") or "").strip()
    if season:
        parts.append(season)
    parts.append(f"Knights {mob}⭐/9⭐ • King {boss}⭐")
    cycles = (rec or {}).get("battle_num") or 0
    if cycles:
        parts.append(f"{cycles} cycles")
    return total, " • ".join(parts)


async def _grab(label: str, coro, out: dict, key: str, fmt=_fmt) -> None:
    try:
        value = fmt(await coro)
        if value:
            out[key] = value
    except genshin.errors.DataNotPublic:
        print(
            f"{label}: target UID's battle records are PRIVATE. "
            "Enable them: HoyoLab -> Settings -> Privacy Settings -> Battle Chronicle."
        )
    except genshin.errors.InvalidCookies:
        print(f"{label}: cookie expired/invalid — refresh HOYO_LTUID_V2 / HOYO_LTOKEN_V2 secrets.")
    except Exception as e:  # noqa: BLE001
        print(f"{label} fetch failed:", e)


async def _build_cookies() -> dict:
    """Cookie auto-refresh: prefer minting a fresh ltoken_v2 from a stoken.

    Any HoyoLab account's cookie works — including a throwaway alt —
    provided the target UID's battle records are public.
    """
    ltuid = os.environ["HOYO_LTUID_V2"]
    stoken = os.environ.get("HOYO_STOKEN", "").strip()
    mid = os.environ.get("HOYO_MID", "").strip()

    if stoken and mid:
        # stoken -> fresh ltoken_v2 (token_type=2). The stoken lives until
        # the account password changes, so this never goes stale.
        from genshin.client.manager.cookie import fetch_cookie_with_stoken_v2

        fresh = await fetch_cookie_with_stoken_v2(
            {"stoken": stoken, "mid": mid}, token_types=[2]
        )
        print("Auto-refreshed ltoken_v2 from stoken.")
        return {"ltuid_v2": ltuid, "ltmid_v2": mid, "ltoken_v2": fresh["ltoken_v2"]}

    # Fallback: static cookie (expires periodically — see get_stoken.py).
    print("No HOYO_STOKEN/HOYO_MID set — using static HOYO_LTOKEN_V2 (may expire).")
    return {"ltuid_v2": ltuid, "ltoken_v2": os.environ["HOYO_LTOKEN_V2"]}


async def main() -> None:
    uid = int(os.environ["HSR_UID"])  # the account to DISPLAY (target)
    try:
        cookies = await _build_cookies()
    except genshin.errors.InvalidCookies:
        raise SystemExit(
            "stoken rejected — it is revoked (password changed?). "
            "Re-run optional-hoyolab/get_stoken.py locally and update the secrets."
        )
    client = genshin.Client(cookies, game=genshin.Game.STARRAIL)

    out: dict = {"generated_at": int(time.time())}

    # Clear stats. For each mode we emit:
    #   <key>        stars, e.g. "12⭐/12⭐"          (bind as Value)
    #   <key>_pts    top-stage score (PF/APC only)
    #   <key>_detail "Season • stars • pts • cycles"  (bind as Label)
    async def mode_fields(label, coro, key):
        res = {}
        await _grab(label, coro, res, "mode", fmt=lambda m: m)
        mode = res.get("mode")
        if mode is None:
            return
        if (v := _fmt(mode, key)):
            out[key] = v
        if key in ("pf", "apc") and (p := _score(mode)):
            out[f"{key}_pts"] = p
        if (d := _detail(mode, key)):
            out[f"{key}_detail"] = d

    await mode_fields("Memory of Chaos", client.get_starrail_challenge(uid), "moc")
    await mode_fields("Pure Fiction", client.get_starrail_pure_fiction(uid), "pf")
    await mode_fields("Apocalyptic Shadow", client.get_starrail_apc_shadow(uid), "apc")
    aa_res = {}
    await _grab("Anomaly Arbitration", client.get_anomaly_arbitration(uid, raw=True),
                aa_res, "raw", fmt=lambda d: d)
    if aa_res.get("raw") is not None:
        total, detail = _anomaly_parts(aa_res["raw"])
        if total:
            out["aa"] = total
        if detail:
            out["aa_detail"] = detail

    # General stats (active days etc.)
    try:
        user = await client.get_starrail_user(uid)
        out["active_days"] = user.stats.active_days
    except Exception as e:  # noqa: BLE001
        print("Stats fetch failed:", e)

    # Write next to hsrUser.js (repo root = parent of this file's folder)
    target = Path(__file__).resolve().parent.parent / "hoyo_stats.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Wrote", target, "->", out)


if __name__ == "__main__":
    asyncio.run(main())
