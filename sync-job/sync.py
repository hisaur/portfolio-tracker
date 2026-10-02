#!/usr/bin/env python3
"""
Ghostfolio Local CSV Sync Sidecar
---------------------------------
Reads a local CSV synced from Google Drive (no Google Cloud API or external auth required),
validates and maps the transactions to Ghostfolio's /api/v1/import schema,
and pushes them to a self-hosted Ghostfolio instance.
"""

import csv
import hashlib
import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional
from dateutil import parser
import requests

# Ensure console handles UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ghostfolio-sync")

# Environment configuration
GHOSTFOLIO_URL = os.getenv("GHOSTFOLIO_URL", "http://ghostfolio:3333").rstrip("/")
GHOSTFOLIO_TOKEN = os.getenv("GHOSTFOLIO_BEARER_TOKEN", "")
GHOSTFOLIO_ACCOUNT_ID = os.getenv("GHOSTFOLIO_ACCOUNT_ID", "").strip()
GHOSTFOLIO_DEFAULT_DATA_SOURCE = os.getenv("GHOSTFOLIO_DATA_SOURCE", "YAHOO")
CSV_FILE_PATH = os.getenv("CSV_FILE_PATH", "/data/portfolio_docker_sync.csv")
INITIAL_WAIT_SECONDS = int(os.getenv("INITIAL_WAIT_SECONDS", "5"))
HEALTH_CHECK_TIMEOUT = int(os.getenv("HEALTH_CHECK_TIMEOUT", "120"))
SYNC_MODE = os.getenv("SYNC_MODE", "once").lower()  # 'once' or 'watch'
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))


def parse_date_to_ghostfolio_iso(date_str: str) -> str:
    """
    Normalizes various date string formats into Ghostfolio's expected ISO 8601 UTC string.
    Handles:
      - Apps Script default string: 'Thu Oct 01 2026 00:00:00 GMT+1000 (Australian Eastern Standard Time)'
      - ISO dates: '2026-10-01', '2026-10-01T00:00:00.000Z'
      - Standard dates: '01/10/2026', '2026/10/01'
    """
    s = str(date_str).strip()
    if not s:
        raise ValueError("Empty date string")

    # Strip parenthesized timezone descriptions like '(Australian Eastern Standard Time)'
    s_clean = re.sub(r"\s*\([^)]*\)", "", s)
    # Replace 'GMT+' or 'GMT-' with '+/-' offset notation for dateutil
    s_clean = re.sub(r"GMT([+-])", r"\1", s_clean).strip()

    dt = parser.parse(s_clean)
    # Ghostfolio expects UTC midnight or ISO 8601 timestamp (YYYY-MM-DDTHH:MM:SS.000Z)
    return dt.strftime("%Y-%m-%dT00:00:00.000Z")


def wait_for_ghostfolio_readiness(url: str, timeout: int = 120) -> bool:
    """
    Waits for Ghostfolio to finish booting and return HTTP 200 on /api/v1/health.
    """
    health_url = f"{url}/api/v1/health"
    start_time = time.time()
    logger.info(f"Checking Ghostfolio health at {health_url} (timeout: {timeout}s)...")

    while time.time() - start_time < timeout:
        try:
            resp = requests.get(health_url, timeout=5)
            if resp.status_code == 200:
                logger.info("Ghostfolio is ready and healthy!")
                return True
            else:
                logger.debug(f"Healthcheck returned status {resp.status_code}, retrying...")
        except requests.exceptions.RequestException as e:
            logger.debug(f"Ghostfolio not reachable yet: {e}")

        time.sleep(3)

    logger.error(f"Ghostfolio did not become healthy within {timeout} seconds.")
    return False


def get_or_create_auth_token(url: str, configured_token: str) -> tuple[str, Optional[str]]:
    """
    Resolves the Bearer authentication token and returns (authToken, securityToken).
    Supports:
      1. Pre-configured JWT token (starts with 'eyJ')
      2. Pre-configured Security Token (exchanges via /api/v1/auth/anonymous)
      3. Ephemeral auto-provisioning: If no token is provided, registers a new user
         via POST /api/v1/user, extracts the generated security token, and exchanges it.
    """
    token = (configured_token or "").strip()

    # Case 1: Already a JWT Bearer token
    if token.startswith("eyJ"):
        logger.info("Using configured Bearer JWT token.")
        return token, None

    # Case 2: Configured Security Token -> exchange for JWT
    if token:
        logger.info("Exchanging configured Security Token for Bearer token...")
        try:
            resp = requests.post(
                f"{url}/api/v1/auth/anonymous",
                json={"accessToken": token},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                auth_token = data.get("authToken")
                if auth_token:
                    logger.info("Successfully obtained Bearer token from Security Token!")
                    return auth_token, token
        except Exception as e:
            logger.warning(f"Failed to exchange configured token: {e}")
        return token, None

    # Case 3: Ephemeral mode with no token provided -> Auto-create user via API!
    logger.info("No token configured. Ephemeral stack detected: Auto-provisioning new Ghostfolio user...")
    try:
        reg_resp = requests.post(
            f"{url}/api/v1/user",
            json={"languageCode": "en"},
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        if reg_resp.status_code in (200, 201):
            reg_data = reg_resp.json()
            sec_token = reg_data.get("accessToken") or reg_data.get("securityToken")
            if sec_token:
                # Exchange for Bearer JWT
                auth_resp = requests.post(
                    f"{url}/api/v1/auth/anonymous",
                    json={"accessToken": sec_token},
                    headers={"Content-Type": "application/json"},
                    timeout=10,
                )
                if auth_resp.status_code in (200, 201):
                    jwt_token = auth_resp.json().get("authToken")
                    if jwt_token:
                        logger.info("=================================================================")
                        logger.info(f"GHOSTFOLIO SECURITY TOKEN: {sec_token}")
                        logger.info(f"GHOSTFOLIO BEARER API KEY: {jwt_token}")
                        logger.info("=================================================================")
                        print("\n" + "=" * 70, flush=True)
                        print("[KEYS] EPHEMERAL GHOSTFOLIO CREDENTIALS GENERATED", flush=True)
                        print("=" * 70, flush=True)
                        print(f"GHOSTFOLIO SECURITY TOKEN (for Web UI):\n  {sec_token}\n", flush=True)
                        print(f"GHOSTFOLIO BEARER API KEY (for REST API):\n  {jwt_token}\n", flush=True)
                        print(f"Dashboard URL: {url}", flush=True)
                        print("=" * 70 + "\n", flush=True)
                        return jwt_token, sec_token
            if reg_data.get("authToken"):
                return reg_data["authToken"], sec_token
        else:
            logger.warning(f"POST /api/v1/user returned status {reg_resp.status_code}: {reg_resp.text}")
    except Exception as e:
        logger.warning(f"Error during auto-provisioning via /api/v1/user: {e}")

    logger.error("Could not obtain an authentication token for Ghostfolio.")
    return "", None


def get_default_account_id(url: str, token: str) -> Optional[str]:
    """
    Queries Ghostfolio GET /api/v1/account to retrieve the first available account ID
    if GHOSTFOLIO_ACCOUNT_ID is not explicitly configured.
    """
    if GHOSTFOLIO_ACCOUNT_ID:
        return GHOSTFOLIO_ACCOUNT_ID

    if not token:
        return None

    try:
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(f"{url}/api/v1/account", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            accounts = data.get("accounts", []) if isinstance(data, dict) else data
            if isinstance(accounts, list) and len(accounts) > 0:
                first_id = accounts[0].get("id")
                name = accounts[0].get("name", "Default")
                logger.info(f"Auto-detected Ghostfolio account: '{name}' (ID: {first_id})")
                return first_id
    except Exception as e:
        logger.warning(f"Could not auto-fetch Ghostfolio account ID: {e}")

    return None


def parse_csv_file(csv_path: str, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Reads the CSV file from local disk and transforms records into Ghostfolio activities schema.
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found at: {csv_path}")

    activities: List[Dict[str, Any]] = []

    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            logger.warning(f"CSV file {csv_path} is empty or has no header.")
            return activities

        # Normalize header keys to lowercase for flexible column mapping
        for row_idx, raw_row in enumerate(reader, start=2):
            row = {str(k).strip().lower(): str(v).strip() for k, v in raw_row.items() if k is not None}

            # Extract columns based on requirements
            raw_date = row.get("date")
            raw_type = row.get("type")
            raw_symbol = row.get("symbol")
            raw_quantity = row.get("quantity")
            raw_unit_price = row.get("unitprice") or row.get("unit_price") or row.get("price")
            raw_currency = row.get("currency", "USD")
            raw_fee = row.get("fee", "0")

            # Skip empty rows
            if not raw_symbol and not raw_quantity:
                continue

            # Validate mandatory fields
            if not all([raw_date, raw_type, raw_symbol, raw_quantity, raw_unit_price]):
                logger.warning(
                    f"Row {row_idx} is missing mandatory fields. Skipping row: {raw_row}"
                )
                continue

            try:
                parsed_date = parse_date_to_ghostfolio_iso(raw_date)
                activity_type = raw_type.upper()
                symbol = raw_symbol.upper()
                quantity = float(raw_quantity)
                unit_price = float(raw_unit_price)
                fee = float(raw_fee) if raw_fee else 0.0
                currency = raw_currency.upper()

                activity: Dict[str, Any] = {
                    "currency": currency,
                    "dataSource": GHOSTFOLIO_DEFAULT_DATA_SOURCE,
                    "date": parsed_date,
                    "fee": fee,
                    "quantity": quantity,
                    "symbol": symbol,
                    "type": activity_type,
                    "unitPrice": unit_price,
                }

                if account_id:
                    activity["accountId"] = account_id

                activities.append(activity)

            except Exception as ex:
                logger.error(f"Error parsing row {row_idx} ({raw_row}): {ex}")
                continue

    return activities


def push_to_ghostfolio(url: str, token: str, activities: List[Dict[str, Any]]) -> bool:
    """
    POSTs activities array to Ghostfolio /api/v1/import endpoint.
    """
    if not activities:
        logger.info("No activities found in CSV to import.")
        return True

    if not token:
        logger.error(
            "GHOSTFOLIO_BEARER_TOKEN is not configured! "
            "Please generate an API token in Ghostfolio (Settings -> Access) and set it in .env."
        )
        return False

    import_url = f"{url}/api/v1/import"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"activities": activities}

    logger.info(f"Submitting {len(activities)} activities to Ghostfolio ({import_url})...")

    try:
        resp = requests.post(import_url, json=payload, headers=headers, timeout=30)

        if resp.status_code in (200, 201):
            logger.info(f"Successfully imported {len(activities)} activities into Ghostfolio!")
            return True
        elif resp.status_code == 400:
            response_json = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            logger.warning(
                f"Ghostfolio responded with HTTP 400 Bad Request: {response_json}. "
                "This typically occurs if duplicate transactions are detected (IS_DUPLICATE) "
                "or if symbol/currency validation failed."
            )
            return False
        elif resp.status_code == 401:
            logger.error("HTTP 401 Unauthorized: Invalid or expired GHOSTFOLIO_BEARER_TOKEN.")
            return False
        else:
            logger.error(f"Failed to import activities. HTTP {resp.status_code}: {resp.text}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"Network error while connecting to Ghostfolio API: {e}")
        return False


def get_file_md5(filepath: str) -> Optional[str]:
    """Calculates MD5 hash of the file to detect content modifications."""
    if not os.path.exists(filepath):
        return None
    hasher = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return None


def run_sync() -> bool:
    """Executes a single CSV parsing and sync cycle."""
    logger.info(f"Reading CSV from local path: {CSV_FILE_PATH}")
    bearer_token, sec_token = get_or_create_auth_token(GHOSTFOLIO_URL, GHOSTFOLIO_TOKEN)
    if not bearer_token:
        logger.error("Authentication failed. Cannot proceed with import.")
        return False

    account_id = get_default_account_id(GHOSTFOLIO_URL, bearer_token)

    try:
        activities = parse_csv_file(CSV_FILE_PATH, account_id=account_id)
        logger.info(f"Parsed {len(activities)} valid transaction(s) from CSV.")
        success = push_to_ghostfolio(GHOSTFOLIO_URL, bearer_token, activities)
        if success:
            logger.info("=================================================================")
            logger.info("DATA INGESTION COMPLETE & PORTFOLIO READY!")
            logger.info(f"Ghostfolio Web Dashboard: {GHOSTFOLIO_URL}")
            if sec_token:
                logger.info(f"GHOSTFOLIO SECURITY TOKEN: {sec_token}")
            if bearer_token:
                logger.info(f"GHOSTFOLIO BEARER API KEY: {bearer_token}")
            logger.info("=================================================================")
            print("\n" + "=" * 70, flush=True)
            print("[OK] DATA INGESTION COMPLETE! GHOSTFOLIO IS READY", flush=True)
            print("=" * 70, flush=True)
            print(f"Dashboard URL: {GHOSTFOLIO_URL}", flush=True)
            if sec_token:
                print(f"GHOSTFOLIO SECURITY TOKEN (for Web UI):\n  {sec_token}\n", flush=True)
            if bearer_token:
                print(f"GHOSTFOLIO BEARER API KEY (for REST API):\n  {bearer_token}\n", flush=True)
            print("=" * 70 + "\n", flush=True)
        return success
    except FileNotFoundError as fnf:
        logger.error(f"Mount error: {fnf}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error during sync: {e}", exc_info=True)
        return False


def main():
    logger.info("Starting Ghostfolio Sync Sidecar...")
    logger.info(f"Target Ghostfolio instance: {GHOSTFOLIO_URL}")
    logger.info(f"Sync Mode: {SYNC_MODE}")

    is_dry_run = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes") or "--dry-run" in sys.argv

    if is_dry_run:
        logger.info("Running in DRY RUN mode (Ghostfolio network calls skipped).")
        activities = parse_csv_file(CSV_FILE_PATH, account_id=GHOSTFOLIO_ACCOUNT_ID or "dry-run-account")
        logger.info(f"Successfully parsed {len(activities)} activities from {CSV_FILE_PATH}:")
        print(json.dumps({"activities": activities}, indent=2))
        print("\n" + "=" * 70, flush=True)
        print("[DEMO] SIMULATED EPHEMERAL GHOSTFOLIO CREDENTIALS (DRY RUN)", flush=True)
        print("=" * 70, flush=True)
        print("GHOSTFOLIO SECURITY TOKEN (for Web UI):\n  demo-sec-token-12345-abcde\n", flush=True)
        print("GHOSTFOLIO BEARER API KEY (for REST API):\n  eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.demo-jwt-payload...\n", flush=True)
        print("Dashboard URL: http://localhost:3333", flush=True)
        print("=" * 70 + "\n", flush=True)
        sys.exit(0)

    # Startup delay to give containers time to initialize networks
    if INITIAL_WAIT_SECONDS > 0:
        logger.info(f"Waiting {INITIAL_WAIT_SECONDS} seconds before checking Ghostfolio...")
        time.sleep(INITIAL_WAIT_SECONDS)

    # Readiness check
    is_ready = wait_for_ghostfolio_readiness(GHOSTFOLIO_URL, timeout=HEALTH_CHECK_TIMEOUT)
    if not is_ready:
        logger.error("Ghostfolio is not responding. Exiting.")
        sys.exit(1)

    if SYNC_MODE == "watch":
        logger.info(f"Entering watch mode. Checking {CSV_FILE_PATH} every {POLL_INTERVAL_SECONDS}s...")
        last_hash = None
        while True:
            current_hash = get_file_md5(CSV_FILE_PATH)
            if current_hash and current_hash != last_hash:
                logger.info(f"Change detected in {CSV_FILE_PATH}. Triggering sync...")
                success = run_sync()
                if success:
                    last_hash = current_hash
            elif not current_hash:
                logger.warning(f"File {CSV_FILE_PATH} is not yet available at the mount point.")
            time.sleep(POLL_INTERVAL_SECONDS)
    else:
        # Default single run mode
        success = run_sync()
        if success:
            logger.info("Sync job completed successfully.")
            sys.exit(0)
        else:
            logger.warning("Sync job completed with warnings or errors.")
            sys.exit(1)


if __name__ == "__main__":
    main()
