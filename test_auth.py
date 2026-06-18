# test_auth.py  — run from project root: python test_auth.py
import os, json, requests
from dotenv import load_dotenv
load_dotenv()

CLIENT_ID   = os.getenv("FYERS_CLIENT_ID", "").strip()
APP_ID      = os.getenv("FYERS_APP_ID", "").strip()
TOTP_KEY    = os.getenv("FYERS_TOTP_KEY", "").strip()
PIN         = os.getenv("FYERS_PIN", "").strip()
APP_TYPE    = os.getenv("FYERS_APP_TYPE", "100").strip() or "100"

print("=== Credential check ===")
print(f"FYERS_CLIENT_ID  = '{CLIENT_ID}'   (len={len(CLIENT_ID)})")
print(f"FYERS_APP_ID     = '{APP_ID}'   (len={len(APP_ID)})")
print(f"FYERS_APP_TYPE   = '{APP_TYPE}'")
print(f"FYERS_TOTP_KEY   = '{TOTP_KEY[:6]}...'  (len={len(TOTP_KEY)})")
print(f"FYERS_PIN        = '{PIN}'   (len={len(PIN)})")

print("\n=== Sending OTP request ===")
url = "https://api-t2.fyers.in/vagator/v2/send_login_otp_v2"
payload = {"fy_id": CLIENT_ID, "app_id": "2"}
print(f"URL     : {url}")
print(f"Payload : {json.dumps(payload)}")

r = requests.post(url, json=payload, timeout=15)
print(f"\nStatus  : {r.status_code}")
print(f"Response: {r.text}")

print("\n=== Testing different app_id values ===")
for app_id_test in ["2", "100", APP_TYPE]:
    payload_test = {"fy_id": CLIENT_ID, "app_id": app_id_test}
    print(f"\nPayload (app_id={app_id_test}): {json.dumps(payload_test)}")
    try:
        r_test = requests.post(url, json=payload_test, timeout=15)
        print(f"Status: {r_test.status_code}")
        print(f"Response: {r_test.text}")
    except Exception as e:
        print(f"Error: {e}")

print(f"CLIENT_ID bytes: {CLIENT_ID.encode('utf-8')}")