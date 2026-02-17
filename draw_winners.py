#!/usr/bin/env python3
"""
Draw raffle winners with Python: weighted random by ticket count.

Reads:
  - "Casino Night" sheet: Player, Earned Tickets (or Raffle Tickets)
  - "Config" sheet: column B = category (e.g. Spa Winner, Gift Card Winner)

For each category, picks one winner (more tickets = higher chance). No repeat winners.
Writes winner names to Config column C.

Run from project root (same folder as .streamlit/secrets.toml):
  python draw_winners.py

Requires: gspread, google-auth. Loads secrets from .streamlit/secrets.toml.
"""
import random
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        raise SystemExit("Need Python 3.11+ (tomllib) or: pip install tomli")

from google.oauth2.service_account import Credentials
import gspread

SHEETS_SCOPE = ["https://www.googleapis.com/auth/spreadsheets"]
SECRETS_PATH = Path(".streamlit/secrets.toml")
CASINO_SHEET_NAME = "Casino Night"
CONFIG_SHEET_NAME = "Config"
PLAYER_COL = "Player"
TICKETS_COL = "Earned Tickets"


def load_secrets():
    if not SECRETS_PATH.exists():
        raise SystemExit(f"Secrets not found at {SECRETS_PATH}. Run from project root.")
    with open(SECRETS_PATH, "rb") as f:
        data = tomllib.load(f)
    if "gcp_service_account" not in data or "sheets" not in data:
        raise SystemExit("secrets.toml must have [gcp_service_account] and [sheets] with sheet_id, worksheet_name.")
    return data


def get_client(secrets):
    creds = Credentials.from_service_account_info(
        secrets["gcp_service_account"],
        scopes=SHEETS_SCOPE,
    )
    return gspread.authorize(creds)


def get_raffle_pool(client, sheet_id):
    """Return list of (player_name, ticket_count) from Casino Night sheet."""
    ss = client.open_by_key(sheet_id)
    sheet = ss.worksheet(CASINO_SHEET_NAME)
    rows = sheet.get_all_records()
    tickets_col = "Earned Tickets" if (rows and "Earned Tickets" in rows[0]) else "Raffle Tickets"
    pool = []
    for row in rows:
        name = (row.get(PLAYER_COL) or "").strip()
        if not name:
            continue
        try:
            tickets = int(float(row.get(tickets_col, 0) or 0))
        except (TypeError, ValueError):
            tickets = 0
        if tickets < 0:
            tickets = 0
        pool.append((name, tickets))
    return pool


def get_categories(client, sheet_id):
    """Return list of (row_index_1based, category_label) for Config column B."""
    ss = client.open_by_key(sheet_id)
    config = ss.worksheet(CONFIG_SHEET_NAME)
    # B1:B50
    col_b = config.get("B1:B50")
    out = []
    for i, row in enumerate(col_b or [], start=1):
        cell = (row[0] if row else "").strip() if row else ""
        if cell:
            out.append((i, cell))
    return out


def weighted_draw_one(pool):
    """Pick one (name, tickets) from pool with probability proportional to tickets. Returns (name, updated_pool)."""
    if not pool:
        return None, []
    total = sum(t for _, t in pool)
    if total <= 0:
        chosen = random.choice(pool)
    else:
        r = random.uniform(0, total)
        for name, t in pool:
            r -= t
            if r <= 0:
                chosen = (name, t)
                break
        else:
            chosen = pool[-1]
    name = chosen[0]
    new_pool = [(n, t) for n, t in pool if n != name]
    return name, new_pool


def main():
    secrets = load_secrets()
    sheet_id = secrets["sheets"]["sheet_id"]
    client = get_client(secrets)

    pool = get_raffle_pool(client, sheet_id)
    if not pool:
        raise SystemExit("No players with names in the Casino Night sheet.")

    categories = get_categories(client, sheet_id)
    if not categories:
        raise SystemExit("No categories in Config column B. Add labels in B1, B2, ...")

    # Draw one winner per category (no repeats)
    current_pool = list(pool)
    results = []
    for row_num, category in categories:
        if not current_pool:
            results.append((row_num, category, "(no remaining entrants)"))
            continue
        winner, current_pool = weighted_draw_one(current_pool)
        results.append((row_num, category, winner))

    # Write to Config column C
    ss = client.open_by_key(sheet_id)
    config = ss.worksheet(CONFIG_SHEET_NAME)
    for row_num, category, winner in results:
        config.update_acell(f"C{row_num}", winner)
        print(f"C{row_num}: {category} → {winner}")

    print("Done. Config column C updated.")


if __name__ == "__main__":
    main()
