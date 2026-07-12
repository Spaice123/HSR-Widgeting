"""
ONE-TIME SETUP (run locally, never in CI): obtain a long-lived `stoken`
so the widget can auto-refresh its HoyoLab cookie forever.

Why this exists
---------------
`ltoken_v2` (the cookie the record API wants) expires. But HoyoLab's app
login also issues an `stoken` — a refresh token that stays valid until
the account's password changes. With it, CI can mint a brand-new
ltoken_v2 on every run (genshin.py `fetch_cookie_with_stoken_v2`),
headlessly, with no captcha. Cookie expiry stops being your problem.

Why LOCALLY: the login may trigger a Geetest captcha and/or an email
verification code — genshin.py opens http://localhost:5000 for the
captcha and prompts for the code in the terminal. Neither is possible
on a headless GitHub runner. Login once here; CI never logs in again.

Usage
-----
    pip install -r requirements.txt
    python get_stoken.py          # prompts for email + password
    # or non-interactively:
    HOYO_EMAIL=... HOYO_PASSWORD=... python get_stoken.py

Use a THROWAWAY HoyoLab account (see README "alt-account mode") — then
even the stoken guards nothing of value. Set the three printed values
as GitHub secrets and you're done, permanently.
"""

import asyncio
import getpass
import os
import socket

import genshin


def free_port() -> int:
    """Find a free localhost port for the captcha-solver page (5000 is often taken)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def main() -> None:
    email = os.environ.get("HOYO_EMAIL") or input("HoyoLab email: ")
    password = os.environ.get("HOYO_PASSWORD") or getpass.getpass("HoyoLab password: ")

    client = genshin.Client()
    port = free_port()
    print(f"Logging in (a browser page may open at http://localhost:{port} if a captcha triggers)...")
    result = await client.login_with_app_password(email, password, port=port)

    print("\nSuccess! Set these three GitHub secrets (Settings -> Secrets -> Actions):\n")
    print(f"  HOYO_LTUID_V2 = {result.ltuid_v2}")
    print(f"  HOYO_MID      = {result.ltmid_v2}")
    print(f"  HOYO_STOKEN   = {result.stoken}")
    print("\nYou can now DELETE the HOYO_LTOKEN_V2 secret if you had one —")
    print("the workflow mints a fresh ltoken_v2 from the stoken on every run.")
    print("This stoken stays valid until the account's password changes.")


if __name__ == "__main__":
    asyncio.run(main())
