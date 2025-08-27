#!/usr/bin/env python3
"""Tesla Fleet API - Ongoing token refresh and registration verification.

This script handles ongoing maintenance:
1. Refresh user access_token using existing refresh_token
2. Verify/re-register partner account with Tesla Fleet API (idempotent)

Run periodically to keep tokens fresh and registration active.
"""

import asyncio
import sys
from pathlib import Path

import aiohttp
import yaml


async def refresh_user_token(tesla_config, config, config_path):
    """Refresh user access token using refresh_token."""
    refresh_token = tesla_config.get("refresh_token")
    client_id = tesla_config.get("client_id")
    client_secret = tesla_config.get("client_secret")

    if not refresh_token:
        print("❌ No refresh_token found - run 'make tesla-mint' first")
        return False

    print("🔄 Refreshing user access token...")

    token_data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token",
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    new_access_token = result.get("access_token")
                    new_refresh_token = result.get("refresh_token")
                    expires_in = result.get("expires_in", 3600)

                    if new_access_token:
                        # Update config with new tokens
                        tesla_config["access_token"] = new_access_token
                        tesla_config["token_expires_in"] = expires_in
                        if new_refresh_token:  # Not always provided
                            tesla_config["refresh_token"] = new_refresh_token

                        # Save updated config
                        with open(config_path, "w") as f:
                            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

                        print("✅ User token refreshed successfully!")
                        print(f"   New access token: {new_access_token[:20]}...")
                        if new_refresh_token:
                            print(f"   New refresh token: {new_refresh_token[:20]}...")
                        print(f"   Expires in: {expires_in} seconds")
                        return True
                    else:
                        print("❌ No access token in refresh response")
                        return False
                else:
                    error_text = await response.text()
                    print(f"❌ Token refresh failed: {response.status}")
                    print(f"Response: {error_text}")
                    return False

        except Exception as e:
            print(f"❌ Token refresh error: {e}")
            return False


async def verify_partner_registration(tesla_config):
    """Verify and re-register partner account if needed."""
    client_id = tesla_config.get("client_id")
    client_secret = tesla_config.get("client_secret")

    print("\n" + "=" * 50)
    print("🔐 Verifying Fleet API partner registration...")

    # Determine region and endpoints
    region = tesla_config.get("region", "auto")
    if region == "auto":
        # Auto-detect from existing refresh token
        existing_token = tesla_config.get("refresh_token", "")
        if existing_token.startswith("EU_"):
            audience = "https://fleet-api.prd.eu.vn.cloud.tesla.com"
            api_endpoint = "https://fleet-api.prd.eu.vn.cloud.tesla.com"
        elif existing_token.startswith("AP_"):
            audience = "https://fleet-api.prd.ap.vn.cloud.tesla.com"
            api_endpoint = "https://fleet-api.prd.ap.vn.cloud.tesla.com"
        else:
            audience = "https://fleet-api.prd.na.vn.cloud.tesla.com"
            api_endpoint = "https://fleet-api.prd.na.vn.cloud.tesla.com"
    else:
        # Use configured region
        if region == "eu":
            audience = "https://fleet-api.prd.eu.vn.cloud.tesla.com"
            api_endpoint = "https://fleet-api.prd.eu.vn.cloud.tesla.com"
        elif region == "ap":
            audience = "https://fleet-api.prd.ap.vn.cloud.tesla.com"
            api_endpoint = "https://fleet-api.prd.ap.vn.cloud.tesla.com"
        else:
            audience = "https://fleet-api.prd.na.vn.cloud.tesla.com"
            api_endpoint = "https://fleet-api.prd.na.vn.cloud.tesla.com"

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
                "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token",
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    partner_token = result.get("access_token")
                    if not partner_token:
                        print("❌ No partner token received")
                        return False
                    print("✅ Partner token obtained")
                else:
                    error_text = await response.text()
                    print(f"❌ Failed to get partner token: {response.status}")
                    print(f"Response: {error_text}")
                    return False

        except Exception as e:
            print(f"❌ Partner token error: {e}")
            return False

        # Verify/re-register partner account with domain
        domain = "joy13975.github.io"
        print(f"🏢 Verifying registration for domain: {domain}")

        registration_data = {"domain": domain}

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
                    print("✅ Partner registration verified/renewed")
                    return True
                elif response.status == 409:
                    print("✅ Partner registration active (already registered)")
                    return True
                else:
                    print(f"❌ Partner registration check failed: {response.status}")
                    print(f"Response: {response_text}")
                    return False

        except Exception as e:
            print(f"❌ Partner registration error: {e}")
            return False


async def refresh_tesla_tokens():
    """Main function: refresh user tokens and verify partner registration."""
    project_root = Path(__file__).parent.parent
    config_path = project_root / "config.yaml"

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

    if not all([client_id, client_secret]):
        print("❌ Tesla API credentials not configured")
        print("💡 Configure client_id and client_secret in config.yaml")
        return False

    # Check if we have tokens
    has_refresh_token = bool(tesla_config.get("refresh_token"))

    if not has_refresh_token:
        print("❌ No refresh token found")
        print("💡 Run 'make tesla-mint' first for initial setup")
        return False

    print("🔧 Tesla token refresh and registration verification")

    success = True

    # Refresh user tokens
    refresh_success = await refresh_user_token(tesla_config, config, config_path)
    if not refresh_success:
        success = False

    # Verify partner registration
    register_success = await verify_partner_registration(tesla_config)
    if not register_success:
        success = False

    if success:
        print("\n✅ Tesla tokens refreshed and registration verified!")
    else:
        print("\n⚠️  Some operations failed - check errors above")

    return success


if __name__ == "__main__":
    success = asyncio.run(refresh_tesla_tokens())
    sys.exit(0 if success else 1)
