#!/usr/bin/env python3
"""
HSR-Stats setup wizard — automates (nearly) the whole setup.

    pip install -r setup_requirements.txt
    python setup_wizard.py

You enter:
  - your HSR UID
  - THROWAWAY HoyoLab credentials  (see README "alt-account mode")
  - Discord app credentials        (client id / user id / bot token)
  - a GitHub personal access token (classic PAT, scopes: repo + workflow)

The wizard then:
  1. Logs into HoyoLab once -> long-lived stoken (captcha, if triggered,
     opens in your browser; genshin.py handles it).
  2. Validates your UID on Enka and checks your battle records are public
     (test-fetches Memory of Chaos with the new cookie).
  3. Verifies the Discord credentials with a test widget PATCH.
  4. Creates the GitHub repo, uploads all project files, encrypts + sets
     every secret, and triggers the first workflow run.
  5. Writes a local .env so `node hsrUser.js` works on your machine too.

Still manual (no API exists for these):
  - Creating the Discord application + Dynamic Profile Widget (README step 2).
  - HoyoLab privacy toggle on your MAIN account (Battle Chronicle -> public).
  - In-game "Show Character Details" for the showcase.

Every step is skippable — press Enter at a prompt to skip that section.
"""

import asyncio
import base64
import getpass
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:  # installed by ensure_deps() on first run
    requests = None

ROOT = Path(__file__).resolve().parent
GH_API = "https://api.github.com"
UA = {"User-Agent": "HSR-Stats-SetupWizard/1.0"}

# Files pushed to the new repo (relative to this script's folder).
REPO_FILES = [
    ".gitignore",
    "hsrUser.js",
    "package.json",
    "package-lock.json",
    ".env.example",
    "README.md",
    ".github/workflows/update.yml",
    "optional-hoyolab/hsr_hoyolab.py",
    "optional-hoyolab/get_stoken.py",
    "optional-hoyolab/requirements.txt",
    "setup_wizard.py",
    "setup_requirements.txt",
]


def say(msg: str) -> None:
    print(f"\n=== {msg} ===")


def ask(prompt: str, secret: bool = False) -> str:
    if secret:
        # getpass hides input completely — warn so it doesn't look frozen.
        print("  (typing is HIDDEN — nothing appears as you type; press Enter when done)")
        try:
            return getpass.getpass(f"{prompt}: ").strip()
        except Exception:
            # Some terminals (e.g. certain IDE consoles) can't do hidden input.
            return input(f"{prompt} (visible): ").strip()
    return input(f"{prompt}: ").strip()


def free_port() -> int:
    """Find a free localhost port for the captcha-solver page (5000 is often taken)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ensure_deps() -> None:
    """Check required packages; offer to install any that are missing."""
    missing = []
    # rsa is genshin.py's optional extra, required for password login.
    for module, package in (
        ("genshin", "genshin"), ("rsa", "rsa"), ("requests", "requests"), ("nacl", "pynacl"),
    ):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if not missing:
        return

    print(f"Missing required packages: {', '.join(missing)}")
    choice = input("Install them now with pip? [Y/n]: ").strip().lower()
    if choice not in ("", "y", "yes"):
        sys.exit(f"Install manually, then re-run:\n  {sys.executable} -m pip install {' '.join(missing)}")

    # Use THIS interpreter's pip so it lands in the right Python.
    subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
    global requests
    import requests  # noqa: PLC0415
    print("Installed. Continuing...\n")


# ---------------------------------------------------------------------------
# Step 1 — HoyoLab: throwaway credentials -> long-lived stoken
# ---------------------------------------------------------------------------
async def hoyolab_login() -> dict | None:
    say("Step 1/5 — HoyoLab login (throwaway account)")
    email = ask("HoyoLab email (Enter to skip this step)")
    if not email:
        return None
    password = ask("HoyoLab password", secret=True)

    import genshin

    client = genshin.Client()
    port = free_port()
    print(f"Logging in... (a captcha page may open at http://localhost:{port} —")
    print(" solve it in your browser; an emailed code may also be prompted here)")
    result = await client.login_with_app_password(email, password, port=port)
    print("Login OK — got long-lived stoken.")
    return {
        "HOYO_LTUID_V2": result.ltuid_v2,
        "HOYO_MID": result.ltmid_v2,
        "HOYO_STOKEN": result.stoken,
    }


# ---------------------------------------------------------------------------
# Step 2 — Validate target UID (Enka public + battle records public)
# ---------------------------------------------------------------------------
async def validate_uid(uid: str, hoyo: dict | None) -> None:
    """Sanity checks. Warnings only — validation must never kill the wizard."""
    say("Step 2/5 — Validating your UID")

    try:
        r = requests.get(f"https://enka.network/api/hsr/uid/{uid}", headers=UA, timeout=15)
        if r.ok and r.json().get("detailInfo"):
            d = r.json()["detailInfo"]
            n_show = len(d.get("avatarDetailList", []))
            print(f"Enka OK: {d.get('nickname')} (TB {d.get('level')}), {n_show} showcase characters.")
            if n_show == 0:
                print("  WARNING: showcase is empty — enable 'Show Character Details' in-game.")
        else:
            print(f"  WARNING: Enka returned {r.status_code} — check the UID / profile visibility.")
    except Exception as e:  # noqa: BLE001
        print(f"  WARNING: couldn't reach Enka ({type(e).__name__}) — skipping this check.")

    if not hoyo:
        return

    try:
        import genshin
        from genshin.client.manager.cookie import fetch_cookie_with_stoken_v2

        fresh = await fetch_cookie_with_stoken_v2(
            {"stoken": hoyo["HOYO_STOKEN"], "mid": hoyo["HOYO_MID"]}, token_types=[2]
        )
        client = genshin.Client(
            {"ltuid_v2": hoyo["HOYO_LTUID_V2"], "ltmid_v2": hoyo["HOYO_MID"],
             "ltoken_v2": fresh["ltoken_v2"]},
            game=genshin.Game.STARRAIL,
        )
        moc = await client.get_starrail_challenge(int(uid))
        stars = getattr(moc, "total_stars", "?")
        print(f"Battle records OK: Memory of Chaos reachable ({stars} stars this season).")
    except Exception as e:  # noqa: BLE001
        import genshin
        if isinstance(e, genshin.errors.DataNotPublic):
            print("  ACTION NEEDED: your MAIN account's battle records are PRIVATE.")
            print("  HoyoLab -> Profile -> Settings -> Privacy -> enable Battle Chronicle,")
            print("  then clear stats will appear on the next scheduled run (no redo needed).")
        else:
            print(f"  WARNING: battle-record check failed ({type(e).__name__}: {e}) — continuing.")


# ---------------------------------------------------------------------------
# Step 3 — Discord credential check (test PATCH)
# ---------------------------------------------------------------------------
def discord_check(client_id: str, user_id: str, bot_token: str, uid: str) -> bool:
    say("Step 3/5 — Verifying Discord credentials")
    url = (f"https://discord.com/api/v9/applications/{client_id}"
           f"/users/{user_id}/identities/0/profile")
    payload = {"data": {"dynamic": [
        {"type": 1, "name": "nickname", "value": "HSR-Stats setup OK"},
        {"type": 1, "name": "uid", "value": f"UID {uid}"},
    ]}}
    try:
        r = requests.patch(url, json=payload, timeout=15, headers={
            "Authorization": f"Bot {bot_token}", "Content-Type": "application/json", **UA,
        })
    except Exception as e:  # noqa: BLE001
        print(f"  WARNING: couldn't reach Discord ({type(e).__name__}) — continuing anyway.")
        return False
    if r.ok:
        print("Discord OK — test PATCH accepted (first real run replaces it).")
        return True
    print(f"  WARNING: Discord PATCH failed ({r.status_code}): {r.text[:200]}")
    print("  Did you create the app + widget and bind the fields? (README step 2)")
    return False


# ---------------------------------------------------------------------------
# Step 4 — GitHub: repo + files + encrypted secrets + first run
# ---------------------------------------------------------------------------
def gh(pat: str, method: str, path: str, **kw):
    r = requests.request(method, f"{GH_API}{path}", timeout=30, headers={
        "Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json", **UA,
    }, **kw)
    return r


def encrypt_secret(repo_public_key_b64: str, value: str) -> str:
    """Encrypt a secret with the repo's public key (libsodium sealed box)."""
    from nacl import encoding, public

    pk = public.PublicKey(repo_public_key_b64.encode(), encoding.Base64Encoder())
    sealed = public.SealedBox(pk).encrypt(value.encode())
    return base64.b64encode(sealed).decode()


def github_setup(pat: str, repo_name: str, secrets: dict) -> None:
    say("Step 4/5 — GitHub repo + secrets")

    me = gh(pat, "GET", "/user")
    me.raise_for_status()
    owner = me.json()["login"]
    full = f"{owner}/{repo_name}"

    # Create repo (or reuse if it already exists)
    r = gh(pat, "POST", "/user/repos", json={
        "name": repo_name, "private": True,
        "description": "HSR Discord profile widget (auto-generated by HSR-Stats setup wizard)",
    })
    if r.status_code == 201:
        print(f"Created private repo {full}")
    elif r.status_code == 422:
        print(f"Repo {full} already exists — reusing it.")
    else:
        r.raise_for_status()

    # Upload files (create or update)
    for rel in REPO_FILES:
        fp = ROOT / rel
        if not fp.exists():
            print(f"  skip (missing locally): {rel}")
            continue
        content = base64.b64encode(fp.read_bytes()).decode()
        existing = gh(pat, "GET", f"/repos/{full}/contents/{rel}")
        body = {"message": f"setup wizard: add {rel}", "content": content}
        if existing.ok:
            body["sha"] = existing.json()["sha"]
        put = gh(pat, "PUT", f"/repos/{full}/contents/{rel}", json=body)
        if put.ok:
            print(f"  pushed {rel}")
        elif put.status_code == 403 and "workflow" in rel:
            raise SystemExit(
                "  PAT lacks the 'workflow' scope (required to push .github/workflows). "
                "Create a classic PAT with scopes: repo + workflow."
            )
        else:
            put.raise_for_status()

    # Encrypted secrets
    key = gh(pat, "GET", f"/repos/{full}/actions/secrets/public-key")
    key.raise_for_status()
    key_id, key_b64 = key.json()["key_id"], key.json()["key"]
    for name, value in secrets.items():
        if not value:
            continue
        put = gh(pat, "PUT", f"/repos/{full}/actions/secrets/{name}", json={
            "encrypted_value": encrypt_secret(key_b64, str(value)), "key_id": key_id,
        })
        put.raise_for_status()
        print(f"  secret set: {name}")

    # First run
    print("Triggering first workflow run...")
    for attempt in range(6):
        r = gh(pat, "POST", f"/repos/{full}/actions/workflows/update.yml/dispatches",
               json={"ref": "main"})
        if r.status_code == 204:
            print(f"Workflow dispatched — watch it at https://github.com/{full}/actions")
            return
        time.sleep(5)  # workflow file may take a moment to be indexed
    print(f"  Couldn't auto-dispatch ({r.status_code}). Run it manually: "
          f"https://github.com/{full}/actions -> Update HSR Widget -> Run workflow")


# ---------------------------------------------------------------------------
# Step 5 — local .env
# ---------------------------------------------------------------------------
def write_env(secrets: dict) -> None:
    say("Step 5/5 — Writing local .env")
    lines = [f"{k}={v}" for k, v in secrets.items() if v]
    (ROOT / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {ROOT / '.env'} (git-ignored; for local `node hsrUser.js` runs).")


# ---------------------------------------------------------------------------
async def main() -> None:
    print(__doc__)
    ensure_deps()

    uid = ask("Your HSR UID (9 digits)")
    if not uid.isdigit():
        sys.exit("UID must be numeric.")

    try:
        hoyo = await hoyolab_login()
    except Exception as e:  # noqa: BLE001
        print(f"\nHoyoLab login failed: {type(e).__name__}: {e}")
        if input("Continue WITHOUT clear stats (MoC/PF/AS/AA)? [y/N]: ").strip().lower() not in ("y", "yes"):
            sys.exit("Aborted — fix the login issue and re-run.")
        hoyo = None
    await validate_uid(uid, hoyo)

    say("Discord credentials (create app + widget first — README step 2)")
    client_id = ask("Discord Application (client) ID (Enter to skip)")
    user_id = bot_token = ""
    if client_id:
        user_id = ask("Your Discord user ID")
        bot_token = ask("Discord bot token", secret=True)
        discord_check(client_id, user_id, bot_token, uid)

    secrets = {
        "HSR_UID": uid,
        "DISCORD_CLIENT_ID": client_id,
        "DISCORD_USER_ID": user_id,
        "DISCORD_BOT_TOKEN": bot_token,
        **(hoyo or {}),
    }

    say("GitHub (classic PAT with scopes: repo + workflow)")
    pat = ask("GitHub PAT (Enter to skip — secrets will be printed instead)", secret=True)
    if pat:
        repo_name = ask("Repo name to create/use [HSR-Stats]") or "HSR-Stats"
        try:
            github_setup(pat, repo_name, secrets)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"\nGitHub setup failed: {type(e).__name__}: {e}")
            print("Nothing is lost — your secrets are below; set them manually or re-run.")
            for k, v in secrets.items():
                if v:
                    print(f"  {k} = {v}")
    else:
        print("\nSet these secrets manually (GitHub -> Settings -> Secrets -> Actions):")
        for k, v in secrets.items():
            if v:
                print(f"  {k} = {v}")

    write_env(secrets)

    say("Done — remaining manual steps (if not done already)")
    print(" 1. Discord app + Dynamic Profile Widget + field binding  (README step 2)")
    print(" 2. Main account: HoyoLab privacy -> Battle Chronicle public")
    print(" 3. In-game: enable 'Show Character Details' (showcase)")
    print("The widget updates every 6h from now on. Cookie refresh is automatic.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit("\nAborted.")
