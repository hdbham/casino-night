# Link the leaderboard to Google Sheets

Follow these steps to use a real Google Sheet instead of demo data.

---

## 1. Create a Google Sheet

1. Go to [Google Sheets](https://sheets.google.com) and create a new spreadsheet.
2. Name the **first worksheet** (tab at the bottom) **`CasinoLeaderboard`** (or set `worksheet_name` in secrets to match).
3. In **row 1**, add headers:
   - **A1:** `Player`
   - **B1:** `Table`
   - **C1:** `Chips`
   - **D1:** `LastUpdate`
4. Add a few test rows (e.g. names, table names, numbers in Chips).
5. **Get the Sheet ID** from the URL:
   - URL looks like: `https://docs.google.com/spreadsheets/d/ **1abc...xyz** /edit`
   - The part between `/d/` and `/edit` is your **Sheet ID**. Copy it.

---

## 2. Google Cloud: project and service account

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or select one):
   - Top bar: click the project name → **New Project** → name it (e.g. "Casino Night") → **Create**.
3. Enable the APIs:
   - **APIs & Services** → **Library**.
   - Search for **Google Sheets API** → open it → **Enable**.
   - (Optional) Search **Google Drive API** → **Enable** (helps if you open sheets by link).
4. Create a service account:
   - **APIs & Services** → **Credentials** → **Create Credentials** → **Service account**.
   - Name it (e.g. "casino-leaderboard") → **Create and Continue** → **Done**.
5. Create a key for that service account:
   - Click the new service account → **Keys** tab → **Add key** → **Create new key** → **JSON** → **Create**.
   - A JSON file downloads. Keep it private (do not commit to git).

---

## 3. Put the key into Streamlit secrets

You need to copy values from the JSON file into `.streamlit/secrets.toml`.

**From the JSON file you downloaded:**

| JSON key | Use in `secrets.toml` |
|----------|------------------------|
| `project_id` | `project_id` |
| `private_key` | `private_key` (keep the `\n` inside the key as real newlines or as `\n`) |
| `client_email` | `client_email` |
| `token_uri` is fixed | `token_uri = "https://oauth2.googleapis.com/token"` |

**Important for `private_key`:**  
The key is a long string with `\n` between lines. In TOML you can either:

- Use a **multiline literal** so you paste the key with real newlines:
  ```toml
  private_key = """
  -----BEGIN PRIVATE KEY-----
  MIIEvQIBADANBg...
  ...
  -----END PRIVATE KEY-----
  """
  ```
- Or one line with `\n` kept as two characters (what we had in the template).

**Example `.streamlit/secrets.toml`:**

```toml
[gcp_service_account]
type = "service_account"
project_id = "your-project-id"
private_key = """
-----BEGIN PRIVATE KEY-----
YOUR_ACTUAL_KEY_LINES_HERE
-----END PRIVATE KEY-----
"""
client_email = "casino-leaderboard@your-project-id.iam.gserviceaccount.com"
token_uri = "https://oauth2.googleapis.com/token"

[sheets]
sheet_id = "1abc...xyz"
worksheet_name = "CasinoLeaderboard"
```

Replace `your-project-id`, the key block, and `1abc...xyz` with your real values.

**Optional: Operator PIN and name**  
To require a PIN to open the Operator page (spend chips / earn tickets), put the PIN in the **Config** sheet:

- **Config, cell B2:** operator PIN (e.g. `1234`). Change it in the sheet anytime; the app reads it when the unlock form is shown.

If Config B2 is empty, the app falls back to `secrets.toml`:

```toml
operator_pin = "1234"
```

If neither is set, the Operator page is open without a PIN. When a PIN is required, operators also enter their **name**; both PIN and name are used to write **logs** to a **Log** sheet in the same spreadsheet (one row per applied action: timestamp, operator name, action, player, amount, details). The **Log** sheet is created automatically if it does not exist.

---

## 4. Share the Google Sheet with the service account

1. Open your Google Sheet.
2. Click **Share**.
3. In "Add people and groups", paste the **service account email** (same as `client_email` in secrets, e.g. `casino-leaderboard@your-project-id.iam.gserviceaccount.com`).
4. Set permission to **Editor** (or at least "Viewer" if you only read).
5. Uncheck "Notify people" (the service account doesn’t read email) → **Share**.

---

## 4.1 Countdown timer (optional)

To show a countdown instead of the current time:

1. In the **same** spreadsheet, add a worksheet named **`Config`** (tab at the bottom).
2. In **Config**, put the event end date/time (deadline) in cell **A2**, in one of these formats:
   - `2026-02-19 21:00:00`
   - `2026-02-19 21:00`
   - `2/19/2026 9:00 PM`
   - `2/19/2026 21:00`
3. Share the sheet with the service account (same as step 4 above). The app reads **Config** from the same `sheet_id`.
4. Optional: in `.streamlit/secrets.toml` under `[sheets]` you can set:
   - `countdown_worksheet = "Config"` (default)
   - `countdown_cell = "A2"` (default)

If **Config** or A2 is missing or invalid, the app shows the current clock instead. **B2** on Config is reserved for the operator PIN (see Operator PIN above); the app only reads B2 and never writes to Config column B, so your PIN is never overwritten.

### 4.2 Winner celebration (item winners + confetti)

To show a **winner reveal** screen (one winner every 20 seconds with confetti):

1. Run **Draw Winners** in the app. Results are written to the **WinningResults** sheet (Category, Winner, Probability).
2. Open the celebration view by adding **`?view=celebration`** to the URL (e.g. `http://localhost:8501/?view=celebration`). It reads from **WinningResults**.

If WinningResults is empty, the app uses the **top 3** from the leaderboard as “1st Place”, “2nd Place”, “3rd Place”. Use the “Back to Leaderboard” link to return to the main view.

### 4.3 Ticket buckets (optional)

You can let players **allocate their tickets per prize**. Then each draw uses only the tickets in that prize’s “bucket.”

1. **Options sheet (recommended):** Add a worksheet named **`Options`**. Put `Winning Options` in **A1** and one prize per row in **A2** down (e.g. `Tattoo`, `Spa`, `Gift Card`, `Raising Canes`, `Pokemon Set`, `Pie an admin`). The app reads categories only from the Options sheet and adds matching bucket columns to the Casino Night sheet. If you don't use an Options sheet, you can put category names in **Config** column B as a fallback.
2. When you click **Draw Winners**, the app uses the Options sheet for categories (if present), else Config column B, and **adds any missing bucket columns** to the Casino Night sheet. Fill in ticket numbers per player in those columns.
3. In each row, the player puts a number in a bucket column = how many tickets they put toward that prize. **Raffle Tickets** stays as their total (for leaderboard); bucket columns are used only for the corresponding draws.
4. If a bucket column is empty for everyone, that draw uses **Raffle Tickets** instead. The same person can win more than one prize if they had tickets in multiple buckets.

| Player | Raffle Tickets | Spa Winner | Tattoo Winner | Gift Card Winner |
|--------|----------------|------------|---------------|------------------|
| Alice  | 12             | 5          | 3             | 4                |
| Bob    | 5              | 0          | 5             | 0                |

---

## 5. Use the app with Google

1. In `app.py`, set:
   ```python
   DEMO_MODE = False
   ```
2. Install dependencies if you haven’t:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the app:
   ```bash
   streamlit run app.py
   ```

The leaderboard reads from your sheet. **Updates are push-only**: there is no interval refresh. Add the Apps Script below and set `sync_worksheet` in secrets so the app refetches only when the sheet is edited.

---

## 6. Push on edit (Apps Script)

When you add the script and the sync config, the app checks a **Sync** cell every 10 seconds and only refetches when that value changes (so edits show up within ~10 seconds and you stay under API quota).

### Step 1: Add the script to your sheet

1. Open your Google Sheet → **Extensions** → **Apps Script**.
2. Delete any existing code and paste in the contents of **`AppsScript_PushOnEdit.js`** from this project (or the code below).
3. Save (Ctrl+S or the disk icon). You can close the Apps Script tab.

**Script to paste:**

```javascript
const SYNC_SHEET_NAME = 'Sync';
const SYNC_CELL = 'A1';

function onEdit(e) {
  var ss = e ? e.source : SpreadsheetApp.getActiveSpreadsheet();
  var sync = ss.getSheetByName(SYNC_SHEET_NAME);
  if (!sync) {
    sync = ss.insertSheet(SYNC_SHEET_NAME);
  }
  sync.getRange(SYNC_CELL).setValue(new Date());
}
```

The script runs automatically on every edit. It creates a **Sync** tab if needed and writes the current time to **Sync!A1**. The Streamlit app reads that cell; when it changes, it refetches the leaderboard.

### Step 2: Turn on push in the app

In **`.streamlit/secrets.toml`**, under `[sheets]`, add (or uncomment):

```toml
sync_worksheet = "Sync"
sync_cell = "A1"
```

Restart the Streamlit app. It will check the Sync cell every 15 seconds and refetch **only when the sheet was edited** (Sync cell changed). No interval-based refresh.
