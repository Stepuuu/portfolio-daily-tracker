#!/usr/bin/env node
/**
 * 浏览器截图 MCP 服务器
 *
 * 功能：
 * 1. 截取指定 URL 的页面截图
 * 2. 获取页面 HTML 内容
 * 3. 检查页面元素
 *
 * 用于 AI Agent 辅助调试前端页面、截取持仓截图等场景。
 */

import puppeteer from 'puppeteer';
import { createServer } from 'http';
import { existsSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCREENSHOTS_DIR = process.env.SCREENSHOTS_DIR || join(__dirname, '../../data/screenshots');

// 确保截图目录存在
if (!existsSync(SCREENSHOTS_DIR)) {
  mkdirSync(SCREENSHOTS_DIR, { recursive: true });
}

let browser = null;

async function getBrowser() {
  if (!browser) {
    browser = await puppeteer.launch({
      headless: 'new',
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
  }
  return browser;
}

async function takeScreenshot(url, options = {}) {
  const browser = await getBrowser();
  const page = await browser.newPage();

  try {
    // 设置视口大小
    await page.setViewport({
      width: options.width || 1920,
      height: options.height || 1080
    });

    // 导航到页面
    await page.goto(url, {
      waitUntil: 'networkidle2',
      timeout: 30000
    });

    // 等待额外时间让页面渲染完成
    await new Promise(r => setTimeout(r, options.waitTime || 2000));

    // 生成文件名
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const filename = `screenshot_${timestamp}.png`;
    const filepath = join(SCREENSHOTS_DIR, filename);

    // 截图
    await page.screenshot({
      path: filepath,
      fullPage: options.fullPage || false
    });

    // 获取页面标题和一些基本信息
    const title = await page.title();
    const pageInfo = await page.evaluate(() => {
      return {
        title: document.title,
        url: window.location.href,
        bodyText: document.body?.innerText?.substring(0, 2000) || '',
        errors: window.__errors || []
      };
    });

    return {
      success: true,
      filepath,
      filename,
      title,
      url,
      pageInfo
    };
  } catch (error) {
    return {
      success: false,
      error: error.message,
      url
    };
  } finally {
    await page.close();
  }
}

async function getPageContent(url) {
  const browser = await getBrowser();
  const page = await browser.newPage();

  try {
    await page.goto(url, {
      waitUntil: 'networkidle2',
      timeout: 30000
    });

    await new Promise(r => setTimeout(r, 2000));

    const content = await page.evaluate(() => {
      const getElementInfo = (selector) => {
        const el = document.querySelector(selector);
        return el ? {
          exists: true,
          text: el.innerText?.substring(0, 500),
          visible: el.offsetParent !== null
        } : { exists: false };
      };

      return {
        title: document.title,
        url: window.location.href,
        mainContent: document.querySelector('main')?.innerText?.substring(0, 3000) ||
                     document.body?.innerText?.substring(0, 3000) || '',
        header: getElementInfo('header'),
        sidebar: getElementInfo('aside'),
        errors: Array.from(document.querySelectorAll('[class*="error"], [class*="Error"]'))
          .map(el => el.innerText).filter(t => t).slice(0, 5),
        consoleErrors: window.__consoleErrors || []
      };
    });

    return {
      success: true,
      content,
      url
    };
  } catch (error) {
    return {
      success: false,
      error: error.message,
      url
    };
  } finally {
    await page.close();
  }
}

// HTTP 服务器
const server = createServer(async (req, res) => {
  res.setHeader('Content-Type', 'application/json');

  const url = new URL(req.url, `http://${req.headers.host}`);
  const path = url.pathname;

  try {
    if (path === '/screenshot') {
      const targetUrl = url.searchParams.get('url') || 'http://localhost:3000';
      const result = await takeScreenshot(targetUrl, {
        width: parseInt(url.searchParams.get('width')) || 1920,
        height: parseInt(url.searchParams.get('height')) || 1080,
        fullPage: url.searchParams.get('fullPage') === 'true',
        waitTime: parseInt(url.searchParams.get('wait')) || 2000
      });
      res.end(JSON.stringify(result, null, 2));
    } else if (path === '/content') {
      const targetUrl = url.searchParams.get('url') || 'http://localhost:3000';
      const result = await getPageContent(targetUrl);
      res.end(JSON.stringify(result, null, 2));
    } else if (path === '/health') {
      res.end(JSON.stringify({ status: 'ok' }));
    } else {
      res.statusCode = 404;
      res.end(JSON.stringify({ error: 'Not found' }));
    }
  } catch (error) {
    res.statusCode = 500;
    res.end(JSON.stringify({ error: error.message }));
  }
});

const PORT = process.env.BROWSER_MCP_PORT || 9222;
server.listen(PORT, () => {
  console.log(`Browser MCP server running on http://localhost:${PORT}`);
  console.log('Endpoints:');
  console.log(`  GET /screenshot?url=<url>&width=<w>&height=<h>&fullPage=<bool>&wait=<ms>`);
  console.log(`  GET /content?url=<url>`);
  console.log(`  GET /health`);
});

// 优雅关闭
process.on('SIGINT', async () => {
  if (browser) {
    await browser.close();
  }
  process.exit();
});
