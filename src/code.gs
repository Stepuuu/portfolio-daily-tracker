// =================================================================
// 配置中心: 在这里修改所有关键参数
// =================================================================
const CONFIG = {
  // --- 文件与文件夹配置 ---
  TARGET_FOLDER_ID: "xxxxxxxxxxx", // 请替换为您的文件夹ID
  TEMPLATE_NAME: "模板",
  DAILY_FILENAME_PREFIX: "投资组合记录-",

  // --- 月度收益日历配置 ---
  MONTHLY_SUMMARY_START_CELL: "J1",

  // --- 股价更新配置 ---
  TICKER_COLUMN: 2,
  PRICE_COLUMN: 4,

  // --- 核心数据单元格配置 ---
  TREND_CHART_CELL_TO_FETCH: "H21", // 资产总和单元格
  COST_BASIS_CELL_TO_FETCH: "I21",   // 【主要】成本总和单元格 (新位置)
  OLD_COST_BASIS_CELL: "M2",      // 【备用】旧的成本总和单元格

  // --- 历史趋势图配置 ---
  TREND_CHART_DATA_SHEET_NAME: "资产图表-源数据",
  TREND_CHART_DISPLAY_SHEET_NAME: "Sheet1",
  TREND_CHART_POSITION_CELL: "N1",
  TREND_CHART_TITLE: "资产总和与变化率趋势",
  CHART_WIDTH: 480,
  CHART_HEIGHT: 320,
  // TREND_CHART_UPDATE_DAY: 1, // 不再需要每周更新逻辑
  
  // --- 等待和重试配置 ---
  MAX_RETRIES_FOR_FORMULA: 5,
  RETRY_INTERVAL_MS: 3000
};


// =================================================================
// 主菜单与主流程控制
// =================================================================

function onOpen() {
  SpreadsheetApp.getUi()
      .createMenu('📈 投资组合自动化')
      .addItem('【一键执行】每日更新全流程', 'runDailyPortfolioUpdate')
      .addToUi();
}

function runDailyPortfolioUpdate() {
  try {
    Logger.log('自动化流程开始。');
    
    const newDailyFile = createOrReplaceDailyFile_();
    if (!newDailyFile) { throw new Error("创建或查找当日文件失败。"); }
    
    const newDailySpreadsheet = SpreadsheetApp.open(newDailyFile);
    Logger.log(`成功创建今日文件: ${newDailyFile.getName()}`);
    
    updatePricesHybridInSpreadsheet_(newDailySpreadsheet);
    Logger.log('股价更新完成。');
    
    Logger.log(`等待表格内公式计算完成...`);
    Utilities.sleep(15000); 

    if (isMarketClosed_(newDailyFile)) {
      Logger.log("检测到资产总额与上一日相同，判断市场休市。");
      Logger.log(`正在删除今日文件: ${newDailyFile.getName()}`);
      newDailyFile.setTrashed(true);
      Logger.log("文件已删除，脚本执行终止。");
      return;
    }
    
    Logger.log("检测到资产总额变动，继续执行后续流程。");
    // 调用修改后的图表函数 (传入File对象)
    createHistoricalTrendChart_(newDailySpreadsheet, newDailyFile); 
    Logger.log('历史趋势图更新完成。'); 

    createMonthlySummary_(newDailySpreadsheet); 
    Logger.log('月度收益日历更新完成。');

    Logger.log(`成功！每日更新流程已全部完成。`);
  } catch (e) {
    Logger.log(`自动化流程发生严重错误: ${e.message}\n${e.stack}`);
  }
}

// =================================================================
// 股价更新模块
// =================================================================

function updatePricesHybridInSpreadsheet_(spreadsheet) {
  // ... (此函数代码保持不变，为节省篇幅已省略) ...
  const sheet = spreadsheet.getSheets()[0];
  const data = sheet.getDataRange().getValues();
  let sinaTickerMap = {}; 
  let googleFinanceMap = {}; 

  for (let i = 1; i < data.length; i++) {
    const originalTicker = data[i][CONFIG.TICKER_COLUMN - 1];
    if (originalTicker && typeof originalTicker === 'string' && originalTicker.includes(':')) {
      const exchange = originalTicker.split(':')[0].toUpperCase();
      const row = i + 1;
      if (exchange === 'SHA' || exchange === 'SHE') {
        const sinaTicker = convertToSinaFormat_(originalTicker);
        if (!sinaTickerMap[sinaTicker]) sinaTickerMap[sinaTicker] = [];
        sinaTickerMap[sinaTicker].push(row);
      } else {
        if (!googleFinanceMap[originalTicker]) googleFinanceMap[originalTicker] = [];
        googleFinanceMap[originalTicker].push(row);
      }
    }
  }

  const sinaTickers = Object.keys(sinaTickerMap);
  if (sinaTickers.length > 0) {
    const url = `https://hq.sinajs.cn/list=${sinaTickers.join(',')}`;
    try {
      const params = { 'muteHttpExceptions': true, 'headers': { 'Referer': 'https://finance.sina.com.cn' } };
      const response = UrlFetchApp.fetch(url, params).getContentText('GBK');
      const lines = response.split(';');
      lines.forEach(line => {
        if (line.trim() === '') return;
        const parts = line.split('=');
        if (parts.length < 2 || !parts[1] || parts[1].trim() === '""') return;
        const tickerPart = parts[0].split('_')[2];
        const dataStr = parts[1].replace(/"/g, '');
        const price = parseFloat(dataStr.split(',')[3]);
        if (!isNaN(price) && price > 0 && sinaTickerMap[tickerPart]) {
          sinaTickerMap[tickerPart].forEach(rowNum => {
            sheet.getRange(rowNum, CONFIG.PRICE_COLUMN).setValue(price);
          });
        }
      });
    } catch (e) {
      Logger.log(`新浪接口出错: ${e.message}`);
    }
  }

  const googleFinanceTickers = Object.keys(googleFinanceMap);
  if (googleFinanceTickers.length > 0) {
    const tempSs = SpreadsheetApp.create("TempPriceFetcher");
    const tempSheet = tempSs.getSheets()[0];
    googleFinanceTickers.forEach((ticker, index) => {
      const formula = `=IFERROR(GOOGLEFINANCE("${ticker}", "price"), IFERROR(GOOGLEFINANCE("${ticker}", "close", WORKDAY(TODAY()-1,-1)), "-"))`;
      tempSheet.getRange(index + 1, 1).setFormula(formula);
    });
    
    Utilities.sleep(10000);
    
    googleFinanceTickers.forEach((ticker, index) => {
      let price = tempSheet.getRange(index + 1, 1).getValue();
      if (typeof price !== 'number' || isNaN(price) || price <= 0) {
        price = "";
      }
      if (googleFinanceMap[ticker]) {
        googleFinanceMap[ticker].forEach(rowNum => {
          sheet.getRange(rowNum, CONFIG.PRICE_COLUMN).setValue(price);
        });
      }
    });
    
    DriveApp.getFileById(tempSs.getId()).setTrashed(true);
  }
}


// =================================================================
// 文件和图表生成模块
// =================================================================

function createOrReplaceDailyFile_() {
  const folder = DriveApp.getFolderById(CONFIG.TARGET_FOLDER_ID);
  const templates = folder.getFilesByName(CONFIG.TEMPLATE_NAME);
  if (!templates.hasNext()) { throw new Error(`在文件夹中找不到名为 "${CONFIG.TEMPLATE_NAME}" 的模板文件。`); }
  const templateFile = templates.next();
  const today = new Date();
  const dateString = Utilities.formatDate(today, Session.getScriptTimeZone(), "yyyyMMdd");
  const newFileName = CONFIG.DAILY_FILENAME_PREFIX + dateString;
  const existingFiles = folder.getFilesByName(newFileName);
  while (existingFiles.hasNext()) {
    existingFiles.next().setTrashed(true);
  }
  const newFile = templateFile.makeCopy(newFileName, folder);
  return newFile;
}

function createMonthlySummary_(newDailySpreadsheet) {
  // ... (此函数代码保持不变，为节省篇幅已省略，其成本逻辑已符合要求) ...
    const todaySheet = newDailySpreadsheet.getSheetByName(CONFIG.TREND_CHART_DISPLAY_SHEET_NAME);
  if (!todaySheet) { 
      Logger.log(`错误：在今日文件中找不到名为 "${CONFIG.TREND_CHART_DISPLAY_SHEET_NAME}" 的工作表。`);
      return; 
  }
  const todayFile = DriveApp.getFileById(newDailySpreadsheet.getId());

  const currentValues = readPortfolioValuesFromFile_(todayFile);
  if (!currentValues) {
    Logger.log("无法读取今日文件的资产和成本数据，无法更新月度总结。");
    return;
  }
  const todayDate = new Date(); 

  const previousFileData = findPreviousDayData_(todayFile);
  const previousValues = previousFileData.values;
  const previousFile = previousFileData.file;
  const isFirstRecordOfMonth = previousFileData.isFirstOfMonth;

  let tableData = []; 
  let header = [["日期", "当日净资产", "当日盈亏", "当日成本"]]; 

  if (!isFirstRecordOfMonth && previousFile) {
    try {
      const prevSS = SpreadsheetApp.open(previousFile);
      const prevSheet = prevSS.getSheetByName(CONFIG.TREND_CHART_DISPLAY_SHEET_NAME);
      if (prevSheet) {
        const startCell = prevSheet.getRange(CONFIG.MONTHLY_SUMMARY_START_CELL);
        const startRow = startCell.getRow();
        const startCol = startCell.getColumn();
        
        const lastRow = findLastDataRow_(prevSheet, startCol, startRow); 
        
        if (lastRow >= startRow + 1) { 
          const prevDataRange = prevSheet.getRange(startRow + 1, startCol, lastRow - startRow, 4); 
          tableData = prevDataRange.getValues();
           Logger.log(`成功从 ${previousFile.getName()} 复制了 ${tableData.length} 行历史数据。`);
        } else {
             Logger.log(`昨天文件 ${previousFile.getName()} 的月度总结表似乎只有表头，不复制数据。`);
        }
      } else {
          Logger.log(`警告：在昨天文件 ${previousFile.getName()} 中找不到 Sheet1。`);
      }
    } catch (e) {
      Logger.log(`打开或读取昨天文件 ${previousFile.getName()} 时出错: ${e.message}`);
    }
  } else {
       Logger.log("今天是本月第一个记录日，或未找到有效的上一个文件，将只创建今天的记录。");
  }

  let dailyProfit = 0;
  if (previousValues && previousValues.assets > 0) { 
    const assetChange = currentValues.assets - previousValues.assets;
    let costChange = 0; 
    if (previousValues.cost > 0) { 
      costChange = currentValues.cost - previousValues.cost;
    }
    dailyProfit = assetChange - costChange;
  } else {
      dailyProfit = currentValues.assets - currentValues.cost; 
      Logger.log(`计算首个记录日(${Utilities.formatDate(todayDate, Session.getScriptTimeZone(), 'yyyy-MM-dd')})盈亏: ${currentValues.assets} - ${currentValues.cost} = ${dailyProfit}`);
  }

  tableData.push([todayDate, currentValues.assets, dailyProfit, currentValues.cost]);

  let totalProfit = 0;
  tableData.forEach(row => {
    if (row && typeof row[2] === 'number' && !isNaN(row[2])) {
       totalProfit += row[2];
    }
  });


  const startCellToday = todaySheet.getRange(CONFIG.MONTHLY_SUMMARY_START_CELL);
  const startRowToday = startCellToday.getRow();
  const startColToday = startCellToday.getColumn();

  todaySheet.getRange(startRowToday, startColToday, todaySheet.getMaxRows() - startRowToday + 1, 4).clearContent();

  const headerRange = todaySheet.getRange(startRowToday, startColToday, 1, 4);
  headerRange.setValues(header);
  
  if (tableData.length > 0) {
    const dataRange = todaySheet.getRange(startRowToday + 1, startColToday, tableData.length, 4);
    dataRange.setValues(tableData);
    
    todaySheet.getRange(startRowToday + 1, startColToday, tableData.length, 1).setNumberFormat('yyyy-mm-dd');        
    todaySheet.getRange(startRowToday + 1, startColToday + 1, tableData.length, 1).setNumberFormat('#,##0.00'); 
    todaySheet.getRange(startRowToday + 1, startColToday + 2, tableData.length, 1).setNumberFormat('0.00');        
    todaySheet.getRange(startRowToday + 1, startColToday + 3, tableData.length, 1).setNumberFormat('#,##0.00'); 
  }

  const totalRowIndex = startRowToday + tableData.length + 1;
  const totalRange = todaySheet.getRange(totalRowIndex, startColToday, 1, 4);
  totalRange.setValues([["本月总计", "", totalProfit, ""]]); 
  todaySheet.getRange(totalRowIndex, startColToday + 2).setNumberFormat('0.00');

  const tableRange = todaySheet.getRange(startRowToday, startColToday, tableData.length + 2, 4);
  const headerAndTotalColor = "#4a86e8";
  
  headerRange.setBackground(headerAndTotalColor).setFontColor("white").setFontWeight("bold");
  totalRange.setBackground(headerAndTotalColor).setFontColor("white").setFontWeight("bold");
  
  tableRange.setBorder(true, true, true, true, true, true, "#a9c4f5", SpreadsheetApp.BorderStyle.SOLID_MEDIUM);

  if (tableData.length > 0) {
     const dataBodyRange = todaySheet.getRange(startRowToday + 1, startColToday, tableData.length, 4);
     dataBodyRange.applyRowBanding(SpreadsheetApp.BandingTheme.LIGHT_GREY, false, false);
  }
  
  todaySheet.setColumnWidth(startColToday, 100);
  todaySheet.setColumnWidth(startColToday + 1, 100);
  todaySheet.setColumnWidth(startColToday + 2, 100);
  todaySheet.setColumnWidth(startColToday + 3, 100);
}

/**
 * 【重写】增量更新历史趋势图数据并重绘图表
 * @param {Spreadsheet} newDailySpreadsheet - 当天新生成的电子表格对象
 * @param {File} newDailyFile - 当天新生成的File对象
 */
function createHistoricalTrendChart_(newDailySpreadsheet, newDailyFile) {
  Logger.log("开始处理历史趋势图...");
  const todaySheet = newDailySpreadsheet.getSheetByName(CONFIG.TREND_CHART_DISPLAY_SHEET_NAME);
  if (!todaySheet) { 
      Logger.log(`错误：在今日文件中找不到名为 "${CONFIG.TREND_CHART_DISPLAY_SHEET_NAME}" 的工作表。`);
      return; 
  }

  // 1. 准备今天的数据源工作表
  let todayDataSourceSheet = newDailySpreadsheet.getSheetByName(CONFIG.TREND_CHART_DATA_SHEET_NAME);
  if (todayDataSourceSheet) { 
      todayDataSourceSheet.clearContents(); // 清空内容，不清格式
  } else { 
      todayDataSourceSheet = newDailySpreadsheet.insertSheet(CONFIG.TREND_CHART_DATA_SHEET_NAME); 
      todayDataSourceSheet.hideSheet();
  }
  todayDataSourceSheet.appendRow(["日期", "资产总和", "变化率"]); // 写入表头

  // 2. 查找前一天的文件
  const previousFileData = findPreviousDayData_(newDailyFile);
  const previousFile = previousFileData.file;
  let previousDataValues = []; // 存储从昨天复制的数据 (包含表头)

  // 3. 如果存在前一天文件，复制其数据源工作表内容
  if (previousFile) {
      try {
          const prevSs = SpreadsheetApp.open(previousFile);
          const prevDataSourceSheet = prevSs.getSheetByName(CONFIG.TREND_CHART_DATA_SHEET_NAME);
          if (prevDataSourceSheet) {
              const prevDataRange = prevDataSourceSheet.getDataRange();
              previousDataValues = prevDataRange.getValues();
              if (previousDataValues.length > 1) { // 确保有数据（除了表头）
                  // 写入除表头外的数据到今天的表
                  todayDataSourceSheet.getRange(2, 1, previousDataValues.length - 1, previousDataValues[0].length)
                                     .setValues(previousDataValues.slice(1));
                  Logger.log(`已从 ${previousFile.getName()} 复制 ${previousDataValues.length - 1} 行图表数据。`);
              } else {
                  Logger.log(`前一日文件 ${previousFile.getName()} 的图表数据源为空或只有表头。`);
              }
          } else {
              Logger.log(`警告：在 ${previousFile.getName()} 中未找到 "${CONFIG.TREND_CHART_DATA_SHEET_NAME}" 工作表。`);
          }
      } catch (e) {
          Logger.log(`从昨天文件 ${previousFile.getName()} 复制图表数据时出错: ${e.message}`);
      }
  } else {
      Logger.log("未找到前一日文件，将只添加今天的数据点。");
  }

  // 4. 读取今天的数据
  const todayValues = readPortfolioValuesFromFile_(newDailyFile);
  if (!todayValues) {
      Logger.log("无法读取今天文件的资产数据，无法添加今日图表数据点。");
      // 即使今天数据读取失败，如果昨天有数据，仍然尝试绘制昨天的图表
  } else {
      const todayDateStr = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyy-MM-dd");
      const currentValue = todayValues.assets;
      let previousValue = null;

      // 从已复制的数据或上一个文件记录中获取前一天的资产值
      if (previousDataValues.length > 1) {
          const lastCopiedRow = previousDataValues[previousDataValues.length - 1];
          if (typeof lastCopiedRow[1] === 'number' && !isNaN(lastCopiedRow[1])) {
              previousValue = lastCopiedRow[1];
          }
      } else if (previousFileData.values && previousFileData.values.assets > 0) {
          // 如果复制失败但找到了上一个文件的值 (例如非常规情况或月初)
          previousValue = previousFileData.values.assets;
      }
      
      let changeRate = 0;
      if (previousValue !== null && previousValue !== 0) { 
          changeRate = (currentValue - previousValue) / previousValue; 
      }
      
      // 5. 追加今天的数据行
      todayDataSourceSheet.appendRow([todayDateStr, currentValue, changeRate]);
      Logger.log(`已追加今天的图表数据点: [${todayDateStr}, ${currentValue}, ${changeRate}]`);
  }

  // 6. 清理旧图表并绘制新图表
  const existingCharts = todaySheet.getCharts();
  existingCharts.forEach(chart => {
    if (chart.getOptions().get('title') === CONFIG.TREND_CHART_TITLE) {
      todaySheet.removeChart(chart);
    }
  });

  const dataRange = todayDataSourceSheet.getDataRange();
  if (dataRange.getNumRows() <= 1) { 
      Logger.log("图表数据点不足（仅表头），无法生成图表。");
      return; 
  }

  const anchorCell = todaySheet.getRange(CONFIG.TREND_CHART_POSITION_CELL);
  const chartBuilder = todaySheet.newChart()
    .asComboChart().addRange(dataRange).setNumHeaders(1)
    .setPosition(anchorCell.getRow(), anchorCell.getColumn(), 0, 0)
    .setOption('title', CONFIG.TREND_CHART_TITLE)
    .setOption('width', CONFIG.CHART_WIDTH)
    .setOption('height', CONFIG.CHART_HEIGHT)
    .setOption('colors', ['#4285F4', '#EA4335'])
    .setOption('legend', { position: 'top' })
    .setOption('series', {
      0: { type: 'bars', targetAxisIndex: 0 },
      1: { type: 'line', targetAxisIndex: 1 }
    })
    .setOption('vAxes', {
      0: { title: '资产总和', format: '#,##0' },
      1: { title: '变化率', format: '#0.0%' }
    })
    .setOption('hAxis', { showTextEvery: 2 }); // 控制横轴标签显示密度
  todaySheet.insertChart(chartBuilder.build());
  Logger.log("历史趋势图已更新并绘制。");
}


// =================================================================
// 辅助函数
// =================================================================

/**
 * 【辅助函数】查找上一个记录日的文件、数据及是否为月初第一个记录
 */
function findPreviousDayData_(todayFile) {
  const folder = DriveApp.getFolderById(CONFIG.TARGET_FOLDER_ID);
  const filesIterator = folder.getFilesByType(MimeType.GOOGLE_SHEETS);
  let allFiles = [];
  while (filesIterator.hasNext()) {
    const file = filesIterator.next();
    if (file.getName().startsWith(CONFIG.DAILY_FILENAME_PREFIX)) {
      allFiles.push(file);
    }
  }
  allFiles.sort((a, b) => a.getName().localeCompare(b.getName()));

  let previousFile = null;
  let previousValues = { assets: 0, cost: 0 };
  let isFirstOfMonth = true; 

  const todayFileName = todayFile.getName();
  let todayIndex = -1;
  for(let i = 0; i < allFiles.length; i++){
      if(allFiles[i].getName() === todayFileName){
          todayIndex = i;
          break;
      }
  }

  if (todayIndex > 0) { 
      previousFile = allFiles[todayIndex - 1];
      const prevValuesResult = readPortfolioValuesFromFile_(previousFile);
      if (prevValuesResult) {
          previousValues = prevValuesResult;
      } else {
          Logger.log(`警告：无法读取上一个文件 ${previousFile.getName()} 的数据，将使用 0 作为基准。`);
      }

      const todayMonthStr = todayFileName.substring(CONFIG.DAILY_FILENAME_PREFIX.length, CONFIG.DAILY_FILENAME_PREFIX.length + 6); 
      const prevMonthStr = previousFile.getName().substring(CONFIG.DAILY_FILENAME_PREFIX.length, CONFIG.DAILY_FILENAME_PREFIX.length + 6); 
      if (todayMonthStr === prevMonthStr) {
          isFirstOfMonth = false; 
      } else {
          Logger.log(`检测到月份变更，今天是 ${todayMonthStr} 的第一个记录日，上一个记录在 ${prevMonthStr}。`);
      }
  } else if (todayIndex === 0) { 
      Logger.log("这是系统中的第一个记录文件。");
  } else {
       Logger.log("错误：在文件列表中未找到今日文件？");
  }

  return { file: previousFile, values: previousValues, isFirstOfMonth: isFirstOfMonth };
}


/**
 * 【辅助函数】在指定工作表中查找月度总结表格数据区域的最后一行行号
 */
function findLastDataRow_(sheet, startCol, startRow) {
    if (!sheet || sheet.getLastRow() < startRow) {
        return startRow; 
    }
    const checkRange = sheet.getRange(startRow + 1, startCol, sheet.getLastRow() - startRow, 1); 
    const columnValues = checkRange.getValues();
    let lastDataRowIndex = -1; 
    
    for (let i = 0; i < columnValues.length; i++) {
        if (columnValues[i][0] === "" || columnValues[i][0] === "本月总计") {
            lastDataRowIndex = i - 1; 
            break;
        }
    }

    if (lastDataRowIndex === -1 && columnValues.length > 0 && columnValues[columnValues.length -1][0] !== "") {
        lastDataRowIndex = columnValues.length - 1;
    }
    
    return (lastDataRowIndex >= 0) ? (startRow + 1 + lastDataRowIndex) : startRow;
}


/**
 * 【辅助函数】通过比较今日和昨日资产总和，判断市场是否休市
 */
function isMarketClosed_(todayFile) {
  const previousFileData = findPreviousDayData_(todayFile); 
  const previousFile = previousFileData.file;
  const previousValues = previousFileData.values;
  
  if (!previousFile) { 
    Logger.log("未找到上一个文件，无法进行休市检查，默认市场开市。");
    return false; 
  }

  const todayValues = readPortfolioValuesFromFile_(todayFile);
  
  if (!todayValues || !previousValues || previousValues.assets === 0) { 
     Logger.log("读取今日或昨日数据失败，或昨日数据为初始值，无法进行休市检查，默认市场开市。");
     return false; 
  }

  const todayAssets = todayValues.assets;
  const yesterdayAssets = previousValues.assets;

  Logger.log(`开始休市检查：今日资产 = ${todayAssets}, 昨日资产 = ${yesterdayAssets}`);

  if (Math.abs(todayAssets - yesterdayAssets) < 0.01) {
    return true; 
  }
  return false; 
}

/**
 * 【辅助函数】从文件中读取资产和成本总和, 优先I21，失败则回退M2
 */
function readPortfolioValuesFromFile_(file) {
  if (!file) {
       Logger.log("readPortfolioValuesFromFile_ 接收到无效的文件对象。");
      return null;
  }
  try {
    const ss = SpreadsheetApp.open(file);
    const sheet = ss.getSheets()[0];
    let assetsValue = null;
    let costValue = null; 
    let retries = 0;

    // --- 1. 读取资产值 (H21) ---
    retries = 0;
    while (retries < CONFIG.MAX_RETRIES_FOR_FORMULA) {
      const val = sheet.getRange(CONFIG.TREND_CHART_CELL_TO_FETCH).getValue();
      if (typeof val === 'number' && !isNaN(val)) {
        assetsValue = val;
        break; 
      }
      Logger.log(`读取资产: 文件 ${file.getName()}, 单元格 ${CONFIG.TREND_CHART_CELL_TO_FETCH}, 值无效 (${val}), 等待重试...`);
      Utilities.sleep(CONFIG.RETRY_INTERVAL_MS);
      retries++;
    }

    if (assetsValue === null) {
      Logger.log(`未能从文件 ${file.getName()} 的 ${CONFIG.TREND_CHART_CELL_TO_FETCH} 获取有效资产值。`);
      return null;
    }

    // --- 2. 尝试读取成本值 (I21 - 新位置) ---
    retries = 0; 
    while (retries < CONFIG.MAX_RETRIES_FOR_FORMULA) {
       try {
            const val_i21 = sheet.getRange(CONFIG.COST_BASIS_CELL_TO_FETCH).getValue();
            if (typeof val_i21 === 'number' && !isNaN(val_i21)) { 
                costValue = val_i21;
                break; 
            }
             Logger.log(`读取新成本: 文件 ${file.getName()}, 单元格 ${CONFIG.COST_BASIS_CELL_TO_FETCH}, 值无效 (${val_i21}), 等待重试...`);
       } catch (e) {
            Logger.log(`读取新成本单元格 ${CONFIG.COST_BASIS_CELL_TO_FETCH} 出错 (可能不存在): ${e.message}`);
            break;
       }
      Utilities.sleep(CONFIG.RETRY_INTERVAL_MS);
      retries++;
    }

    // --- 3. 如果I21失败，尝试读取旧成本值 (M2) ---
    if (costValue === null) {
        Logger.log(`未能从 ${CONFIG.COST_BASIS_CELL_TO_FETCH} 读取成本, 尝试旧位置 ${CONFIG.OLD_COST_BASIS_CELL}...`);
        try { 
            const val_m2 = sheet.getRange(CONFIG.OLD_COST_BASIS_CELL).getValue();
            if (typeof val_m2 === 'number' && !isNaN(val_m2)) {
                costValue = val_m2;
                Logger.log(`成功从旧位置 ${CONFIG.OLD_COST_BASIS_CELL} 读取成本: ${costValue}`);
            } else {
                 Logger.log(`旧位置 ${CONFIG.OLD_COST_BASIS_CELL} 也未能读取有效成本值 (${val_m2})。`);
            }
        } catch (rangeError) {
             Logger.log(`尝试读取旧位置 ${CONFIG.OLD_COST_BASIS_CELL} 时出错 (可能单元格不存在): ${rangeError.message}`);
        }
    }

    // --- 4. 如果都失败，默认成本为0 ---
    if (costValue === null) {
      Logger.log(`新旧位置均无有效成本值，文件 ${file.getName()} 的成本将记为 0。`);
      costValue = 0;
    }

    return { assets: assetsValue, cost: costValue };

  } catch(e) {
    Logger.log(`打开或读取文件 ${file.getName()} 时发生错误: ${e.message}`);
    return null;
  }
}

function convertToSinaFormat_(googleTicker) {
  const parts = googleTicker.split(':');
  if (parts.length < 2) return null;
  const exchange = parts[0].toUpperCase();
  const code = parts[1];
  if (exchange === 'SHA') return `sh${code}`;
  if (exchange === 'SHE') return `sz${code}`;
  return null; 
}