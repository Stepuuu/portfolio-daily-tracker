# Automation Mini App Guide (Must Read)

This document is an English translation of `自动化小程序使用说明--必读.docx`.

## Author

- Xiaohongshu ID: `stepccc`

## Tool Link

- https://drive.google.com/drive/folders/1N-_xzrvTYMgO_pcRNl5jhJgFlPr6mq4l?usp=sharing

## Setup Flow

1. Create your own folder in Google Drive.
2. Open the folder in the browser and copy its folder ID from the URL:
   - Example URL: `https://drive.google.com/drive/folders/xxxxxx`
   - The folder ID is the last `xxxxxx` part.
3. Open the provided folder `自动化投资记录分享-v0.1`, copy the template into your own folder, then edit it:
   - Column A: replace with your holdings' company names.
   - Column B: fill stock symbols/tickers.
   - Column C: fill share counts.
   - Update both pie chart ranges to your holdings range:
     - Label: `A2:Axx`
     - Value: `E2:Exx`
   - If you have two accounts, keep two sheets/charts like the template.
   - If you have one account, remove the second-account rows and chart.
   - Fill total holding cost in cell `I21` for daily profit calculation.
4. Important fixed read position:
   - Cell `H21` is used by the script as the total-assets read location for the daily asset-change chart.
   - Do not move it unless you also update the corresponding read location in code.
5. In Google Sheets, go to `Extensions` -> `Apps Script`.
6. In `code.gs`, fill `TARGET_FOLDER_ID` (line 6 in the original script) with your Drive folder ID from step 2.
7. Run once for initialization:
   - `onOpen` only adds the custom menu button.
   - Main function: `runDailyPortfolioUpdate`.
   - Select `runDailyPortfolioUpdate` and click Run.
8. After confirming it works, set trigger automation:
   - Go to `Triggers` in Apps Script.
   - Click `Add Trigger` (bottom-right).
   - Choose your preferred schedule and save.
9. Chart behavior note:
   - Asset change charts (bar/line) require at least two days of data.
   - Day 1 has no change chart; it appears from day 2 onward.

## Update Log (Translated)

1. `2025-09-25`: Wrote this guide, fixed a potential trigger failure bug, and added daily/monthly asset-change summary tables (starting at `J1` by default).
2. `2025-09-26`: Added non-trading-day logic. No file is generated if assets are unchanged from the previous day.
3. `2025-10-01`: Added cost-change logic. Enter today's cost in `M2` so daily P&L accounts for deposits/additional buys.
4. `2025-10-22`: Cost input moved to `I21`. Columns `J:K:L:M` are now monthly asset statistics, and generated charts start at `N1`.
