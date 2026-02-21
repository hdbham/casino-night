import base64
import io
import random
import time
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import List, Optional, Tuple

import streamlit.components.v1 as components

import pandas as pd
import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    # Fallback: no-op if the package isn't installed. App will still work, just without auto-refresh.
    def st_autorefresh(*args, **kwargs):
        return None

try:
    from google.oauth2.service_account import Credentials
    import gspread
    # Retry with backoff on rate limit (429) and server errors; gspread 5.5+
    _BackOffClient = getattr(gspread, "BackOffHTTPClient", None)
except ImportError:
    Credentials = None
    gspread = None
    _BackOffClient = None


# ---------- CONFIG ----------
DEMO_MODE = False  # Set to True for demo data without Google
APP_BG_HEX = "#02111b"  # match body background in CSS
REFRESH_MS = 5000  # 5 seconds for demo
# Leaderboard refetch interval. Each open session = 60/SHEETS_REFRESH_SECONDS reads/min (e.g. 30s → 2/min).
SHEETS_REFRESH_SECONDS = 30
DEMO_UPDATE_INTERVAL_SECONDS = 10  # How often demo data mutates

# Sheet columns: Player, Chips Bought, Chips Spent, Chips Total, Earned Tickets (plus bucket columns). We display/sort by Earned Tickets.
TICKETS_COL = "RaffleTickets"  # internal; mapped from sheet "Earned Tickets" (or "Raffle Tickets")
TICKETS_SHEET_COL = "Earned Tickets"  # header in sheet; fallback to "Raffle Tickets"
CHIPS_SHEET_COL = "Chips Total"  # header for spendable balance; fallback to "Chips"
CHIPS_SPENT_COL = "Chips Spent"  # optional; incremented when operator spends chips
OPERATOR_FLUSH_INTERVAL_SECONDS = 60  # Queue operator actions and send one batch per minute

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
SCOPES_WRITE = ["https://www.googleapis.com/auth/spreadsheets"]


# ---------- SETUP ----------
st.set_page_config(
    page_title="Leaderboard",
    layout="wide",
)

# Hide Streamlit chrome for TV mode, force dark theme, TV-friendly layout
st.markdown(
    """
    <style>
    /* Fire TV / TV remotes: scroll must be on document, not inner divs */
    html { overflow-y: auto !important; overflow-x: hidden !important; height: auto !important; min-height: 100%; }
    body { overflow-y: auto !important; overflow-x: hidden !important; height: auto !important; min-height: 100%; -webkit-overflow-scrolling: touch; }
    .stApp, [data-testid="stAppViewContainer"] { overflow: visible !important; min-height: auto !important; height: auto !important; }
    .main { overflow: visible !important; }
    /* Force night mode and TV display */
    .stApp, [data-testid="stAppViewContainer"], .main .block-container { background-color: #02111b !important; }
    body { background-color: #02111b !important; }
    .block-container { padding-top: 0.75rem; padding-bottom: 1.5rem; max-width: 1600px; margin-left: auto; margin-right: auto; overflow: visible !important; }
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    header { visibility: hidden; }
    /* Bottom action bar for TV: clear separation, readable from distance */
    .tv-bottom-actions { margin-top: 2rem; padding-top: 1.5rem; border-top: 1px solid rgba(249, 115, 22, 0.35); }
    .tv-bottom-actions .stButton > button { font-size: 1rem !important; font-weight: 600 !important; padding: 0.6rem 1rem !important; }
    </style>
    """,
    unsafe_allow_html=True,
)
# Legacy style block (leaderboard and rows)
st.markdown(
    """
    <style>
    .leader-row {
        font-size: 2.2rem;
        padding: 0.2rem 1rem;
        margin: 0.15rem 0;
        border-radius: 8px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .leader-row-wrap { display: flex; align-items: stretch; margin-bottom: 0.35rem; }
    .medal-cell { width: 3.5rem; display: flex; align-items: center; justify-content: center; flex-shrink: 0; font-size: 50px; }
    .leader-row-wrap .leader-row { flex: 1; min-width: 0; }
    .rank { width: 4rem; font-weight: 700; padding-left: 0.25rem; }
    .name { flex: 2; display: flex; flex-direction: column; }
    .name-main { line-height: 1.1; }
    .name-meta {
        font-size: 1.0rem;
        color: #9ca3af;
        margin-top: 0.05rem;
    }
    .tickets { flex: 1; text-align: right; font-variant-numeric: tabular-nums; }
    .status-badge { font-size: 0.95rem; color: #fbbf24; font-weight: 600; }
    .up { color: #00ff9c; }
    .down { color: #ff4b4b; }
    .same { color: #cccccc; }
    .gold { background: linear-gradient(90deg,#ffc857,#a15e00); color: #1a0f00; }
    .silver { background: linear-gradient(90deg,#e0e6ed,#7b7f86); color: #111827; }
    .bronze { background: linear-gradient(90deg,#e0b089,#8b4513); color: #1f130b; }
    .neutral { background: #041826; color: #e5e7eb; }
    .leader-row.negative-chips { text-decoration: line-through; opacity: 0.85; }
    /* Natural flow: no fixed height so nothing gets pushed out */
    .leaderboard-scaled { overflow: visible; }
    .leaderboard-scaled .leader-row-wrap { display: flex; align-items: center; margin-bottom: 0.35rem; }
    .leaderboard-scaled .leader-row { font-size: clamp(0.65rem, 2.2vh, 1.5rem); padding: 0.35rem 0.5rem; margin: 0; border-radius: 4px; }
    .leaderboard-scaled .medal-cell { width: clamp(1.25rem, 3.5vw, 1.75rem); font-size: clamp(14px, 2.8vh, 25px); }
    .leaderboard-scaled .rank { width: clamp(1.5rem, 4vw, 2rem); padding-left: 0.1rem; }
    .leaderboard-scaled .name-meta { font-size: 0.5em; margin-top: 0.05em; }
    .leaderboard-scaled .status-badge { font-size: 0.85em; }
    .leaderboard-scaled .leader-row .up,
    .leaderboard-scaled .leader-row .down,
    .leaderboard-scaled .leader-row .same { width: 1.25rem !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Refetch sheet on a fixed interval. Movement icons: ▲ ▼ ●
def _leaderboard_content():
    """Load data and render leaderboard every refresh. Movement icons: ▲ up, ▼ down, ● same."""
    try:
        df = load_data()
    except RuntimeError as e:
        st.error(str(e))
        st.markdown("See **SETUP_GOOGLE.md** in this project for step-by-step setup (Sheet, GCP service account, secrets).")
        st.caption("If the problem persists, try refreshing or syncing after 60 seconds.")
        return

    if st.session_state.get("google_deps_missing"):
        st.info("Running with **demo data** — `gspread` and `google-auth` are not installed. To use Google Sheets: `pip install gspread google-auth` then restart the app.")
    if df.empty:
        st.warning("Waiting for earned ticket data in Google Sheets…")
        return
    # If any player has negative chips (strikethrough), show notice at top
    has_negative = False
    for _, row in df.iterrows():
        chips_val = row.get(CHIPS_SHEET_COL, row.get("Chips", 0))
        try:
            chips_num = int(float(str(chips_val).replace(",", "").strip() or 0))
        except (TypeError, ValueError):
            chips_num = 0
        if chips_num < 0:
            has_negative = True
            break
    if has_negative:
        st.info("**If your name is strikethrough:** meet with cashier to settle or buy more chips.")
        st.success("**Protip:** You can go into negative, but you must settle to qualify for a reward or to join another game.")
    # Show top 10 only so they fit on every display
    df = df.head(10)
    if "last_positions" not in st.session_state:
        st.session_state.last_positions = get_position_map(df)
    current_positions = get_position_map(df)
    st.markdown('<div class="leaderboard-scaled">', unsafe_allow_html=True)
    for _, row in df.iterrows():
        name, rank = row["Player"], int(row["Rank"])
        last_rank = st.session_state.last_positions.get(name)
        movement = "up" if last_rank is None or rank < last_rank else ("down" if rank > last_rank else "same")
        render_row(row, movement)
    st.markdown('</div>', unsafe_allow_html=True)
    st.session_state.last_positions = current_positions

_interval_ms = REFRESH_MS if DEMO_MODE else (SHEETS_REFRESH_SECONDS * 1000)
_leaderboard_fragment = _leaderboard_content
st_autorefresh(interval=_interval_ms, key="leaderboard_refresh")


@st.cache_resource
def get_sheet_client():
    """Create and cache a Google Sheets client. Uses BackOffHTTPClient when available to retry on read/rate limit errors."""
    if Credentials is None or gspread is None:
        raise RuntimeError(
            "Google Sheets dependencies are not installed. "
            "Install 'gspread' and 'google-auth' or enable DEMO_MODE."
        )
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=SCOPES,
    )
    if _BackOffClient is not None:
        client = gspread.authorize(creds, http_client=_BackOffClient)
    else:
        client = gspread.authorize(creds)
    return client



def _check_google_secrets() -> Optional[str]:
    """Return an error message if Google secrets are missing, else None."""
    try:
        secrets = st.secrets
    except Exception:
        return "Secrets not loaded. Create `.streamlit/secrets.toml` with your Google credentials."
    if "gcp_service_account" not in secrets:
        return "Missing `[gcp_service_account]` in `.streamlit/secrets.toml`."
    gcp = secrets["gcp_service_account"]
    for key in ("project_id", "private_key", "client_email"):
        if not gcp.get(key):
            return f"Missing or empty `gcp_service_account.{key}` in secrets."
    if "sheets" not in secrets:
        return "Missing `[sheets]` in `.streamlit/secrets.toml`."
    sheet_cfg = secrets["sheets"]
    if not sheet_cfg.get("sheet_id") or not sheet_cfg.get("worksheet_name"):
        return "In `[sheets]` set both `sheet_id` and `worksheet_name`."
    return None


def _parse_countdown_val(val: Optional[str]) -> Optional[datetime]:
    """Parse value as countdown end datetime. Return None if missing or invalid."""
    if not val or not str(val).strip():
        return None
    val = str(val).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
        "%I:%M %p",
    ):
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def get_config_from_sheet() -> Optional[datetime]:
    """Read Config sheet A2 (countdown end). Return countdown_end or None."""
    if DEMO_MODE or Credentials is None or gspread is None:
        return None
    try:
        cfg = st.secrets.get("sheets", {})
        sheet_id = cfg.get("sheet_id")
        countdown_ws = cfg.get("countdown_worksheet", "Config")
        if not sheet_id:
            return None
        client = get_sheet_client()
        spreadsheet = client.open_by_key(sheet_id)
        config_sheet = spreadsheet.worksheet(countdown_ws)
        val = config_sheet.acell("A2").value
        countdown_end = _parse_countdown_val(str(val).strip() if val else None)
        return countdown_end
    except Exception:
        return None


def get_countdown_end() -> Optional[datetime]:
    """Read countdown end time from sheet 'Config' cell A2. Return None if missing or invalid."""
    return get_config_from_sheet()


def get_operator_pin_from_sheet() -> Optional[str]:
    """Read operator PIN from Config sheet cell B2. Return None if empty or read fails."""
    if DEMO_MODE or Credentials is None or gspread is None:
        return None
    try:
        cfg = st.secrets.get("sheets", {})
        sheet_id = cfg.get("sheet_id")
        countdown_ws = cfg.get("countdown_worksheet", "Config")
        if not sheet_id:
            return None
        client = get_sheet_client()
        spreadsheet = client.open_by_key(sheet_id)
        config_sheet = spreadsheet.worksheet(countdown_ws)
        val = config_sheet.acell("B2").value
        s = (str(val).strip() if val else "").strip()
        return s if s else None
    except Exception:
        return None


def _weighted_draw_one(pool: List[Tuple[str, int]]) -> Tuple[Optional[str], int, List[Tuple[str, int]]]:
    """Pick one name from pool with probability proportional to tickets. Returns (name, that person's tickets, updated_pool)."""
    if not pool:
        return None, 0, []
    names = [name for name, tickets in pool]
    weights = [max(0, tickets) for name, tickets in pool]
    if sum(weights) == 0:
        chosen_name = random.choice(names)
    else:
        chosen_name = random.choices(names, weights=weights, k=1)[0]
    chosen_tickets = next((t for n, t in pool if n == chosen_name), 0)
    new_pool = [(n, t) for n, t in pool if n != chosen_name]
    return chosen_name, chosen_tickets, new_pool



OPTIONS_SHEET_NAME = "Options"
OPTIONS_HEADER = "Winning Options"
WINNING_RESULTS_SHEET_NAME = "WinningResults"
LOG_SHEET_NAME = "Log"


def get_winning_options() -> List[str]:
    """Read winning categories from Options sheet: column A, row 2 onward (row 1 = header). Returns list of option names."""
    if DEMO_MODE or Credentials is None or gspread is None:
        return []
    try:
        cfg = st.secrets.get("sheets", {})
        sheet_id = cfg.get("sheet_id")
        if not sheet_id:
            return []
        client = get_sheet_client()
        ss = client.open_by_key(sheet_id)
        try:
            opt_sheet = ss.worksheet(OPTIONS_SHEET_NAME)
        except Exception:
            return []
        col_a = opt_sheet.col_values(1)
        # Row 1 = header ("Winning Options"), options start row 2
        out = []
        for i, cell in enumerate(col_a):
            if i == 0:
                continue
            s = (cell or "").strip()
            if s:
                out.append(s)
        return out
    except Exception:
        return []


def _ensure_bucket_columns(option_names: List[str]) -> None:
    """Add any missing bucket columns to the Casino Night sheet (row 1 headers). Uses write scope."""
    if not option_names or DEMO_MODE or Credentials is None or gspread is None:
        return
    try:
        cfg = st.secrets.get("sheets", {})
        sheet_id = cfg.get("sheet_id")
        leaderboard_ws = cfg.get("worksheet_name", "Casino Night")
        if not sheet_id:
            return
        creds = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]),
            scopes=SCOPES_WRITE,
        )
        client = gspread.authorize(creds, http_client=_BackOffClient) if _BackOffClient else gspread.authorize(creds)
        ss = client.open_by_key(sheet_id)
        lb = ss.worksheet(leaderboard_ws)
        headers = lb.row_values(1)
        if not headers:
            return
        for label in option_names:
            if label not in headers:
                col_index = len(headers) + 1
                lb.update_cell(1, col_index, label)
                headers.append(label)
    except Exception:
        pass


def run_draw_winners() -> Tuple[bool, str]:
    """Run weighted raffle draw and write winners to Config column C. Returns (success, message)."""
    if DEMO_MODE or Credentials is None or gspread is None:
        return False, "Google Sheets not available (DEMO_MODE or missing gspread/google-auth)."
    err = _check_google_secrets()
    if err:
        return False, err
    try:
        cfg = st.secrets.get("sheets", {})
        sheet_id = cfg.get("sheet_id")
        leaderboard_ws = cfg.get("worksheet_name", "Casino Night")
        config_ws = cfg.get("countdown_worksheet", "Config")
        if not sheet_id:
            return False, "Missing sheet_id in secrets."
        creds = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]),
            scopes=SCOPES_WRITE,
        )
        client = gspread.authorize(creds, http_client=_BackOffClient) if _BackOffClient else gspread.authorize(creds)
        ss = client.open_by_key(sheet_id)
        lb = ss.worksheet(leaderboard_ws)
        config = ss.worksheet(config_ws)
        # Categories from Options sheet only (Config B is never written; B2 is reserved for operator PIN)
        option_names = get_winning_options()
        if option_names:
            categories = [(i, label) for i, label in enumerate(option_names, start=1)]
        else:
            col_b = config.get("B1:B50")
            categories = []
            for i, row in enumerate(col_b or [], start=1):
                cell = (row[0] if row else "").strip() if row else ""
                if cell:
                    categories.append((i, cell))
        if not categories:
            return False, "No categories. Add an Options sheet with 'Winning Options' in A1 and option names in A2 down, or put category names in Config column B."
        # Ensure bucket columns exist on the leaderboard sheet
        headers = lb.row_values(1)
        if not headers:
            return False, "Leaderboard sheet has no header row (row 1)."
        for _, category_label in categories:
            if category_label not in headers:
                col_index = len(headers) + 1
                lb.update_cell(1, col_index, category_label)
                headers.append(category_label)
        rows = lb.get_all_records()
        if not rows:
            return False, "No data rows in the leaderboard sheet."
        all_columns = list(rows[0].keys()) if rows else []

        def build_pool_for_category(category_label: str) -> List[Tuple[str, int]]:
            """Pool for this prize: only players who put tickets in this category's bucket. No fallback to total Raffle Tickets."""
            if category_label not in all_columns:
                return []
            pool = []
            for row in rows:
                name = (row.get("Player") or "").strip()
                if not name:
                    continue
                try:
                    t = int(float(row.get(category_label, 0) or 0))
                except (TypeError, ValueError):
                    t = 0
                if t > 0:
                    pool.append((name, t))
            return pool

        # Get or create WinningResults sheet
        try:
            results_sheet = ss.worksheet(WINNING_RESULTS_SHEET_NAME)
        except Exception:
            ss.add_worksheet(title=WINNING_RESULTS_SHEET_NAME, rows=100, cols=5)
            results_sheet = ss.worksheet(WINNING_RESULTS_SHEET_NAME)
        results_sheet.update([["Category", "Winner", "Probability"]], range_name="A1:C1")

        # Draw one winner per category; write to WinningResults
        data_rows = []
        for row_num, category_label in categories:
            pool = build_pool_for_category(category_label)
            if not pool:
                data_rows.append([category_label, "(no tickets in this bucket)", ""])
                continue
            total_tickets = sum(t for _, t in pool)
            winner, winner_tickets, _ = _weighted_draw_one(pool)
            pct = (winner_tickets / total_tickets * 100) if total_tickets > 0 else 0
            pct_str = f"{pct:.1f}% chance" if winner else ""
            data_rows.append([category_label, winner or "", pct_str])
        if data_rows:
            results_sheet.update(data_rows, range_name=f"A2:C{1 + len(data_rows)}")
        return True, "Winners drawn and written to WinningResults sheet."
    except Exception as e:
        return False, str(e)


def get_item_winners() -> List[dict]:
    """Read item winners from WinningResults sheet (Category, Winner, Probability). Returns list of {item, winner, probability}."""
    if DEMO_MODE or Credentials is None or gspread is None:
        return []
    try:
        cfg = st.secrets.get("sheets", {})
        sheet_id = cfg.get("sheet_id")
        if not sheet_id:
            return []
        client = get_sheet_client()
        spreadsheet = client.open_by_key(sheet_id)
        try:
            results_sheet = spreadsheet.worksheet(WINNING_RESULTS_SHEET_NAME)
        except Exception:
            return []
        rows = results_sheet.get_all_records()
        out = []
        for row in rows or []:
            item = (row.get("Category") or row.get("category") or "").strip()
            winner = (row.get("Winner") or row.get("winner") or "").strip()
            probability = (row.get("Probability") or row.get("probability") or "").strip()
            if winner or item:
                out.append({"item": item or "Winner", "winner": winner or "—", "probability": probability})
        return out
    except Exception:
        return []


def _get_leaderboard_worksheet_write():
    """Return (worksheet, None) or (None, error_message). Uses write scope."""
    if DEMO_MODE or Credentials is None or gspread is None:
        return None, "Google Sheets not available (DEMO_MODE or missing gspread/google-auth)."
    err = _check_google_secrets()
    if err:
        return None, err
    try:
        cfg = st.secrets.get("sheets", {})
        sheet_id = cfg.get("sheet_id")
        leaderboard_ws = cfg.get("worksheet_name", "Casino Night")
        if not sheet_id:
            return None, "Missing sheet_id in secrets."
        creds = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]),
            scopes=SCOPES_WRITE,
        )
        client = gspread.authorize(creds, http_client=_BackOffClient) if _BackOffClient else gspread.authorize(creds)
        ss = client.open_by_key(sheet_id)
        lb = ss.worksheet(leaderboard_ws)
        return lb, None
    except PermissionError:
        service_email = st.secrets["gcp_service_account"].get("client_email", "your service account email")
        return None, f"Sheet must be shared with {service_email} as Editor to update data."
    except Exception as e:
        return None, str(e)


def _find_player_row(lb, player_name: str) -> Optional[int]:
    """Find 1-based row number for player name in column A. Returns None if not found."""
    try:
        cells = lb.findall(str(player_name or "").strip(), in_column=1)
        if not cells:
            return None
        return cells[0].row
    except Exception:
        return None


def _col_name_to_index(lb, col_name: str) -> Optional[int]:
    """Return 1-based column index for header col_name, or None."""
    try:
        headers = lb.row_values(1)
        if not headers:
            return None
        for i, h in enumerate(headers):
            if (h or "").strip() == (col_name or "").strip():
                return i + 1
        return None
    except Exception:
        return None


def _rowcol_to_a1(row: int, col: int) -> str:
    """Convert 1-based row, col to A1 notation (e.g. row=1, col=3 -> 'C1')."""
    col_letter = ""
    c = col
    while c > 0:
        c, r = divmod(c - 1, 26)
        col_letter = chr(65 + r) + col_letter
    return (col_letter or "A") + str(row)


def _parse_cell_int(val) -> int:
    try:
        return int(float(str(val).replace(",", "").strip() or 0))
    except (ValueError, TypeError):
        return 0


def _append_operator_logs(ss, items: list, operator_name: str) -> None:
    """Append one row per item to the Log sheet. Columns: Timestamp, Operator, Action, Player, Amount, Details."""
    if not items or DEMO_MODE or not ss:
        return
    operator_name = (operator_name or "").strip() or "—"
    try:
        try:
            log_sheet = ss.worksheet(LOG_SHEET_NAME)
        except Exception:
            ss.add_worksheet(title=LOG_SHEET_NAME, rows=500, cols=6)
            log_sheet = ss.worksheet(LOG_SHEET_NAME)
        existing = log_sheet.get_all_values()
        if not existing:
            log_sheet.update([["Timestamp", "Operator", "Action", "Player", "Amount", "Details"]], range_name="A1:F1")
        for item in items:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            action = (item.get("action") or "").strip() or "—"
            player = (item.get("player") or "").strip() or "—"
            amount = item.get("amount") or 0
            details = ""
            if action == "spend_chips":
                details = "chips deducted"
            elif action == "earn_tickets":
                cat = (item.get("category") or "").strip()
                details = f"bucket: {cat}" if cat else "tickets added"
            row = [ts, operator_name, action, player, amount, details]
            try:
                log_sheet.append_row(row, value_input_option="USER_ENTERED", table_range="A1")
            except Exception:
                # Fallback without table_range for older gspread
                log_sheet.append_row(row, value_input_option="USER_ENTERED")
    except Exception:
        pass


def _flush_operator_pending() -> Tuple[int, Optional[str]]:
    """Apply all queued operator actions in one batch (1 read + 1 write). Returns (applied_count, error_msg)."""
    pending = st.session_state.get("operator_pending") or []
    st.session_state.operator_pending = []
    if not pending:
        return 0, None
    lb, err = _get_leaderboard_worksheet_write()
    if err:
        st.session_state.operator_pending = pending  # put back
        return 0, err
    ss = getattr(lb, "spreadsheet", None)
    operator_name = (st.session_state.get("operator_name") or "").strip() or "—"
    if ss is None or not hasattr(ss, "values_batch_get") or not hasattr(ss, "values_batch_update"):
        # Fallback: apply each action individually
        applied = 0
        applied_items = []
        for item in pending:
            if item.get("action") == "spend_chips":
                ok, _ = operator_spend_chips(item["player"], item["amount"])
                if ok:
                    applied += 1
                    applied_items.append(item)
            elif item.get("action") == "earn_tickets":
                ok, _ = operator_earn_tickets(item["player"], item["amount"], item.get("category"))
                if ok:
                    applied += 1
                    applied_items.append(item)
        _append_operator_logs(ss, applied_items, operator_name)
        return applied, None
    try:
        sheet_title = getattr(lb, "title", "Sheet1")
        rp = f"'{sheet_title}'!" if " " in sheet_title else f"{sheet_title}!"
        # One batch read: row 1 (headers) + column A (player names)
        ranges = [f"{rp}1:1", f"{rp}A2:A1000"]
        resp = ss.values_batch_get(ranges)
        value_ranges = resp.get("valueRanges") or []
        if len(value_ranges) < 2:
            st.session_state.operator_pending = pending
            return 0, "Could not read sheet structure."
        vr0 = value_ranges[0].get("values") or []
        headers = (vr0[0] if vr0 and len(vr0) > 0 else [])
        col_a_rows = value_ranges[1].get("values") or []
        player_to_row = {}
        for i, row_list in enumerate(col_a_rows):
            name = (row_list[0] if row_list else "").strip()
            if name:
                player_to_row[name] = i + 2
        col_chips = next((i + 1 for i, h in enumerate(headers) if (str(h or "").strip() == CHIPS_SHEET_COL or str(h or "").strip() == "Chips")), None)
        col_spent = next((i + 1 for i, h in enumerate(headers) if str(h or "").strip() == CHIPS_SPENT_COL), None)
        col_tickets = next((i + 1 for i, h in enumerate(headers) if (str(h or "").strip() == TICKETS_SHEET_COL or str(h or "").strip() == "Raffle Tickets")), None)

        cells_to_read = set()
        for item in pending:
            player = (item.get("player") or "").strip()
            row = player_to_row.get(player)
            if not row:
                continue
            if item.get("action") == "spend_chips" and col_chips:
                cells_to_read.add((row, col_chips))
                if col_spent:
                    cells_to_read.add((row, col_spent))
            elif item.get("action") == "earn_tickets" and col_tickets:
                cells_to_read.add((row, col_tickets))
                cat = (item.get("category") or "").strip()
                if cat:
                    col_b = next((i + 1 for i, h in enumerate(headers) if str(h or "").strip() == cat), None)
                    if col_b:
                        cells_to_read.add((row, col_b))

        if not cells_to_read:
            st.session_state.operator_pending = pending
            return 0, "No valid pending actions (players or columns not found)."
        sorted_cells = sorted(cells_to_read)
        read_ranges = [f"{rp}{_rowcol_to_a1(r, c)}" for r, c in sorted_cells]
        resp2 = ss.values_batch_get(read_ranges)
        value_ranges2 = resp2.get("valueRanges") or []
        current = {}
        for idx, (r, c) in enumerate(sorted_cells):
            if idx >= len(value_ranges2):
                current[(r, c)] = 0
                continue
            vr = value_ranges2[idx].get("values") or []
            val = 0
            if vr and len(vr) > 0 and len(vr[0]) > 0:
                try:
                    val = vr[0][0]
                except (IndexError, TypeError):
                    pass
            current[(r, c)] = _parse_cell_int(val)

        for item in pending:
            player = (item.get("player") or "").strip()
            row = player_to_row.get(player)
            if not row:
                continue
            if item.get("action") == "spend_chips" and col_chips:
                amount = int(item.get("amount") or 0)
                if amount <= 0:
                    continue
                current[(row, col_chips)] = max(0, current.get((row, col_chips), 0) - amount)
                if col_spent:
                    current[(row, col_spent)] = current.get((row, col_spent), 0) + amount
            elif item.get("action") == "earn_tickets" and col_tickets:
                amount = int(item.get("amount") or 0)
                if amount <= 0:
                    continue
                current[(row, col_tickets)] = current.get((row, col_tickets), 0) + amount
                cat = (item.get("category") or "").strip()
                if cat:
                    col_b = next((i + 1 for i, h in enumerate(headers) if str(h or "").strip() == cat), None)
                    if col_b:
                        current[(row, col_b)] = current.get((row, col_b), 0) + amount

        data = [{"range": f"{rp}{_rowcol_to_a1(r, c)}", "values": [[current[(r, c)]]]} for r, c in sorted_cells]
        ss.values_batch_update(body={"valueInputOption": "USER_ENTERED", "data": data})
        _append_operator_logs(ss, pending, operator_name)
        return len(pending), None
    except Exception as e:
        st.session_state.operator_pending = pending
        return 0, str(e)


def operator_spend_chips(player_name: str, amount: int) -> Tuple[bool, str]:
    """Deduct chips from player. Returns (success, message). Uses batch API when possible (1 read + 1 write)."""
    if amount <= 0:
        return False, "Amount must be positive."
    lb, err = _get_leaderboard_worksheet_write()
    if err:
        return False, err
    try:
        col = _col_name_to_index(lb, CHIPS_SHEET_COL) or _col_name_to_index(lb, "Chips")
        if not col:
            return False, f"Sheet has no '{CHIPS_SHEET_COL}' or 'Chips' column."
        cells = lb.findall(str(player_name).strip(), in_column=1)
        if not cells:
            return False, f"Player '{player_name}' not found in sheet."
        row = cells[0].row
        col_spent = _col_name_to_index(lb, CHIPS_SPENT_COL)
        sheet_title = getattr(lb, "title", "Sheet1")
        range_prefix = f"'{sheet_title}'!" if " " in sheet_title else f"{sheet_title}!"

        # Batch read then batch write (counts as 1 read + 1 write request)
        ss = getattr(lb, "spreadsheet", None)
        if ss is not None and hasattr(ss, "values_batch_get") and hasattr(ss, "values_batch_update"):
            ranges = [f"{range_prefix}{_rowcol_to_a1(row, col)}"]
            if col_spent:
                ranges.append(f"{range_prefix}{_rowcol_to_a1(row, col_spent)}")
            resp = ss.values_batch_get(ranges)
            value_ranges = resp.get("valueRanges") or []
            current_val = (value_ranges[0].get("values") or [[]])[0][0] if value_ranges else 0
            try:
                current = int(float(str(current_val).replace(",", "").strip() or 0))
            except (ValueError, TypeError):
                current = 0
            new_val = max(0, current - amount)
            data = [{"range": f"{range_prefix}{_rowcol_to_a1(row, col)}", "values": [[new_val]]}]
            if col_spent:
                spent_val = (value_ranges[1].get("values") or [[]])[0][0] if len(value_ranges) > 1 else 0
                try:
                    spent = int(float(str(spent_val).replace(",", "").strip() or 0))
                except (ValueError, TypeError):
                    spent = 0
                data.append({"range": f"{range_prefix}{_rowcol_to_a1(row, col_spent)}", "values": [[spent + amount]]})
            ss.values_batch_update(body={"valueInputOption": "USER_ENTERED", "data": data})
            return True, f"Deducted {amount} chips from {player_name}. New balance: {new_val}."
        # Fallback: per-cell reads/writes
        current_val = lb.cell(row, col).value
        try:
            current = int(float(str(current_val).replace(",", "").strip() or 0))
        except (ValueError, TypeError):
            current = 0
        new_val = max(0, current - amount)
        lb.update_cell(row, col, new_val)
        if col_spent:
            spent_val = lb.cell(row, col_spent).value
            try:
                spent = int(float(str(spent_val).replace(",", "").strip() or 0))
            except (ValueError, TypeError):
                spent = 0
            lb.update_cell(row, col_spent, spent + amount)
        return True, f"Deducted {amount} chips from {player_name}. New balance: {new_val}."
    except Exception as e:
        return False, str(e)


def operator_earn_tickets(player_name: str, amount: int, category_label: Optional[str] = None) -> Tuple[bool, str]:
    """Add tickets to player (Earned Tickets and optionally a bucket column). Uses batch API when possible (1 read + 1 write)."""
    if amount <= 0:
        return False, "Amount must be positive."
    lb, err = _get_leaderboard_worksheet_write()
    if err:
        return False, err
    try:
        tickets_col = TICKETS_SHEET_COL
        col_tickets = _col_name_to_index(lb, tickets_col)
        if not col_tickets:
            col_tickets = _col_name_to_index(lb, "Raffle Tickets")
        if not col_tickets:
            return False, f"Sheet has no '{tickets_col}' or 'Raffle Tickets' column."
        cells = lb.findall(str(player_name).strip(), in_column=1)
        if not cells:
            return False, f"Player '{player_name}' not found in sheet."
        row = cells[0].row
        col_bucket = _col_name_to_index(lb, (category_label or "").strip()) if (category_label and category_label.strip()) else None
        sheet_title = getattr(lb, "title", "Sheet1")
        range_prefix = f"'{sheet_title}'!" if " " in sheet_title else f"{sheet_title}!"

        def _read_int_from_val(val):
            try:
                return int(float(str(val).replace(",", "").strip() or 0))
            except (ValueError, TypeError):
                return 0

        ss = getattr(lb, "spreadsheet", None)
        if ss is not None and hasattr(ss, "values_batch_get") and hasattr(ss, "values_batch_update"):
            ranges = [f"{range_prefix}{_rowcol_to_a1(row, col_tickets)}"]
            if col_bucket:
                ranges.append(f"{range_prefix}{_rowcol_to_a1(row, col_bucket)}")
            resp = ss.values_batch_get(ranges)
            value_ranges = resp.get("valueRanges") or []
            current_tickets = _read_int_from_val((value_ranges[0].get("values") or [[]])[0][0]) if value_ranges else 0
            new_tickets = current_tickets + amount
            data = [{"range": f"{range_prefix}{_rowcol_to_a1(row, col_tickets)}", "values": [[new_tickets]]}]
            msg = f"Added {amount} tickets to {player_name}. Total earned tickets: {new_tickets}."
            if col_bucket:
                current_bucket = _read_int_from_val((value_ranges[1].get("values") or [[]])[0][0]) if len(value_ranges) > 1 else 0
                data.append({"range": f"{range_prefix}{_rowcol_to_a1(row, col_bucket)}", "values": [[current_bucket + amount]]})
                msg += f" Bucket '{category_label}' now: {current_bucket + amount}."
            elif category_label and category_label.strip():
                msg += f" (Bucket '{category_label}' not found; only Earned Tickets updated.)"
            ss.values_batch_update(body={"valueInputOption": "USER_ENTERED", "data": data})
            return True, msg
        # Fallback: per-cell
        def _read_int(c):
            return _read_int_from_val(lb.cell(row, c).value)
        current_tickets = _read_int(col_tickets)
        new_tickets = current_tickets + amount
        lb.update_cell(row, col_tickets, new_tickets)
        msg = f"Added {amount} tickets to {player_name}. Total earned tickets: {new_tickets}."
        if col_bucket:
            current_bucket = _read_int(col_bucket)
            lb.update_cell(row, col_bucket, current_bucket + amount)
            msg += f" Bucket '{category_label}' now: {current_bucket + amount}."
        elif category_label and category_label.strip():
            msg += f" (Bucket '{category_label}' not found; only Earned Tickets updated.)"
        return True, msg
    except Exception as e:
        return False, str(e)


def load_data() -> pd.DataFrame:
    """Load leaderboard data from Google Sheets (or demo if enabled)."""
    if DEMO_MODE:
        return load_demo_data()

    # If gspread/google-auth aren't installed, use demo data so the app still runs
    if Credentials is None or gspread is None:
        st.session_state.google_deps_missing = True
        return load_demo_data()

    err = _check_google_secrets()
    if err:
        raise RuntimeError(err)

    st.session_state.google_deps_missing = False
    client = get_sheet_client()
    try:
        sheet = client.open_by_key(st.secrets["sheets"]["sheet_id"]).worksheet(
            st.secrets["sheets"]["worksheet_name"]
        )
    except PermissionError:
        service_email = st.secrets["gcp_service_account"].get("client_email", "your service account email")
        raise RuntimeError(
            f"The Google Sheet is not shared with the app. Open your sheet → Share → add **{service_email}** as Editor (or Viewer), then try again."
        )
    records = sheet.get_all_records()
    if not records:
        return pd.DataFrame(columns=["Player", "Dollar Amount", "Chips"])

    df = pd.DataFrame(records)
    # Sheet columns: Player, Dollar Amount, Chips. Rank by Chips.
    if "Player" not in df.columns:
        df["Player"] = ""
    for col in ["Dollar Amount", "Chips"]:
        if col not in df.columns:
            df[col] = 0
    df["Dollar Amount"] = pd.to_numeric(df["Dollar Amount"], errors="coerce").fillna(0)
    df["Chips"] = pd.to_numeric(df["Chips"], errors="coerce").fillna(0).astype(int)
    df = df.sort_values("Chips", ascending=False).reset_index(drop=True)
    df["Rank"] = df.index + 1
    # Don't expose amounts on leaderboard; keep RaffleTickets for code that expects it
    df["RaffleTickets"] = 0
    return df


def get_position_map(df: pd.DataFrame) -> dict:
    """Map player -> rank for change detection."""
    return {row["Player"]: int(row["Rank"]) for _, row in df.iterrows()}


def render_row(row, movement: str):
    """Render a single leaderboard row with styling and movement indicator."""
    # Medal colors for top 3
    if row["Rank"] <= 2:
        row_class = "gold"
    elif row["Rank"] <= 4:
        row_class = "silver"
    elif row["Rank"] <= 6:
        row_class = "bronze"
    else:
        row_class = "neutral"

    # Strikethrough if chips are negative
    chips_val = row.get(CHIPS_SHEET_COL, row.get("Chips", 0))
    try:
        chips_num = int(float(str(chips_val).replace(",", "").strip() or 0))
    except (TypeError, ValueError):
        chips_num = 0
    if chips_num < 0:
        row_class += " negative-chips"

    movement_symbol = {
        "up": "▲",
        "down": "▼",
        "same": "●",
    }[movement]

    movement_class = movement

    # Meta line: Whale if $100+, then high rolling status, then table (no $ amount shown)
    dollar_val = row.get("Dollar Amount", 0)
    try:
        dollar_num = float(dollar_val) if pd.notna(dollar_val) else 0
    except (TypeError, ValueError):
        dollar_num = 0
    is_whale = dollar_num >= 100

    status_val = row.get("High Rolling Status", None)
    table_val = row.get("Table", None)
    last_update_val = row.get("LastUpdate", None)
    meta_parts = []
    if is_whale:
        whale_style = "color:#000;" if row["Rank"] <= 6 else ""
        meta_parts.append(f"<span class='status-badge' style='{whale_style}'>Whale</span>")
    if pd.notna(status_val) and str(status_val).strip():
        status_text = str(status_val).strip()
        top_style = "color:#000;" if row["Rank"] <= 6 else ""
        if status_text.lower() != "whale":
            meta_parts.append(f"<span class='status-badge' style='{top_style}'>{status_text}</span>")
        elif not is_whale:
            meta_parts.append(f"<span class='status-badge' style='{top_style}'>{status_text}</span>")
    if pd.notna(table_val) and str(table_val).strip():
        meta_parts.append(str(table_val).strip())
    if pd.notna(last_update_val) and str(last_update_val).strip():
        meta_parts.append(str(last_update_val).strip())
    meta_line = " • ".join(meta_parts)
    meta_html = f"<div class='name-meta'>{meta_line}</div>" if meta_line else ""

    player_safe = escape(str(row["Player"]))
    if is_whale:
        player_safe = "🐋 " + player_safe
    rank_num = int(row["Rank"])
    if rank_num <= 2:
        medal = "🥇"
    elif rank_num <= 4:
        medal = "🥈"
    elif rank_num <= 6:
        medal = "🥉"
    else:
        medal = ""
    rank_display = str(rank_num)
    medal_html = f'<div class="medal-cell">{medal}</div>' if medal else '<div class="medal-cell"></div>'
    # Build HTML: medal left of the gradient row (rank, name only; no amounts)
    row_html = (
        f'<div class="leader-row-wrap">'
        f'{medal_html}'
        f'<div class="leader-row {row_class}">'
        f'<div class="rank">{rank_display}</div>'
        f'<div class="name"><div class="name-main">{player_safe}</div>{meta_html}</div>'
        f'<div class="{movement_class}" style="width:3rem;text-align:right;">{movement_symbol}</div>'
        "</div></div>"
    )
    st.markdown(row_html, unsafe_allow_html=True)


# Less confetti, bigger pieces. Re-runs on each winner change. Iframe stays transparent so winner text shows through.
CONFETTI_HTML = """
<style>html, body { background: transparent !important; min-height: 100vh; } canvas { display: block; background: transparent !important; }</style>
<script>
(function() {
    var script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/canvas-confetti@1.9.4/dist/confetti.browser.min.js';
    script.onload = function() {
        if (typeof confetti !== 'function') return;
        var opts = { particleCount: 45, scalar: 2.2, spread: 70, ticks: 200 };
        confetti({ origin: { x: 0.4, y: 0.6 }, ...opts });
        confetti({ origin: { x: 0.6, y: 0.6 }, ...opts });
        var c = document.querySelector('canvas');
        if (c) { c.style.background = 'transparent'; c.style.pointerEvents = 'none'; }
    };
    document.head.appendChild(script);
})();
</script>
"""

WINNER_DURATION_SECONDS = 20


def _celebration_view():
    """Full-screen winner reveal: one winner every 20s with confetti. Data from WinningResults sheet or top 3 leaderboard."""
    winners = st.session_state.get("celebration_winners")
    if winners is None:
        winners = get_item_winners()
        if not winners:
            try:
                df = load_data()
                if not df.empty:
                    for i, (_, row) in enumerate(df.head(3).iterrows()):
                        winners.append({"item": ["1st Place", "2nd Place", "3rd Place"][i], "winner": str(row.get("Player", "—"))})
            except Exception:
                pass
        st.session_state.celebration_winners = winners
        st.session_state.celebration_start = time.time()
    if not winners:
        st.markdown(
            "<p style='text-align:center; color:#9ca3af;'>No winners yet. Click <b>Draw Winners</b> to run the draw and fill the <b>WinningResults</b> sheet, or go back to the leaderboard.</p>",
            unsafe_allow_html=True,
        )
        st.markdown("<p style='text-align:center;'><a href='?' style='color:#f97316;'>Back to Leaderboard</a></p>", unsafe_allow_html=True)
        return

    # Fragment reruns every WINNER_DURATION_SECONDS: new winner is shown and confetti runs in the same run (right after name change).
    # Confetti iframe behind winner block so the name is always visible (Streamlit iframes can't be made reliably transparent).
    st.markdown(
        "<style>"
        ".stApp iframe { position: fixed !important; top: 0 !important; left: 0 !important; "
        "width: 100vw !important; height: 100vh !important; z-index: 1 !important; pointer-events: none !important; border: none !important; }"
        ".celebration-winner-block { position: relative !important; z-index: 10 !important; }"
        "</style>",
        unsafe_allow_html=True,
    )
    @st.fragment(run_every=timedelta(seconds=WINNER_DURATION_SECONDS))
    def _show_winner():
        winners_list = st.session_state.celebration_winners
        start = st.session_state.get("celebration_start", time.time())
        idx = int((time.time() - start) / WINNER_DURATION_SECONDS) % len(winners_list)
        entry = winners_list[idx]
        item, winner = entry.get("item", "Winner"), entry.get("winner", "—")
        probability = entry.get("probability", "")
        # Vary HTML by idx so iframe content changes each winner = confetti script reruns on name change
        components.html(CONFETTI_HTML + f"<!-- winner {idx} -->", height=100, scrolling=False)
        # Winner block on top so name is never hidden; show probability under winner name
        prob_html = f"<p style='color:#9ca3af; font-size:1.25rem; margin-top:0.5rem;'>{escape(probability)}</p>" if probability else ""
        st.markdown(
            f"<div class='celebration-winner-block' style='text-align:center; padding:4rem 2rem; background:transparent; min-height:60vh;'>"
            f"<p style='color:#f97316; font-size:1.8rem; letter-spacing:0.2em; text-transform:uppercase; margin-bottom:1rem;'>{escape(item)}</p>"
            f"<p style='color:#fff; font-size:4rem; font-weight:800; letter-spacing:0.05em;'>{escape(winner)}</p>"
            f"{prob_html}"
            f"<p style='color:#6b7280; font-size:1.2rem; margin-top:2rem;'>{idx + 1} of {len(winners_list)} · next in {WINNER_DURATION_SECONDS}s</p>"
            f"</div>",
            unsafe_allow_html=True,
        )

    _show_winner()
    st.markdown(
        "<p style='text-align:center; margin-top:1rem;'>"
        "<a href='?' style='color:#6b7280;'>Back to Leaderboard</a></p>",
        unsafe_allow_html=True,
    )


def _operator_view():
    """Page for operators to spend chips or add earned tickets for a player. Caches sheet data to avoid extra read requests."""
    st.markdown(
        "<h1 style='color:#f97316; font-size:2.2rem; letter-spacing:0.1em; "
        "text-transform:uppercase; text-align:center; margin-bottom:0.5rem;'>Operator</h1>"
        "<p style='text-align:center; color:#9ca3af; font-size:1rem; margin-bottom:2rem;'>"
        "Spend chips or add earned tickets</p>",
        unsafe_allow_html=True,
    )

    # PIN gate: read PIN from Config sheet A3 first, then fall back to secrets
    required_pin = get_operator_pin_from_sheet()
    if not required_pin:
        try:
            required_pin = st.secrets.get("operator_pin") or st.secrets.get("operator", {}).get("pin")
            required_pin = str(required_pin).strip() if required_pin else None
        except Exception:
            pass
    if required_pin:
        required_pin = str(required_pin).strip()
    if required_pin and not st.session_state.get("operator_unlocked"):
        name_input = st.text_input("Your name", key="operator_name_input", placeholder="Operator name (for logs)")
        pin_input = st.text_input("Enter PIN", type="password", key="operator_pin_input", placeholder="PIN")
        if st.button("Unlock", key="operator_unlock_btn", type="primary"):
            if (pin_input or "").strip() != required_pin:
                st.error("Incorrect PIN.")
            else:
                st.session_state.operator_unlocked = True
                st.session_state.operator_name = (name_input or "").strip() or "—"
                st.rerun()
        st.markdown("<p style='text-align:center; margin-top:1.5rem;'><a href='?' style='color:#6b7280;'>Back to Leaderboard</a></p>", unsafe_allow_html=True)
        return

    # Show who is logged in (for logging)
    op_name = st.session_state.get("operator_name") or "—"
    if op_name != "—":
        st.caption(f"Logged in as **{op_name}** (actions are written to the Log sheet).")

    # Cache leaderboard and options so we don't refetch on every rerun (reduces API reads)
    if st.session_state.get("operator_options") is None:
        st.session_state.operator_options = get_winning_options()
    options_list = st.session_state.operator_options

    if st.session_state.get("operator_df") is None:
        try:
            df = load_data()
            st.session_state.operator_df = df
        except RuntimeError as e:
            st.error(str(e))
            st.caption("If the problem persists, try refreshing or syncing after 60 seconds.")
            st.markdown("<p style='text-align:center;'><a href='?' style='color:#6b7280;'>Back to Leaderboard</a></p>", unsafe_allow_html=True)
            return
    else:
        df = st.session_state.operator_df

    # Queue: flush once per minute when pending and interval elapsed
    if "operator_pending" not in st.session_state:
        st.session_state.operator_pending = []
    if "last_operator_flush_time" not in st.session_state:
        st.session_state.last_operator_flush_time = None
    pending = st.session_state.operator_pending
    now = time.time()
    if pending and (
        st.session_state.last_operator_flush_time is None
        or (now - st.session_state.last_operator_flush_time) >= OPERATOR_FLUSH_INTERVAL_SECONDS
    ):
        applied, flush_err = _flush_operator_pending()
        st.session_state.last_operator_flush_time = now
        if applied:
            st.session_state.operator_df = load_data()
            st.success(f"Applied {applied} queued action(s) to the sheet.")
        if flush_err:
            st.error(f"Flush error: {flush_err}")
            st.caption("Try refreshing or syncing after 60 seconds.")
        st.rerun()

    @st.fragment(run_every=timedelta(seconds=OPERATOR_FLUSH_INTERVAL_SECONDS))
    def _operator_flush_timer():
        """Flush queued actions once per minute while operator page is open."""
        p = st.session_state.get("operator_pending") or []
        if not p:
            return
        last = st.session_state.get("last_operator_flush_time")
        if last is None:
            return
        if (time.time() - last) >= OPERATOR_FLUSH_INTERVAL_SECONDS:
            applied, flush_err = _flush_operator_pending()
            st.session_state.last_operator_flush_time = time.time()
            if applied:
                st.session_state.operator_df = load_data()
                st.success(f"Applied {applied} queued action(s) to the sheet.")
            if flush_err:
                st.error(f"Flush error: {flush_err}")
                st.caption("Try refreshing or syncing after 60 seconds.")
            st.rerun()
    _operator_flush_timer()

    if df.empty:
        st.warning("No players in the sheet yet. Add players on the main sheet first.")
        st.markdown("<p style='text-align:center;'><a href='?' style='color:#6b7280;'>Back to Leaderboard</a></p>", unsafe_allow_html=True)
        return
    players = sorted(df["Player"].astype(str).dropna().unique().tolist(), key=str.strip)

    player = st.selectbox(
        "Select player",
        options=players,
        key="operator_player",
        help="Choose the player to spend chips or add tickets for.",
    )
    if not player:
        st.markdown("<p style='text-align:center;'><a href='?' style='color:#6b7280;'>Back to Leaderboard</a></p>", unsafe_allow_html=True)
        return

    # Show balance for selected player: sheet value + optimistic deltas from queued actions
    row = df.loc[df["Player"].astype(str).str.strip() == str(player).strip()]
    pending_list = st.session_state.get("operator_pending") or []
    player_stripped = str(player).strip()
    chips_delta = 0
    tickets_delta = 0
    for item in pending_list:
        if (item.get("player") or "").strip() != player_stripped:
            continue
        if item.get("action") == "spend_chips":
            chips_delta -= int(item.get("amount") or 0)
        elif item.get("action") == "earn_tickets":
            tickets_delta += int(item.get("amount") or 0)
    if not row.empty:
        r = row.iloc[0]
        base_chips = 0
        if CHIPS_SHEET_COL in df.columns:
            base_chips = _parse_cell_int(r.get(CHIPS_SHEET_COL))
        elif "Chips" in df.columns:
            base_chips = _parse_cell_int(r.get("Chips"))
        base_tickets = int(r.get("RaffleTickets", 0)) if "RaffleTickets" in r else 0
        try:
            base_tickets = int(base_tickets)
        except (ValueError, TypeError):
            base_tickets = 0
        live_chips = base_chips + chips_delta
        live_tickets = base_tickets + tickets_delta
        parts = [f"**{live_chips:,}** chips", f"**{live_tickets:,}** tickets"]
        st.info("Balance (live): " + "  ·  ".join(parts))
        if chips_delta != 0 or tickets_delta != 0:
            st.caption("Includes queued changes — not yet synced to sheet.")

    # Sync delay indicator (always show; countdown when there are pending actions)
    st.markdown("---")
    st.caption("**Sync delay:** Operator changes are batched and sent every **" + str(OPERATOR_FLUSH_INTERVAL_SECONDS) + " seconds** to reduce API usage.")
    pending_count = len(pending_list)
    if pending_count:
        st.caption(f"**{pending_count}** action(s) queued (see countdown below).")
        if st.button("Flush now", key="operator_flush_now"):
            applied, flush_err = _flush_operator_pending()
            st.session_state.last_operator_flush_time = time.time()
            if applied:
                st.session_state.operator_df = load_data()
                st.success(f"Applied {applied} queued action(s) to the sheet.")
            if flush_err:
                st.error(f"Flush error: {flush_err}")
                st.caption("Try refreshing or syncing after 60 seconds.")
            st.rerun()

    st.markdown("---")
    st.subheader("Spend chips")
    spend_amount = st.number_input("Chips to deduct", min_value=1, value=10, step=1, key="spend_chips_amount")
    if st.button("Spend chips", key="spend_chips_btn", type="primary"):
        st.session_state.operator_pending = st.session_state.get("operator_pending") or []
        st.session_state.operator_pending.append({"action": "spend_chips", "player": player, "amount": spend_amount})
        if st.session_state.last_operator_flush_time is None:
            st.session_state.last_operator_flush_time = time.time()
        st.success(f"Queued: deduct {spend_amount} chips from {player}. Applies within {OPERATOR_FLUSH_INTERVAL_SECONDS} s.")
        st.rerun()

    st.markdown("---")
    st.subheader("Earn tickets")
    earn_amount = st.number_input("Tickets to add", min_value=1, value=1, step=1, key="earn_tickets_amount")
    category = st.selectbox(
        "Category (optional — also adds to this prize bucket)",
        options=[None] + (options_list or []),
        format_func=lambda x: "(none)" if x is None else x,
        key="operator_category",
    )
    if st.button("Add tickets", key="earn_tickets_btn", type="primary"):
        st.session_state.operator_pending = st.session_state.get("operator_pending") or []
        st.session_state.operator_pending.append({
            "action": "earn_tickets", "player": player, "amount": earn_amount,
            "category": category if category else None,
        })
        if st.session_state.last_operator_flush_time is None:
            st.session_state.last_operator_flush_time = time.time()
        cat_txt = f" (bucket: {category})" if category else ""
        st.success(f"Queued: +{earn_amount} tickets for {player}{cat_txt}. Applies within {OPERATOR_FLUSH_INTERVAL_SECONDS} s.")
        st.rerun()

    st.markdown("<br>")
    if st.button("Refresh player list & balance", key="operator_refresh"):
        st.session_state.operator_df = load_data()
        st.rerun()
    st.markdown("<p style='text-align:center;'><a href='?' style='color:#6b7280;'>Back to Leaderboard</a></p>", unsafe_allow_html=True)


def main():
    if st.query_params.get("view") == "celebration":
        _celebration_view()
        return
    # if st.query_params.get("view") == "operator":
    #     _operator_view()
    #     return
    # Clear operator cache, queue, unlock, and name when not on operator page
    for key in ("operator_df", "operator_options", "operator_pending", "last_operator_flush_time", "operator_unlocked", "operator_name"):
        if key in st.session_state:
            del st.session_state[key]

    scale = st.session_state.get("leaderboard_scale", 50)
    st.markdown(f'<div class="leaderboard-scale-wrap" style="font-size: {scale}%;">', unsafe_allow_html=True)
    st.markdown(
        "<h1 style='color:#f97316; font-size:clamp(1.5rem, 4vw, 2.5rem); letter-spacing:0.1em; "
        "text-transform:uppercase; margin:0 0 0.5rem 0; text-align:center;'>Leaderboard</h1>",
        unsafe_allow_html=True,
    )
    _leaderboard_fragment()
    st.markdown("</div>", unsafe_allow_html=True)

    # View Winners button hidden
    # st.markdown("<br><br>", unsafe_allow_html=True)
    # st.markdown('<div class="tv-bottom-actions">', unsafe_allow_html=True)
    # col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
    # with col2:
    #     if st.button("View Winners", key="view_winners", use_container_width=True):
    #         if "celebration_winners" in st.session_state:
    #             del st.session_state["celebration_winners"]
    #         st.query_params["view"] = "celebration"
    #         st.rerun()
    # st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()

