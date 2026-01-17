# User Guide (English)

## Table of Contents
1. [Introduction](#introduction)
2. [Prerequisites](#prerequisites)
3. [Installation](#installation)
4. [How It Works](#how-it-works)
5. [Configuration](#configuration)
6. [Usage](#usage)
7. [Automation Setup](#automation-setup)
8. [FAQ](#faq)

---

## Introduction

Investment Portfolio Automation Tool is a Google Apps Script-based tool designed for investors who need to track their portfolio changes daily.

### Core Features

| Feature | Description |
|---------|-------------|
| 📸 **Daily Snapshot Generation** | Automatically copies template and creates dated daily files |
| 📈 **Hybrid Price Updates** | A-shares via Sina Finance API, global stocks via Google Finance |
| 📊 **Historical Trend Chart** | Incrementally updates asset trend visualization |
| 📅 **Monthly Profit Calendar** | Calculates daily P&L with monthly summaries |
| 🔍 **Smart Market Detection** | Detects market closures and deletes redundant files |

---

## Prerequisites

### Required
- ✅ A Google Account
- ✅ Access to Google Drive and Google Sheets
- ✅ Stable internet connection (for price fetching)

### Recommended
- 💻 Chrome browser for best experience
- 📱 Or Google Sheets mobile app

---

## Installation

### Step 1: Prepare Google Drive Folder

1. Open [Google Drive](https://drive.google.com/)
2. Create a new folder (e.g., `Portfolio Records`)
3. **Get the folder ID**:
   - Open the folder, look at the browser address bar
   - URL format: `https://drive.google.com/drive/folders/XXXXXXXXXXXXXX`
   - `XXXXXXXXXXXXXX` is your folder ID

### Step 2: Upload Template File

1. Download `template/模板.xlsx` from this repository
2. Upload it to your Google Drive folder
3. **Do not rename the template file** - keep it as `模板`

### Step 3: Set Up Apps Script

1. Open the uploaded `模板` file with Google Sheets
2. Click `Extensions` → `Apps Script`
3. Delete the default code in the editor
4. Copy all code from `src/code.gs` in this repository
5. Paste it into the Apps Script editor

### Step 4: Modify Configuration

Find the `CONFIG` block at the beginning of the code and modify:

```javascript
const CONFIG = {
  TARGET_FOLDER_ID: "xxxxxxxxxxx", // Replace with your folder ID
  // ... keep other settings as default
};
```

### Step 5: Save and Authorize

1. Click the 💾 Save button (or Ctrl+S)
2. Name your project (e.g., `Portfolio Automation`)
3. Refresh the Google Sheets page
4. Complete authorization when prompted

---

## How It Works

### Overall Flow

```
【One-Click Execute】Daily Update Flow
    │
    ├── 1. Create/Replace Daily File
    │      └── Copy template, name as "投资组合记录-YYYYMMDD"
    │
    ├── 2. Update Stock Prices
    │      ├── A-shares (SHA/SHE) → Sina Finance API
    │      └── Others → Google Finance
    │
    ├── 3. Wait for Formula Calculation (15s)
    │
    ├── 4. Market Closure Detection
    │      ├── No asset change → Delete file and exit
    │      └── Asset changed → Continue execution
    │
    ├── 5. Update Historical Trend Chart
    │      └── Incrementally append today's data point
    │
    └── 6. Update Monthly Profit Calendar
           └── Calculate daily P&L, update monthly summary
```

### Price Fetching Mechanism

| Exchange Code | Data Source | Example |
|--------------|-------------|---------|
| SHA (Shanghai) | Sina Finance | SHA:600519 |
| SHE (Shenzhen) | Sina Finance | SHE:000001 |
| Others | Google Finance | NASDAQ:AAPL |

### Data Storage Structure

Each daily file contains:
- **Sheet1** - Main portfolio table (total assets at H21, cost basis at I21)
- **资产图表-源数据** - Historical trend data (hidden sheet)

---

## Configuration

### CONFIG Options Explained

```javascript
const CONFIG = {
  // --- File and Folder Configuration ---
  TARGET_FOLDER_ID: "xxxxxxxxxxx",     // Google Drive folder ID
  TEMPLATE_NAME: "模板",                // Template file name
  DAILY_FILENAME_PREFIX: "投资组合记录-", // Daily file prefix

  // --- Monthly Summary Configuration ---
  MONTHLY_SUMMARY_START_CELL: "J1",    // Monthly summary table start position

  // --- Price Update Configuration ---
  TICKER_COLUMN: 2,                    // Stock code column (Column B = 2)
  PRICE_COLUMN: 4,                     // Current price column (Column D = 4)

  // --- Core Data Cell Configuration ---
  TREND_CHART_CELL_TO_FETCH: "H21",    // Total assets cell
  COST_BASIS_CELL_TO_FETCH: "I21",     // Cost basis cell

  // --- Historical Chart Configuration ---
  TREND_CHART_DATA_SHEET_NAME: "资产图表-源数据",
  TREND_CHART_DISPLAY_SHEET_NAME: "Sheet1",
  TREND_CHART_POSITION_CELL: "N1",     // Chart display position
  CHART_WIDTH: 480,                    // Chart width
  CHART_HEIGHT: 320,                   // Chart height

  // --- Retry Configuration ---
  MAX_RETRIES_FOR_FORMULA: 5,          // Max retries for formula calculation
  RETRY_INTERVAL_MS: 3000              // Retry interval (milliseconds)
};
```

---

## Usage

### Menu Usage

After installation, refresh the Google Sheets page. A new menu will appear:

**📈 投资组合自动化 (Portfolio Automation)**
- **【一键执行】每日更新全流程** - Execute complete daily update flow

### Daily Usage Flow

1. **After each trading day ends** (e.g., after 3:30 PM)
2. Open the template file in Google Sheets
3. Click `📈 投资组合自动化` → `【一键执行】每日更新全流程`
4. Wait for the script to complete
5. Check the newly generated `投资组合记录-YYYYMMDD` file

### Viewing Historical Data

- **Historical Trend Chart**: View in Sheet1 of each daily file
- **Monthly Profit Calendar**: View at position J1 in each daily file
- **Raw Trend Data**: In the hidden `资产图表-源数据` sheet

---

## Automation Setup

### Setting Up Daily Auto-Trigger

You can configure the script to run automatically every day:

1. In the Apps Script editor, click the ⏰ **Triggers** icon on the left
2. Click **+ Add Trigger** in the bottom right
3. Configure the following options:
   - **Choose which function to run**: `runDailyPortfolioUpdate`
   - **Select event source**: Time-driven
   - **Select type of time based trigger**: Day timer
   - **Select time of day**: 6 PM - 7 PM (recommended: after market close)
4. Click **Save**

### Important Notes

- Triggers may have a few minutes of time variance
- Ensure the account has access to all related files
- Recommend setting it 1-2 hours after market close

---

## FAQ

### Q: The menu is not showing?
**A**: Refresh the page. If still not showing, check if the Apps Script code was saved correctly. Or manually run the `onOpen` function in Apps Script.

### Q: Getting permission errors?
**A**: Re-run the script and follow the authorization steps again. Make sure to select the correct Google account.

### Q: Stock prices not updating?
**A**: 
- Check if the stock code format is correct (e.g., `SHA:600519`)
- Sina API may have access limits, try again later
- Google Finance may require VPN access in some regions

### Q: How to handle market holidays?
**A**: The script automatically detects market closures (no asset change) and deletes the daily file. No manual handling needed.

### Q: How to change the timezone?
**A**: In Apps Script, click ⚙️ **Project Settings**, modify the **Time zone** setting.

### Q: How to get the folder ID?
**A**: 
1. Open the target folder
2. Look at the browser address bar
3. URL format: `https://drive.google.com/drive/folders/XXXXXX`
4. The `XXXXXX` part is the folder ID

### Q: Can I track mutual funds?
**A**: Currently only supports assets that can be priced via Sina and Google Finance. Mutual fund tracking requires additional data source configuration.

---

## Need Help?

If you encounter issues while using this tool:
1. 📖 Check [GitHub Issues](../../issues) for similar problems
2. 🐛 Submit a new Issue describing your problem
3. 💬 Contributions and suggestions are welcome!

---

*Last updated: 2025*
