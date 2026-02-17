/**
 * Casino Night Leaderboard – push on edit
 *
 * 1. In your Google Sheet: Extensions → Apps Script
 * 2. Replace any code with this file contents and save (Ctrl+S).
 * 3. The script runs automatically when anyone edits the sheet.
 *    It writes a timestamp to the "Sync" tab so the Streamlit app
 *    knows to refetch (no need to poll every 30s).
 *
 * The "Sync" tab is created automatically if it doesn’t exist.
 * Share the sheet with your Streamlit service account (Viewer is enough).
 */

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
