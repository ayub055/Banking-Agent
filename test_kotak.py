"""Standalone connectivity test for the Kotak Model Gateway.

Runs three checks in order and prints a clear PASS/FAIL for each:
  1. DNS  — can this machine resolve the IDAM + model hostnames?
  2. Auth — can it generate an OAuth2 bearer token?
  3. Call — can it get a completion back from the model?

Usage:
    python test_kotak.py

Reads the same KOTAK_* values the app uses (config/settings.py / env vars).
Fill them in there, or override inline at the top of this file.
"""

import socket
from urllib.parse import urlparse

# Pull the exact config the app uses. Override any of these here if you prefer.
from config.settings import (
    KOTAK_TOKEN_URL, KOTAK_API_URL, KOTAK_CLIENT_ID, KOTAK_CLIENT_SECRET,
    KOTAK_CA_BUNDLE, KOTAK_MODEL, KOTAK_MAX_TOKENS,
)
from script import KotakOAuth2Handler, KotakAIWrapper


def _mask(s: str) -> str:
    return (s[:4] + "…" + s[-2:]) if s and len(s) > 6 else ("<empty>" if not s else "***")


def check_dns(url: str) -> bool:
    host = urlparse(url).hostname
    try:
        ip = socket.gethostbyname(host)
        print(f"   PASS  {host} -> {ip}")
        return True
    except Exception as e:
        print(f"   FAIL  {host} -> {e}")
        return False


def main():
    print("=" * 60)
    print("KOTAK MODEL GATEWAY — CONNECTIVITY TEST")
    print("=" * 60)
    print(f"Token URL : {KOTAK_TOKEN_URL}")
    print(f"API URL   : {KOTAK_API_URL}")
    print(f"Client ID : {_mask(KOTAK_CLIENT_ID)}")
    print(f"Secret    : {_mask(KOTAK_CLIENT_SECRET)}")
    print(f"CA bundle : {KOTAK_CA_BUNDLE or '(system default)'}")
    print(f"Model     : {KOTAK_MODEL}")
    print()

    if not KOTAK_CLIENT_ID or not KOTAK_CLIENT_SECRET:
        print("!! client_id / client_secret are empty — set them in config/settings.py "
              "or via KOTAK_CLIENT_ID / KOTAK_CLIENT_SECRET env vars.\n")

    # ---- 1. DNS -----------------------------------------------------------
    print("[1/3] DNS resolution")
    dns_ok = check_dns(KOTAK_TOKEN_URL) & check_dns(KOTAK_API_URL)
    if not dns_ok:
        print("\nRESULT: DNS FAILED — the host(s) can't be resolved from this machine.")
        print("You are likely not on the Kotak internal network (need VPN/proxy/VPC),")
        print("or the URL is wrong. Fix connectivity before testing auth.\n")
        return
    print()

    # ---- 2. Auth ----------------------------------------------------------
    print("[2/3] OAuth2 token")
    oauth = KotakOAuth2Handler(
        token_url=KOTAK_TOKEN_URL,
        client_id=KOTAK_CLIENT_ID,
        client_secret=KOTAK_CLIENT_SECRET,
        ca_bundle=KOTAK_CA_BUNDLE or None,
    )
    try:
        token = oauth.generate_token()
        print(f"   PASS  token acquired ({_mask(token)}), expires {oauth.token_expires_at}")
    except Exception as e:
        print(f"   FAIL  {e}")
        print("\nRESULT: AUTH FAILED — DNS works but the token endpoint rejected/timed out.")
        print("Check the client_id/secret, the token URL, and the CA bundle path.\n")
        return
    print()

    # ---- 3. Model call ----------------------------------------------------
    print("[3/3] Model completion")
    wrapper = KotakAIWrapper(
        api_url=KOTAK_API_URL,
        oauth2_handler=oauth,
        ca_bundle=KOTAK_CA_BUNDLE or None,
    )
    try:
        resp = wrapper.send_message(
            message="Reply with exactly the word: OK",
            model=KOTAK_MODEL,
            max_tokens=KOTAK_MAX_TOKENS,
            temperature=0,
        )
        print("   PASS  raw response:")
        print("   " + str(resp)[:800])
        print("\nRESULT: SUCCESS — you can call the Kotak Model Gateway from this machine.\n")
    except Exception as e:
        print(f"   FAIL  {e}")
        print("\nRESULT: MODEL CALL FAILED — token worked but the model endpoint errored.")
        print("Check the API URL/model name and whether your token is authorised for it.\n")


if __name__ == "__main__":
    main()
