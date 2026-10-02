/**
 * Google Apps Script: Ghostfolio Google Sheet to Google Drive CSV Sync
 * 
 * Target Sheet: https://docs.google.com/spreadsheets/d/<YOUR_SPREADSHEET_ID>/edit
 * Output File: portfolio_docker_sync.csv (stored in Google Drive root)
 *
 * NOTE ON PERMISSIONS:
 * Google Apps Script simple triggers (like basic onEdit(e)) CANNOT access DriveApp due to
 * Google security restrictions. To automate execution on every edit, you MUST set up an
 * "Installable Trigger" following the setup instructions in the README.
 */

const SYNC_FILE_NAME = "portfolio_docker_sync.csv";

/**
 * Creates a custom menu in the Google Sheet UI for quick manual sync and verification.
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("📊 Ghostfolio Sync")
    .addItem("Sync Now to Drive", "exportToDriveCSV")
    .addToUi();
}

/**
 * Main trigger function to be bound to an Installable "On edit" trigger.
 * Can also be run directly from the Apps Script editor or custom menu.
 */
function exportToDriveCSV() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  if (!ss) {
    Logger.log("No active spreadsheet found.");
    return;
  }

  // Always read the first tab as per requirement
  const sheet = ss.getSheets()[0];
  const range = sheet.getDataRange();
  const values = range.getValues();

  if (!values || values.length === 0) {
    Logger.log("Spreadsheet is empty. Nothing to export.");
    return;
  }

  const timeZone = ss.getSpreadsheetTimeZone() || Session.getScriptTimeZone();
  const csvRows = [];

  for (let r = 0; r < values.length; r++) {
    const row = values[r];

    // Skip trailing blank rows
    const isBlank = row.every(cell => cell === "" || cell === null || cell === undefined);
    if (isBlank) continue;

    const formattedRow = row.map(cell => {
      if (cell === null || cell === undefined) {
        return "";
      }

      // If the cell contains a native Date object, format it as ISO YYYY-MM-DD
      if (cell instanceof Date) {
        return Utilities.formatDate(cell, timeZone, "yyyy-MM-dd");
      }

      let text = cell.toString().trim();

      // Escape quotes and wrap with quotes if comma, newline, or quote exists (RFC 4180 standard)
      if (text.includes('"') || text.includes(',') || text.includes('\n') || text.includes('\r')) {
        text = '"' + text.replace(/"/g, '""') + '"';
      }
      return text;
    });

    csvRows.push(formattedRow.join(","));
  }

  const csvContent = csvRows.join("\r\n");

  // Save or update file in Google Drive
  const existingFiles = DriveApp.getFilesByName(SYNC_FILE_NAME);

  if (existingFiles.hasNext()) {
    const file = existingFiles.next();
    file.setContent(csvContent);
    Logger.log(`Updated existing file: ${SYNC_FILE_NAME} (ID: ${file.getId()})`);
  } else {
    const file = DriveApp.createFile(SYNC_FILE_NAME, csvContent, MimeType.CSV);
    Logger.log(`Created new file: ${SYNC_FILE_NAME} (ID: ${file.getId()})`);
  }
}
