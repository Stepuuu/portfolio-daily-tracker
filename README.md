# Investment Portfolio Automation Tool / 投资组合自动化工具

[English](#english) | [中文](#中文)

---

## English

### 📊 Introduction

A Google Apps Script automation tool that helps you track and record daily investment portfolio changes. Built on Google Sheets and Google Drive, this tool provides:

- **Automated Daily Updates** - One-click to create daily portfolio snapshots
- **Hybrid Price Fetching** - Supports both A-shares (via Sina Finance API) and global stocks (via Google Finance)
- **Historical Trend Charts** - Automatically generates and updates asset trend visualizations
- **Monthly Profit Calendar** - Tracks daily profit/loss with monthly summaries
- **Smart Market Detection** - Automatically detects market closures and skips unnecessary updates

### 🚀 Quick Start

> **📌 Prefer a visual guide?**  
> If the instructions below seem overwhelming, download [**Automation_Mini_App_Guide_EN.docx**](Automation_Mini_App_Guide_EN.docx) first.  
> Need the original Chinese screenshot tutorial? Download [**自动化小程序使用说明--必读.docx**](自动化小程序使用说明--必读.docx).

#### Prerequisites

- A Google Account with access to Google Drive and Google Sheets
- Basic understanding of Google Apps Script

#### Installation

1. **Download the template**
   - Download `template/模板.xlsx` from this repository
   - Upload it to your Google Drive folder
   - Open it with Google Sheets

2. **Set up Apps Script**
   - In Google Sheets, go to `Extensions` → `Apps Script`
   - Copy the code from `src/code.gs` 
   - Paste it into the script editor
   - **Important**: Update `TARGET_FOLDER_ID` in the CONFIG section with your folder ID

3. **Configure and Run**
   - Save the script
   - Refresh the spreadsheet
   - Use the new `📈 投资组合自动化` menu

### 📁 Project Structure

```
├── README.md                           # This file (bilingual)
├── LICENSE                             # MIT License
├── .gitignore                          # Git ignore rules
├── Automation_Mini_App_Guide_EN.docx   # 📘 English translation of the Chinese docx guide
├── 自动化小程序使用说明--必读.docx        # 📸 Chinese visual guide with screenshots (download to view)
├── src/
│   └── code.gs                         # Main Apps Script source code
├── template/
│   ├── 模板.xlsx                        # Blank template
│   └── 投资组合记录-20250925.xlsx        # Example with sample data
└── docs/
    ├── Automation_Mini_App_Guide_EN.md # 📖 English translation of the Chinese docx guide
    ├── User_Guide_EN.md                # 📖 English user guide (click to view)
    └── User_Guide_CN.md                # 📖 中文使用指南（点击即可阅读）
```

### 🔧 Features

| Feature | Description |
|---------|-------------|
| 📸 Daily Snapshots | Creates dated copies of your portfolio |
| 📈 Price Updates | Fetches real-time prices from Sina (A-shares) and Google Finance (global) |
| 📊 Trend Charts | Visualizes asset changes and growth rates |
| 📅 Monthly Calendar | Calculates and displays daily P&L |
| 🔍 Market Detection | Skips updates on market holidays |

### ⚙️ Configuration

Key settings in `src/code.gs`:

```javascript
const CONFIG = {
  TARGET_FOLDER_ID: "your-folder-id",  // Your Google Drive folder ID
  TEMPLATE_NAME: "模板",                // Template file name
  DAILY_FILENAME_PREFIX: "投资组合记录-", // Daily file prefix
  // ... more settings
};
```

### 📖 Documentation

- [English User Guide](docs/User_Guide_EN.md)
- [中文使用指南](docs/User_Guide_CN.md)

### 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

### 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 中文

### 📊 简介

这是一个基于 Google Apps Script 的投资组合自动化工具，帮助你追踪和记录每日投资组合变化。基于 Google Sheets 和 Google Drive 构建，提供以下功能：

- **每日自动更新** - 一键创建每日投资组合快照
- **混合股价获取** - 同时支持 A 股（新浪财经接口）和境外股票（Google Finance）
- **历史趋势图表** - 自动生成并更新资产趋势可视化
- **月度收益日历** - 追踪每日盈亏并汇总月度数据
- **智能休市检测** - 自动检测休市日，跳过不必要的更新

### 🚀 快速开始

> **📌 懒得看文字说明？**  
> 直接下载 [**自动化小程序使用说明--必读.docx**](自动化小程序使用说明--必读.docx)，里面有**详细的图文教程**，跟着截图一步一步操作就行！

#### 前置条件

- 一个可以访问 Google Drive 和 Google Sheets 的 Google 账号
- 对 Google Apps Script 有基本了解（不了解也没关系，跟着 docx 里的图操作就行）

#### 安装步骤

1. **下载模板**
   - 从本仓库下载 `template/模板.xlsx`
   - 上传到你的 Google Drive 文件夹
   - 使用 Google Sheets 打开

2. **设置 Apps Script**
   - 在 Google Sheets 中，点击 `扩展程序` → `Apps Script`
   - 复制 `src/code.gs` 中的代码
   - 粘贴到脚本编辑器中
   - **重要**：修改 CONFIG 中的 `TARGET_FOLDER_ID` 为你的文件夹 ID

3. **配置并运行**
   - 保存脚本
   - 刷新电子表格
   - 使用新出现的 `📈 投资组合自动化` 菜单

### 📁 项目结构

```
├── README.md                           # 本文件（中英双语）
├── LICENSE                             # MIT 许可证
├── .gitignore                          # Git 忽略规则
├── Automation_Mini_App_Guide_EN.docx   # 📘 中文 docx 的英文翻译版（下载后查看）
├── 自动化小程序使用说明--必读.docx        # 📸 中文图文教程（下载后查看，新手推荐！）
├── src/
│   └── code.gs                         # 主要 Apps Script 源代码
├── template/
│   ├── 模板.xlsx                        # 空白模板
│   └── 投资组合记录-20250925.xlsx        # 带示例数据的模板
└── docs/
    ├── Automation_Mini_App_Guide_EN.md # 📖 中文 docx 的英文翻译版（点击即可阅读）
    ├── User_Guide_EN.md                # 📖 英文使用指南（点击即可阅读）
    └── User_Guide_CN.md                # 📖 中文使用指南（点击即可阅读）
```

### 🔧 功能特点

| 功能 | 说明 |
|------|------|
| 📸 每日快照 | 创建带日期的投资组合副本 |
| 📈 股价更新 | 从新浪（A股）和 Google Finance（境外）获取实时价格 |
| 📊 趋势图表 | 可视化资产变化和增长率 |
| 📅 月度日历 | 计算并展示每日盈亏 |
| 🔍 休市检测 | 在休市日跳过更新 |

### ⚙️ 配置说明

`src/code.gs` 中的关键设置：

```javascript
const CONFIG = {
  TARGET_FOLDER_ID: "your-folder-id",  // 你的 Google Drive 文件夹 ID
  TEMPLATE_NAME: "模板",                // 模板文件名
  DAILY_FILENAME_PREFIX: "投资组合记录-", // 每日文件前缀
  // ... 更多设置
};
```

### 📖 文档

- [English User Guide](docs/User_Guide_EN.md)
- [中文使用指南](docs/User_Guide_CN.md)

### 🤝 贡献

欢迎贡献代码！请随时提交 Pull Request。

### 📄 许可证

本项目采用 MIT 许可证 - 详情请参阅 [LICENSE](LICENSE) 文件。

---

## ⭐ Star This Repository

如果这个项目对你有帮助，请给个 Star ⭐！

If you find this project helpful, please give it a Star ⭐!

---

## 📬 Original Source

This project was originally shared via Google Drive. You can also access the original files here:
- [Google Drive Folder (原始 Google Drive 文件夹)](https://drive.google.com/drive/folders/1N-_xzrvTYMgO_pcRNl5jhJgFlPr6mq4l?usp=sharing)
