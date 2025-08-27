#!/usr/bin/env python3
"""Tesla Fleet API - Complete initial setup (mint tokens + partner registration).

This script combines both OAuth flow and partner registration for a complete setup:
1. User OAuth flow to get initial access/refresh tokens
2. Partner registration with Tesla Fleet API
3. Save all tokens to config.yaml

This is a one-time setup. Use tesla_refresh.py for ongoing token refresh.
"""

import asyncio
import http.server
import socketserver
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import aiohttp
import yaml


class OAuthCallbackHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler to catch OAuth callback."""

    def do_GET(self):
        """Handle OAuth callback with authorization code."""
        # Parse query string to get code
        parsed = urllib.parse.urlparse(self.path)
        query_params = urllib.parse.parse_qs(parsed.query)

        if "code" in query_params:
            self.server.auth_code = query_params["code"][0]
            print(f"\n✅ Authorization code received: {self.server.auth_code[:20]}...")
        elif "error" in query_params:
            self.server.auth_error = query_params["error"][0]
            error_desc = query_params.get("error_description", [""])[0]
            print(f"\n❌ OAuth error: {self.server.auth_error}")
            if error_desc:
                print(f"Description: {error_desc}")

        # Send success response
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

        if hasattr(self.server, "auth_code"):
            self.wfile.write(
                b"<html><body><h2>Success!</h2><p>Authorization code received. You can close this window.</p></body></html>"
            )
        else:
            self.wfile.write(
                b"<html><body><h2>Error</h2><p>OAuth authorization failed. Check your terminal.</p></body></html>"
            )

    def log_message(self, format, *args):
        """Suppress HTTP access logs."""
        pass


async def register_partner_account(tesla_config, access_token):
    """Register partner account with Tesla Fleet API."""
    client_id = tesla_config.get("client_id")
    client_secret = tesla_config.get("client_secret")
    auth_endpoint = tesla_config.get(
        "auth_endpoint", "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
    )
    partner_domain = tesla_config.get("partner_domain")

    if not partner_domain:
        print("❌ No partner_domain configured in tesla config")
        print("💡 Add 'partner_domain: your-domain.com' to tesla config")
        return False

    print("\n" + "=" * 50)
    print("🔐 Getting partner token for Fleet API registration...")

    # Get Fleet API endpoints from config
    fleet_endpoints = tesla_config.get(
        "fleet_api_endpoints",
        {
            "na": "https://fleet-api.prd.na.vn.cloud.tesla.com",
            "eu": "https://fleet-api.prd.eu.vn.cloud.tesla.com",
            "ap": "https://fleet-api.prd.ap.vn.cloud.tesla.com",
        },
    )

    # Determine region and endpoints
    region = tesla_config.get("region", "auto")
    if region == "auto":
        # Auto-detect from existing refresh token
        existing_token = tesla_config.get("refresh_token", "")
        if existing_token.startswith("EU_"):
            audience = fleet_endpoints["eu"]
            api_endpoint = fleet_endpoints["eu"]
        elif existing_token.startswith("AP_"):
            audience = fleet_endpoints["ap"]
            api_endpoint = fleet_endpoints["ap"]
        else:
            audience = fleet_endpoints["na"]
            api_endpoint = fleet_endpoints["na"]
    else:
        # Use configured region
        api_endpoint = fleet_endpoints.get(region, fleet_endpoints["na"])
        audience = api_endpoint

    # Get partner token (client credentials)
    token_data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "audience": audience,
        "scope": "openid vehicle_device_data vehicle_cmds vehicle_charging_cmds",
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                auth_endpoint,
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    partner_token = result.get("access_token")
                    if not partner_token:
                        print("❌ No partner token received")
                        return False
                    print(f"✅ Partner token obtained ({partner_token[:10]}...)")
                else:
                    error_text = await response.text()
                    print(f"❌ Failed to get partner token: {response.status}")
                    print(f"Response: {error_text}")
                    return False

        except Exception as e:
            print(f"❌ Partner token error: {e}")
            return False

        # Register partner account with domain
        print(f"🏢 Registering partner account with domain: {partner_domain}")

        registration_data = {"domain": partner_domain}

        try:
            async with session.post(
                f"{api_endpoint}/api/1/partner_accounts",
                json=registration_data,
                headers={
                    "Authorization": f"Bearer {partner_token}",
                    "Content-Type": "application/json",
                },
            ) as response:
                response_text = await response.text()
                if response.status in [200, 201]:
                    print("✅ Partner account registered successfully")
                    print(f"Response: {response_text}")
                    return True
                elif response.status == 409:
                    print("ℹ️  Partner account already registered")
                    print(f"Response: {response_text}")
                    return True
                else:
                    print(f"❌ Partner registration failed: {response.status}")
                    print(f"Response: {response_text}")
                    return False

        except Exception as e:
            print(f"❌ Partner registration error: {e}")
            return False


async def mint_tesla_tokens():
    """Complete Tesla setup: OAuth flow + partner registration."""
    config_path = Path.cwd() / "config.yaml"

    if not config_path.exists():
        print("❌ config.yaml not found")
        return False

    # Load current config
    with open(config_path) as f:
        config = yaml.safe_load(f)

    tesla_config = config.get("tesla", {})

    if not tesla_config.get("enabled", False):
        print("❌ Tesla API is disabled in config.yaml")
        print("💡 Set tesla.enabled: true to enable Tesla API")
        return False

    client_id = tesla_config.get("client_id")
    client_secret = tesla_config.get("client_secret")
    auth_endpoint = tesla_config.get(
        "auth_endpoint", "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
    )
    oauth_authorize_endpoint = tesla_config.get(
        "oauth_authorize_endpoint", "https://auth.tesla.com/oauth2/v3/authorize"
    )

    if not all([client_id, client_secret]):
        print("❌ Tesla API credentials not configured")
        print("💡 Configure client_id and client_secret in config.yaml")
        return False

    # OAuth configuration
    redirect_uri = "http://localhost:8750/callback"
    scopes = [
        "openid",
        "offline_access",
        "vehicle_device_data",  # Critical for reading vehicle data
        "vehicle_cmds",
        "vehicle_charging_cmds",
        "vehicle_location",  # May be needed for some data access
    ]
    scope_string = " ".join(scopes)

    print("🚀 Starting Tesla complete setup (OAuth + Fleet API registration)...")
    print("📋 Scopes requested:", ", ".join(scopes))

    # Step 1: Kill any existing process on port 8750
    PORT = 8750
    print(f"🔧 Checking if port {PORT} is in use...")

    # Kill any process using the port (using same approach as main app)
    try:
        result = subprocess.run(
            f"lsof -ti:{PORT} 2>/dev/null | xargs kill -9 2>/dev/null || true",
            shell=True,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"✅ Cleared port {PORT}")
            # Give processes time to release the port
            await asyncio.sleep(1)
    except Exception:
        pass  # Port might not be in use, which is fine

    print(f"🌐 Starting callback server on http://localhost:{PORT}/callback")

    try:
        with socketserver.TCPServer(("127.0.0.1", PORT), OAuthCallbackHandler) as httpd:
            httpd.auth_code = None
            httpd.auth_error = None

            # Start server in background thread
            server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            server_thread.start()

            # Step 2: Open browser for OAuth authorization
            auth_url = (
                f"{oauth_authorize_endpoint}"
                f"?response_type=code"
                f"&client_id={client_id}"
                f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
                f"&scope={urllib.parse.quote(scope_string)}"
                f"&prompt_missing_scopes=true"  # this allows scope selection
            )

            print("\n🔐 Opening Tesla OAuth authorization in browser...")
            print(f"If browser doesn't open, visit: {auth_url}")

            try:
                webbrowser.open(auth_url)
            except Exception as e:
                print(f"⚠️  Couldn't open browser automatically: {e}")

            print("\n⏳ Waiting for authorization... (complete login in browser)")

            # Wait for callback (with timeout)
            timeout = 300  # 5 minutes
            for _ in range(timeout):
                if hasattr(httpd, "auth_code") and httpd.auth_code:
                    break
                if hasattr(httpd, "auth_error") and httpd.auth_error:
                    return False
                await asyncio.sleep(1)
            else:
                print("❌ OAuth timeout - no authorization received")
                return False

            httpd.shutdown()

            auth_code = httpd.auth_code
            if not auth_code:
                print("❌ No authorization code received")
                return False

    except OSError as e:
        if "Address already in use" in str(e):
            print(f"❌ Port {PORT} is still in use after cleanup attempt")
            print("💡 Try running 'lsof -i :8750' to find the process")
            print("💡 Or wait a moment and try again")
        else:
            print(f"❌ Server error: {e}")
        return False

    # Step 3: Exchange authorization code for tokens
    print("\n🔄 Exchanging authorization code for tokens...")

    token_data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": auth_code,
        "redirect_uri": redirect_uri,
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                auth_endpoint,
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    access_token = result.get("access_token")
                    refresh_token = result.get("refresh_token")
                    expires_in = result.get("expires_in", 3600)

                    if not access_token or not refresh_token:
                        print("❌ Invalid token response - missing tokens")
                        return False

                    print("✅ Tokens received successfully!")
                    print(f"   Access token: {access_token[:20]}...")
                    print(f"   Refresh token: {refresh_token[:20]}...")
                    print(f"   Expires in: {expires_in} seconds")

                else:
                    error_text = await response.text()
                    print(f"❌ Token exchange failed: {response.status}")
                    print(f"Response: {error_text}")
                    return False

        except Exception as e:
            print(f"❌ Token exchange error: {e}")
            return False

    # Step 4: Update config with tokens
    print("\n💾 Updating config.yaml with new tokens...")

    tesla_config["refresh_token"] = refresh_token
    tesla_config["access_token"] = access_token
    tesla_config["token_expires_in"] = expires_in

    # Save config with tokens
    try:
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        print("✅ Config updated with user tokens!")
    except Exception as e:
        print(f"❌ Failed to update config: {e}")
        return False

    # Step 5: Register with Fleet API
    registration_success = await register_partner_account(tesla_config, access_token)

    if registration_success:
        print("\n🎉 Tesla complete setup successful!")
        print("✅ User tokens obtained and saved")
        print("✅ Partner account registered with Fleet API")
        print("\n💡 Use 'make tesla-refresh' for ongoing token refresh")
        return True
    else:
        print("\n⚠️  Tesla OAuth successful but Fleet API registration failed")
        print("✅ User tokens obtained and saved")
        print("❌ Partner registration failed - run 'make tesla-refresh' to retry")
        return True  # Still consider partial success


if __name__ == "__main__":
    try:
        success = asyncio.run(mint_tesla_tokens())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n❌ Interrupted by user")
        sys.exit(1)
