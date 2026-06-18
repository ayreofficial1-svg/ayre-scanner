"""
Fyers App Activation with Local Server

Run once after creating the API app to authorize it via OAuth.
This script:
1. Starts a local HTTP server on port 8080
2. Opens your browser for Fyers OAuth login
3. Captures the auth code automatically
4. Stores it for the scanner to use

If port 8080 is busy, you can manually edit the PORT variable below.
"""

import urllib.parse
import webbrowser
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import time
import sys

from config.settings import FYERS_APP_ID_FULL, FYERS_SECRET_KEY, FYERS_CLIENT_ID

# ── Configuration ──────────────────────────────────────────────────────────
PORT = 8080
REDIRECT_URI = f"http://127.0.0.1:{PORT}"

# Global to capture the auth code
captured_auth_code = None
capture_event = threading.Event()


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handle OAuth redirect from Fyers"""
    
    def do_GET(self):
        global captured_auth_code
        
        # Parse the query parameters
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)
        
        auth_code = query_params.get("auth_code", [None])[0]
        
        if auth_code:
            captured_auth_code = auth_code
            
            # Send success response to browser
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            html_success = """
            <html>
            <head><title>Fyers Authorization Success</title></head>
            <body style="font-family: Arial, sans-serif; padding: 40px; text-align: center;">
                <h1 style="color: green;">Authorization Successful!</h1>
                <p style="font-size: 16px;">
                    Your Fyers app has been authorized. The scanner can now use automated login.
                </p>
                <p style="color: gray; font-size: 14px;">You can close this window.</p>
            </body>
            </html>
            """
            self.wfile.write(html_success.encode('utf-8'))
            
            # Signal that we got the code
            capture_event.set()
        else:
            # Send error response
            self.send_response(400)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            html_error = """
            <html>
            <head><title>Authorization Failed</title></head>
            <body style="font-family: Arial, sans-serif; padding: 40px; text-align: center;">
                <h1 style="color: red;">Authorization Failed</h1>
                <p>No auth code received. Please try again.</p>
            </body>
            </html>
            """
            self.wfile.write(html_error.encode('utf-8'))
    
    def log_message(self, format, *args):
        """Suppress default logging"""
        pass


def start_server():
    """Start the local HTTP server"""
    server = HTTPServer(("127.0.0.1", PORT), OAuthCallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main():
    print("\n" + "=" * 70)
    print("Fyers App Authorization")
    print("=" * 70 + "\n")
    
    print(f"Starting local server on {REDIRECT_URI}...")
    server = start_server()
    time.sleep(1)  # Give server time to start
    print("[OK] Server started\n")
    
    # Build OAuth URL
    params = urllib.parse.urlencode(
        {
            "client_id": FYERS_APP_ID_FULL,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "state": "activate",
        }
    )
    
    oauth_url = f"https://api-t1.fyers.in/api/v3/generate-authcode?{params}"
    
    print("Opening browser for Fyers login...")
    print(f"URL: {oauth_url}\n")
    
    try:
        webbrowser.open(oauth_url)
    except Exception as e:
        print(f"Could not open browser: {e}")
        print(f"Open this URL manually:\n{oauth_url}\n")
    
    print("Waiting for authorization (max 5 minutes)...\n")
    
    # Wait for the auth code (timeout after 5 minutes)
    if capture_event.wait(timeout=300):
        print(f"\n[SUCCESS] Authorization received!")
        print(f"Auth Code: {captured_auth_code[:20]}...\n")
        
        print("Your Fyers app is now authorized.")
        print("You can now run: python main.py\n")
        
        server.shutdown()
        return 0
    else:
        print("\n[FAILED] Authorization timeout (5 minutes expired)")
        print("Please try again.\n")
        server.shutdown()
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nCanceled by user\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] {e}\n")
        sys.exit(1)
