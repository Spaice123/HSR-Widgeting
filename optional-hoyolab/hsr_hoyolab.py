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
    """Total season score across all stages, e.g. '30,000 pts' (PF/APC only)."""
    if mode is None or getattr(mode, "has_data", True) is False:
        return None
    score = sum((getattr(f, "score", 0) or 0) for f in (getattr(mode, "floors", None) or []))
    return f"{score:,} pts" if score else None


def _fmt_anomaly(aa) -> str | None:
    """Format Anomaly Arbitration like '14★ • Gold' (stars + best medal)."""
    if aa is None:
        return None
    s = getattr(aa, "summary", None)  # best-record brief
    if s is not None:
        stars = getattr(s, "boss_stars", 0) + getattr(s, "mini_boss_stars", 0)
        medal = (getattr(s, "medal_type", "") or "").replace("_", " ").strip().title()
        return f"{stars}⭐/14⭐ • {medal}" if medal else f"{stars}⭐/14⭐"
    # No summary yet: fall back to the most recent season record with data.
    recs = [r for r in getattr(aa, "records", []) if getattr(r, "has_data", False)]
    if not recs:
        return None
    r = recs[0]
    return f"{getattr(r, 'boss_stars', 0) + getattr(r, 'mini_boss_stars', 0)}⭐/14⭐"


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

    # Clear stats: current-season Memory of Chaos / Pure Fiction / Apocalyptic
    # Shadow. previous=True variants exist if you ever want last season.
    await _grab("Memory of Chaos", client.get_starrail_challenge(uid), out, "moc",
                fmt=lambda m: _fmt(m, "moc"))

    # PF / APC: stars in `pf`/`apc`, total score separately in `pf_pts`/`apc_pts`
    # so the widget can show points as the label under the star value.
    pf_res = {}
    await _grab("Pure Fiction", client.get_starrail_pure_fiction(uid), pf_res, "mode",
                fmt=lambda m: m)
    if pf_res.get("mode") is not None:
        if (v := _fmt(pf_res["mode"], "pf")):
            out["pf"] = v
        if (p := _score(pf_res["mode"])):
            out["pf_pts"] = p

    apc_res = {}
    await _grab("Apocalyptic Shadow", client.get_starrail_apc_shadow(uid), apc_res, "mode",
                fmt=lambda m: m)
    if apc_res.get("mode") is not None:
        if (v := _fmt(apc_res["mode"], "apc")):
            out["apc"] = v
        if (p := _score(apc_res["mode"])):
            out["apc_pts"] = p
    await _grab(
        "Anomaly Arbitration", client.get_anomaly_arbitration(uid), out, "aa",
        fmt=_fmt_anomaly,
    )

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
