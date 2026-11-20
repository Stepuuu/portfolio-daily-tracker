# Feishu workbench / 飞书工作台

Introduced in **v3.2.0**; capital-flow entries added in **v3.2.1**. Use native cards in a bot's private chat to record daily portfolio changes, inspect assets, and run stock research. Setup is required: installing the repository or ClawHub skill does **not** automatically install an event handler, publish a bot menu, or validate your application's callbacks.

本功能自 **v3.2.0** 提供，**v3.2.1** 增加资金与本金变动：通过机器人私聊中的原生卡片完成每日组合记录、资产查看和股票研究。需要先配置本机服务与飞书应用；安装仓库或 ClawHub 技能**不会自动接入事件、发布菜单或完成当前应用的回调验收**。

This messaging integration currently uses an existing OpenClaw Feishu connection.
The portfolio engine, web dashboard and stock research workbench run independently
of OpenClaw; using environment variables for credentials does not replace the
bridge's event connection.

当前消息接入复用已有 OpenClaw 飞书连接。组合引擎、网页与股票研究工作台可独立运行；
用环境变量提供凭证，也仍需配置桥接所用的事件连接。

**Since 4.0.0** the web app keeps holdings only in the confirmed ledger. Once a
ledger is active the card's daily update records into it: every item becomes
ledger events, the preview is the ledger's own proposal and confirming on the
card books it (see [With the transaction ledger](#with-the-transaction-ledger--启用交易账本后)).
Without a ledger the card edits the legacy dated holdings files as before. Price
alerts and the briefs are not pushed to Feishu; they appear in the web app's
notification bell. The daily report made by the automatic close snapshot, or
after a card entry, is pushed when `feishu_chat_id` is configured.

**4.0.0 起**网页的持仓只来自已确认的账本。账本启用后，卡片的「每日更新」直接记入账本：每一项变成账本记录，
预览就是账本自己的入账预览，在卡片上确认即入账（见[启用交易账本后](#with-the-transaction-ledger--启用交易账本后)）；
没有账本时仍修改旧版按日期保存的持仓文件。价格提醒和盘前盘后简报不推送到飞书，只在网页的铃铛里；收盘自动快照或卡片入账后生成的日报，
在配置了 `feishu_chat_id` 时推送。

## Architecture / 接入方式

```mermaid
flowchart LR
    A[Feishu private chat / 飞书私聊] --> B[Existing OpenClaw Feishu connection]
    B --> C[JS event bridge]
    C --> D[Loopback companion :18200]
    D --> E[Local ledger or holdings files]
    D --> F[Local research API :8000]
    D --> G[Feishu message API]
    G --> A
```

The [bridge module](../openclaw/feishu-workbench-bridge.mjs) reuses the Feishu plugin's existing authenticated connection. The [companion](../dashboard/feishu_workbench/) accepts authenticated loopback requests, stores commands before executing them, and sends or updates Card 2.0 messages through the same bot application. It does not require a public webhook or another WebSocket consumer.

[JS 桥接模块](../openclaw/feishu-workbench-bridge.mjs)复用 Feishu 插件已有的认证连接。[本机伴随服务](../dashboard/feishu_workbench/)只接收带令牌的 loopback 请求，将操作持久化后执行，并通过同一机器人应用发送或更新 Card 2.0。无需额外公网回调地址，也无需再启动一个长连接消费者。

## 1. Prepare the local services / 准备本机服务

Use Linux, Python 3.10+, and the project's Node.js 22.12+ toolchain. The process manager needs Linux `pidfd` support, supplied by Python or libc. OpenClaw's Feishu connection and the companion must share access to the private bridge token, normally under the same operating-system user. No Docker setup is required.

使用 Linux、Python 3.10+ 和项目要求的 Node.js 22.12+。启动管理器需要 Linux `pidfd` 支持，可由 Python 或 libc 提供。OpenClaw 的 Feishu 连接与伴随服务需要读取同一份私有桥接令牌，通常应由同一系统用户运行。无需配置 Docker。

Run these commands from the repository root. / 以下命令均在仓库根目录执行。

```bash
python3 -m venv .venv
source .venv/bin/activate
make setup
make lab
```

If the environment is already installed, reuse it. Keep the research API available at `http://127.0.0.1:8000`; use your actual loopback port if different. `make lab` supports research without portfolio setup. Daily updates require a confirmed ledger (the setup wizard's opening balance, see [LEDGER.md](LEDGER.md)) or, without one, an existing dated portfolio described in the [full guide](FULL_GUIDE_CN.md); this installer does not create or import personal holdings. Start the companion with the same `PORTFOLIO_DIR` as the backend (or leave it unset in both): the companion uses `PORTFOLIO_DIR`, else `QR_PORTFOLIO_DIR`, else the project's own `portfolio` directory, made absolute once, for everything (the ledger, the holdings files it edits only while no ledger is active there, the lock, snapshots and its pipeline).

已经安装时复用现有环境即可。研究 API 应运行在 `http://127.0.0.1:8000`；若使用其他本机端口，在下方配置中相应修改。`make lab` 可独立运行研究；每日更新需要已确认期初的交易账本（设置向导，见 [LEDGER.md](LEDGER.md)），没有账本时需要按[完整指南](FULL_GUIDE_CN.md)初始化已有日期持仓，工作台安装器不会创建或导入个人持仓。伴随服务与后端的 `PORTFOLIO_DIR` 必须相同（或都不设置）：伴随服务按 `PORTFOLIO_DIR`、`QR_PORTFOLIO_DIR`、项目自带的 `portfolio` 目录的顺序选定一个目录，转成绝对路径后，账本、旧持仓文件（只在该目录没有启用账本时修改）、写锁、快照和日报流程都只用这一个目录。

### Initialize private configuration / 初始化私有配置

Obtain your own `open_id` for **this bot application**. The allowlist is mandatory. Its members can access the same portfolio and research workspace; it is not account-level or tenant-level isolation.

先取得**当前机器人应用下**自己的 `open_id`。必须设置明确白名单；白名单成员共享同一套组合和研究工作区，并不按账户或租户隔离。

```bash
read -r -p "Owner open_id: " QR_OWNER_OPEN_ID
export QR_OWNER_OPEN_ID

PYTHONPATH=dashboard python -m feishu_workbench init \
  --project-dir . \
  --allowed-user "$QR_OWNER_OPEN_ID" \
  --lab-url http://127.0.0.1:8000 \
  --account-id qr
```

`qr` is the OpenClaw **connection account name**, not a portfolio account or a Feishu App ID. Change it consistently if your connection uses another name. Repeat `--allowed-user` only for people who should have access to this entire workspace.

`qr` 是 OpenClaw 的**连接账户名**，不是投资账户名或飞书 App ID。已有连接使用其他名字时，需要同步修改所有相关配置。只有需要访问整套工作区的人才应加入 `--allowed-user`。

The initializer creates `dashboard/data/feishu-workbench/config.json` and `bridge.token` with mode `0600`, inside a `0700` directory. It generates a random token, records the resolved runtime paths, and refuses to overwrite an existing state directory. Keep these files private and untracked. A short token or a token/config file readable by other users is rejected.

初始化器会在 `0700` 目录下创建 `0600` 权限的 `dashboard/data/feishu-workbench/config.json` 和 `bridge.token`，生成随机令牌并写入解析后的运行路径。已有目录不会被覆盖。这些文件应保留在本机，不加入版本控制；过短的令牌或其他用户可读的令牌、配置文件会被拒绝。

Choose one credential source for the companion. / 为伴随服务选择一种凭证来源。

- **Existing OpenClaw configuration:** when initializing, append `--openclaw-config "$OPENCLAW_CONFIG_FILE"`, after setting that variable to your private config file. The selected entry must contain `channels.feishu.accounts.<account-id>.appId` and `appSecret`. The file remains local; secrets are not copied into the workbench config.
- **Environment variables:** if `--openclaw-config` is omitted, the companion reads `FEISHU_APP_ID` and `FEISHU_APP_SECRET`. They must identify the same bot used by the bridge. Set them before starting the companion, for example with the prompts below.
- **复用 OpenClaw 配置：**首次初始化时追加 `--openclaw-config "$OPENCLAW_CONFIG_FILE"`，变量指向本机私有配置。所选条目应包含 `channels.feishu.accounts.<account-id>.appId` 和 `appSecret`，凭证不会复制进工作台配置。
- **环境变量：**省略该参数时，伴随服务读取 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`，必须与桥接连接使用同一机器人。启动服务前可使用以下交互输入，避免把密钥写进命令文本。

```bash
read -r -p "Feishu App ID: " FEISHU_APP_ID
read -r -s -p "Feishu App Secret: " FEISHU_APP_SECRET
export FEISHU_APP_ID FEISHU_APP_SECRET
```

For Lark, use `domain: "lark"` in the private workbench config when using environment credentials, or the domain configured in the selected OpenClaw account. Only the official Feishu and Lark API domains are supported.

使用 Lark 时，环境变量凭证模式需在私有工作台配置中设置 `domain: "lark"`；复用 OpenClaw 时读取所选连接的域名设置。仅支持飞书与 Lark 官方 API 域名。

### Start, inspect, stop / 启动、查看、停止

```bash
PYTHONPATH=dashboard python -m feishu_workbench.control start \
  --config dashboard/data/feishu-workbench/config.json

PYTHONPATH=dashboard python -m feishu_workbench.control status \
  --config dashboard/data/feishu-workbench/config.json

# Stop when no workbench operation is running / 无工作台操作执行时停止
PYTHONPATH=dashboard python -m feishu_workbench.control stop \
  --config dashboard/data/feishu-workbench/config.json
```

Start again after stopping when continuing setup. The companion binds `127.0.0.1:18200`; an occupied port is an error. The manager identifies its own process and will not replace an unrelated service. It refuses a managed stop while operations are running. Starting the companion does not enable startup at boot or start the research API.

继续配置时需要重新执行 `start`。伴随服务监听 `127.0.0.1:18200`，端口被占用时会报错。管理器核实自身进程，不会替换其他服务；操作执行中会拒绝停止。启动伴随服务不会设置开机自启，也不会代为启动研究 API。

## 2. Connect the existing Feishu handler / 接入现有 Feishu 处理器

The bridge exports `dispatchWorkbenchEvent`; it is not an automatically registered OpenClaw plugin. Integrate it with the existing Feishu plugin's event dispatcher for the configured account. The exact integration file depends on your installed plugin version. Preserve existing handlers, especially other applications' card interactions.

桥接模块导出 `dispatchWorkbenchEvent`，它不是会自动注册的 OpenClaw 插件。需要将它接入所用 Feishu 插件已有的事件分发器，具体文件位置取决于插件版本。保留原有处理器，尤其是其他卡片交互。

Set these variables in the environment of the **OpenClaw process**, not only the companion shell. The paths below are derived from the repository root. Do not place the token itself in plugin source or logs.

以下环境变量必须由 **OpenClaw 进程**读取，不能仅设置在伴随服务的终端里。路径从仓库根目录生成；不要将令牌内容写入插件源码或日志。

```bash
export QR_WORKBENCH_ACCOUNT_ID=qr
export QR_WORKBENCH_BRIDGE_URL=http://127.0.0.1:18200/bridge/event
export QR_WORKBENCH_BRIDGE_MODULE="$(pwd)/openclaw/feishu-workbench-bridge.mjs"
export QR_WORKBENCH_BRIDGE_TOKEN_FILE="$(pwd)/dashboard/data/feishu-workbench/bridge.token"
```

The following is an integration pattern, **not a standalone installer**. Here `handlers` is the plugin's existing handler map and `accountId` is the current authenticated connection. Apply the wrapper before the map is registered with its existing dispatcher. Pass the original event unchanged, and return the bridge's result to Feishu. Only `null` means “continue to the original handler”; `{}` means the event was accepted for asynchronous processing.

以下是接入模式，**不是可独立运行的安装器**。其中 `handlers` 指插件已有的处理器表，`accountId` 指当前已认证连接。应在该表注册进现有分发器前包装处理器，原样转发事件，并将返回值交还飞书。只有 `null` 才继续调用原处理器，`{}` 也表示已接收并异步处理。

```js
import { pathToFileURL } from 'node:url';

const { dispatchWorkbenchEvent } = await import(
  pathToFileURL(process.env.QR_WORKBENCH_BRIDGE_MODULE).href
);
for (const eventType of [
  'im.message.receive_v1',
  'card.action.trigger',
  'application.bot.menu_v6',
  'im.chat.access_event.bot_p2p_chat_entered_v1',
]) {
  const previous = handlers[eventType];
  handlers[eventType] = async (event) => {
    const response = await dispatchWorkbenchEvent({ accountId, eventType, event });
    if (response !== null) return response;
    return previous ? previous(event) : {};
  };
}
```

Do not create a second Feishu connection for this bridge. Its 1.8-second deadline covers queue acceptance, not completion of a report or experiment. Slow work runs in the companion, which updates the message afterwards. A bridge timeout does not prove that an operation was rejected; inspect its state before trying again.

不要为桥接模块创建第二条飞书连接。桥接的 1.8 秒时限用于确认任务是否接收，不用于等待日报或实验完成；耗时任务由伴随服务执行，随后更新卡片。桥接超时不表示任务一定未被接收，应先核对状态再决定是否重试。

## 3. Configure the Feishu console / 配置飞书控制台

Use a bot application that is available to the intended users. Enable the application permissions required to send messages as the bot, update its own messages, and receive private messages. If enabling entry events, also enable the permission shown for the [bot private-chat entry event](https://open.feishu.cn/document/group/chat-member/event/bot_p2p_chat_entered). No workspace-wide document or trading permissions are needed for this companion.

使用对目标用户可见的机器人应用，启用以应用身份发消息、更新自身消息、接收用户私聊消息所需的应用权限。启用进入私聊事件时，还需开通[该事件文档](https://open.feishu.cn/document/group/chat-member/event/bot_p2p_chat_entered)列出的权限。此伴随服务无需工作区文档权限或交易权限。

Configure the existing long-connection delivery mode under **Events & Callbacks**. Event subscriptions and callback subscriptions are separate settings; a working text conversation does not prove that card callbacks are configured. See the [official card callback setup reference](https://github.com/larksuite/cli/blob/main/skills/lark-im/references/lark-im-card-action-reply.md).

在**事件与回调**中使用已有长连接的投递方式。事件订阅与回调订阅是不同配置；文字聊天正常并不代表卡片回调已配置。参见[官方卡片回调配置说明](https://github.com/larksuite/cli/blob/main/skills/lark-im/references/lark-im-card-action-reply.md)。

**Required:** open the application's **Events & Callbacks → Callback Configuration**, enable **Card interaction callback (`card.action.trigger`)**, and select delivery through the existing long connection. Save and publish the configuration as required by the console. The companion cannot enable this setting for the application.

**必做：**进入当前应用的**事件与回调 → 回调配置**，启用**卡片回传交互（`card.action.trigger`）**，选择使用已有长连接接收回调，并按控制台要求保存、发布配置。伴随服务不能代替应用开启此配置。

| Console area / 控制台位置 | Identifier / 标识 | Purpose / 用途 |
|---|---|---|
| Event configuration / 事件配置 | `im.message.receive_v1` | Required for the exact text entry command / 精确文字入口需要 |
| Callback configuration / 回调配置 | `card.action.trigger` | Required for buttons and forms / 按钮和表单必需 |
| Event configuration / 事件配置 | `application.bot.menu_v6` | Optional native menu / 可选原生菜单 |
| Event configuration / 事件配置 | `im.chat.access_event.bot_p2p_chat_entered_v1` | Optional initial card on private-chat entry / 可选首次进入私聊时打开卡片 |

For a native menu, configure **Bot → Custom Menu** with event actions, then publish the app configuration through the console. Start with three main entries below; `qrw_home` is also supported for a homepage entry. The [official menu event reference](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/application-v6/bot/events/menu) describes the menu event.

原生菜单需要在**机器人 → 自定义菜单**中配置事件动作，并通过控制台发布应用配置。建议使用以下三个主入口；也支持通过 `qrw_home` 打开主页。菜单事件见[官方说明](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/application-v6/bot/events/menu)。

| Menu label / 菜单名称 | `event_key` |
|---|---|
| 每日更新 / Daily update | `qrw_daily` |
| 资产总览 / Assets | `qrw_assets` |
| 研究任务 / Research | `qrw_research` |

Menu publishing is a separate console action; the repository does not perform it. Without a menu, an allowlisted user can send exactly **`工作台`**, **`QR工作台`**, or **`/workbench`** in the bot's private chat. Additional prose is not interpreted as a workbench command. Re-entering a chat that already has a workbench card does not automatically send another one.

菜单发布是单独的控制台操作，仓库不会自动执行。没有菜单时，白名单用户仍可在机器人私聊中发送精确文本 **`工作台`**、**`QR工作台`** 或 **`/workbench`**。附带其他描述的文字不会作为工作台命令；已有工作台卡片的会话再次进入时，不会自动重复发卡。

After setup, this optional command **sends a real card** to `initial_user` from the allowlist. Reusing the same `--request-id` avoids reopening it as a separate request. It is not an offline preview.

配置完成后，以下可选命令会向白名单中的 `initial_user` **实际发送卡片**。重复使用相同的 `--request-id` 可避免作为新请求重复打开；这不是离线预览命令。

```bash
PYTHONPATH=dashboard python -m feishu_workbench open \
  --config dashboard/data/feishu-workbench/config.json \
  --panel home --request-id first-workbench
```

## Using the cards / 使用卡片

| View / 页面 | Workflow / 操作 |
|---|---|
| Home / 主页 | Daily update, assets, research; reports as a secondary entry / 每日更新、资产总览、研究任务，另有日报入口 |
| Daily update / 每日更新 | One-click unchanged update, or a resumable list of changes across accounts and products, followed by one confirmation / 无变动一键更新；有变动则逐项添加账户与产品，统一核对确认 |
| Assets / 资产 | Read saved positions and each account's original-currency cash; the legacy fund total is CNY / 查看已保存持仓与各账户原币现金，旧版基金合计估值为 CNY |
| Research / 研究 | Select an existing dataset, template and manual/Agent mode; review configuration, then start / 选择已有数据集、模板和手动或 Agent 模式，核对配置后启动 |
| History / 研究记录 | Select from up to 20 recent tasks; inspect progress or results, refresh, or cancel a pending/running task / 从最多 20 项近期任务中选择，查看进度或结果、刷新或取消等待中及运行中任务 |
| Reports / 日报 | Inspect recent saved reports and report-job status; retry a failed report separately / 查看近期已保存日报和更新状态，可单独重试失败的日报任务 |

**No change:** press **今日无变动，更新日报** to confirm that all accounts are unchanged and start the configured daily report. There are no fields or additional preview. The displayed date uses Asia/Shanghai. This button is unavailable while a draft contains unsaved changes.

**With changes:** open **有变动**, then add each buy, sell, cash balance or fund valuation to the list. Each entry selects its own account; one batch can update one account or several. Existing products are selected from that account's holdings, including earlier entries in the draft. Sells support a specified quantity or the full remaining position. A new buy requires an explicit market-prefixed code, such as `NASDAQ:EXAMPLE`; trade currency follows the market. Quantities are shares and prices/fees use that currency.

The list supports up to **30 entries**, editing, deleting, changing the common date, and leaving/reopening the workbench. Drafts are stored locally per user and do not change holdings. Trades are calculated in list order. A cash reconciliation is a **final balance after all trades**, regardless of where it was added in the list; it replaces calculated cash for that account and currency. Fund reconciliation changes only that account's **CNY fund total valuation**, independently of cash, and does not record a subscription or redemption. Enter each account/currency balance only once; edit that entry to correct it. Exchange-traded ETF purchases and sales use the buy/sell path. Negative resulting cash is disclosed for reconciliation, not treated as a broker financing instruction.

**核对全部变动** shows every trade and each affected account's final cash and fund valuation. Untouched accounts remain unchanged. Confirm once to save the entire list and queue one report. If a save is interrupted, reopen the list and use **继续处理** to recover the same update; confirmed trades are not applied again. When the underlying holdings or draft changes after preview, review a fresh preview.

**无变动：**点击 **今日无变动，更新日报**，即确认所有账户无变化并更新日报，无需填写表单或再次预览。日期以北京时间为准；清单中有未保存变动时，该按钮不可用。

**有变动：**逐项添加买入、卖出、现金余额或基金估值，每项单独选择账户，支持一个账户或多个账户一起更新。产品从所选账户的持仓中选择，已加入清单的买卖也会计入可选持仓；卖出支持指定数量或全部卖出。买入新产品时填写带市场前缀的代码，成交币种按市场确定。数量按股填写，单价和手续费使用对应币种。

清单最多 **30 项**，可以修改、删除、调整记账日期，退出工作台后也能继续填写。加入清单不会修改实际持仓。买卖按清单顺序计算；**现金对账始终表示所有买卖后的最终余额**，与加入清单的先后顺序无关。每个账户、每种币种只填写一次最终余额，需要更正时修改该项。基金估值仅更新该账户的 CNY 基金合计，现金单独记录，不表示申购或赎回；场内 ETF 买卖使用买入、卖出入口。最终现金为负时会明确提示核对，不会向券商申请融资。

点击 **核对全部变动**，检查每笔买卖及各账户最终现金、基金估值，再统一保存并生成一份日报。未添加变动的账户保持原样。保存中断时，重新打开清单后点击 **继续处理**，会恢复同一次更新，避免重复入账。预览后持仓或清单发生变化时，需要重新核对。

**Capital flows (v3.2.1):** use **资金转入（增加本金）** or **资金转出（减少本金）** for new funding or withdrawals. Each changes native-currency cash and account contributed capital together, without changing position costs. CNY flows use the same amount for cash and capital; USD/HKD flows require the explicit CNY capital amount recorded for that transfer. Use **本金核对（只改本金）** for the final account principal if cash has already been updated. This is account net contributed capital, not a stock's per-share cost; changing it also changes the legacy inferred capital flow. It may be negative when cumulative withdrawals exceed deposits. The legacy percentage return is not a meaningful return-on-capital measure with zero or negative net capital; consult the [accounting conventions](ACCOUNTING.md) for the daily approximation.

A final cash reconciliation overrides all trade/flow cash movements in the batch. A final principal reconciliation likewise overrides the principal after flows; do not enter a principal increment into this final-total field. Only one final principal entry per account is allowed. Existing accounts must have a recorded principal before capital-flow entries can be used; missing principal is not assumed to be zero.

**资金与本金（v3.2.1）：**新增资金选 **资金转入（增加本金）**，提取资金选 **资金转出（减少本金）**，两者同时调整现金和账户投入本金，不修改持仓成本。人民币按相同金额登记；美元或港元需明确填写这次资金流对应的人民币本金。现金已经更新过时，可以用 **本金核对（只改本金）** 填写更新后的本金总额。这里是账户净投入本金，不是某只股票的成本价；它的变化会影响原有日报推算的资金流。累计转出超过转入时，净投入可能为负，零或负本金下的累计收益率不能作为有效的本金回报率。

同一清单的现金对账始终使用买卖、资金转入转出后的最终余额；本金核对也使用这些变动后的最终本金总额，不能填写“增加了多少”。每个账户只添加一项本金核对。账户缺少初始本金时需要先初始化，不会默认按零处理。已加入清单的记录会保留。

Without a transaction ledger the card edits the **dated JSON holdings** files as described above; once the ledger is active it records into the ledger instead (next section). It never places broker orders. Assets reflect saved records; opening or refreshing the asset card does not fetch live quotes or invent missing valuations.

没有交易账本时，卡片按上文修改**按日期保存的 JSON 持仓**；账本启用后改为记入账本（见下一节）。卡片不向券商下单。资产反映已保存记录，打开或刷新资产卡不会抓取实时行情，也不会补造缺失估值。

## With the transaction ledger / 启用交易账本后

When `ledger.sqlite3` in the portfolio directory has a confirmed revision, the daily card records into the ledger; without one it behaves as above. The card is then one more way to make a [ledger proposal](LEDGER.md) and confirm it, like 记一笔 in the web app: each item becomes one or more ledger events, **核对全部变动** shows the ledger's own preview of them, and **确认入账并更新日报** on that preview is the person confirming the proposal. The receipt lists what was booked.

**In-process, not through the HTTP API.** The companion runs as its own process and may run while the backend is stopped, so it uses `core.ledger` (the code behind `/api/ledger`) directly on the same `ledger.sqlite3`. The ledger's guarantees live in that code: exact decimals, a proposal confirmed only at the revision it was previewed against, every later event replayed, a repeated confirmation returning the first receipt, events and receipt committed in one SQLite transaction. A confirmation is therefore one local transaction rather than a request whose outcome a timeout could leave unknown. Like the confirm endpoint, the card stores the trade-plan links of what it books. The companion and the backend must use the same portfolio directory (`PORTFOLIO_DIR`): fee profiles and trade plans are read from the `desk.sqlite3` beside the ledger. The companion chooses that directory once at start (`PortfolioAdapter.configured`, the backend's rule) and uses it for everything, so it never edits an old holdings file elsewhere while the configured directory has an active ledger.

| Card item | What is booked |
|---|---|
| 买入 / 卖出 (A shares, US, Hong Kong settled in HKD) | one `buy` / `sell` in the market's currency. A fee left empty is estimated from the account's fee profile exactly as 记一笔 estimates it, shown as 估算 and recorded in the event's note as 〔费用为估算：按账户费率估算〕; without an estimate (no profile for that market, an unclassified code) the fee must be typed. A buy beyond the cash in that currency is refused unless 允许融资 is chosen |
| 港股通买入 / 卖出 (a Hong Kong trade with its CNY amount) | as the broker-file importer books 港股通: `fx` CNY→HKD and an HKD `buy`, or an HKD `sell` and `fx` HKD→CNY. Rate = CNY amount ÷ HKD turnover; the CNY fee (required) is converted at that rate to the cent, so CNY cash moves by exactly the amount plus or minus the fee. The ledger books an `fx` whatever the cash, so the card refuses a 港股通 buy beyond the CNY cash (after the items above it) unless 允许融资 is chosen, as for any buy. The HKD `buy` is paid exactly by its `fx` and is booked with margin allowed, so HKD owed before does not block a buy paid in CNY |
| 资金转入 / 资金转出 | `deposit` / `withdrawal` (cash and 累计净投入). Foreign currency needs the CNY principal of that transfer; `fx_rate` is derived from it so that 累计净投入 moves by exactly that principal |
| 本金更新（按差额转入转出） | target 累计净投入 − the ledger's, as one CNY `deposit` or `withdrawal` |
| 基金估值 | `fund_value` (CNY); the ledger's value already, no event |
| 现金对账（现在是 X） | after every other item: X − the ledger's balance in that currency. A positive difference is one `interest`, a negative one a `fee`, each with a note naming both balances; neither changes 累计净投入. The preview shows the ledger balance, X, the difference and the kind before anything is booked. No difference, no event. A difference in a currency that the same list also moves with a transfer needs a 差额说明 |
| 今日无变动 | nothing is booked; only the report runs |

**Refused** with a short reason: overwriting a quantity or a cost price, and changing 累计净投入 without moving cash (本金核对, 资金已入账, 修正历史本金). The ledger has no such events; reverse the entry in 账本 › 流水 and enter it again with 记一笔.

**Exactly once.** Repeated taps and Feishu redeliveries reuse the command's nonce; the companion's operations store is keyed on the ledger proposal, so two cards, two taps or a restart between preview and confirm meet the same operation and receipt; the ledger itself books a proposal once. Events carry a source `feishu-card` and an external id from the list item, so a list item that was booked is shown as already booked and never booked again. An item whose booking was later reversed in 账本 › 流水 is refused (the ledger would count it as a duplicate and book nothing); delete it and add it again to book it anew, and a receipt shown again for such a booking says it was reversed since. **Only the card confirms its proposals**: the web's 待确认 lists them for 2 hours as 在飞书卡片上确认, where they can be read and discarded but not confirmed (the confirm endpoint refuses them), because only the card's confirmation completes its list and queues the day's report. Once a list is confirmed every item of it is handled (a 现金对账 that needed no adjustment too): the list is emptied, and the same preview confirmed again returns the first receipt. Editing, clearing or re-dating the list, or previewing it again, withdraws its earlier proposal, also when the new preview books nothing.

**Out-of-date previews.** If the ledger changed after the preview (an entry in the web app, an import, another card), the proposal can no longer be confirmed: the card withdraws it, books nothing, and shows a fresh preview of the same list to confirm again.

**Dates.** Every event is dated the list's date. An entry dated before existing ones is checked as the ledger checks any back-dated entry (`replay_for_entry`); the cash difference of 现金对账 is taken on that date.

**Report and close snapshot.** After a booking, the card runs the same pipeline as before (`run_pipeline` for the list's date, under the portfolio write lock), now valuing the ledger. The backend's 15:40 close snapshot runs `auto-pipeline` under the same lock and the same checkpoint (`pipeline-state/<date>.json`), which records the holdings it was made from, including the ledger revision. So the same holdings never get a second snapshot or push (a push whose result is unknown is never resent), while an entry confirmed in the evening changes the holdings and the card's run makes that day's snapshot and report again. Like the close pipeline (`auto-pipeline`), the card pushes the report only when `feishu_chat_id` is configured; without it the new snapshot and report are saved, the checkpoint records the report as complete with no push pending, and 日报 shows 日报已按入账后的持仓生成并保存 (not sent, not failed). A later entry the same day makes them again the same way. (Without a ledger the card still pushes every report, as before.) A confirmation that books nothing (今日无变动, a list the ledger already holds) leaves a day whose report is complete as it is: no second snapshot, report or push (the checkpoint names the ledger revision, which any later entry moves). **Before the day's closes.** The card books an entry (or a 今日无变动) at any time, but makes the day's snapshot only when the snapshot engine would book it, by the engine's own rule (`core.market.calendar.snapshot_wait`, which the engine's snapshot asks too, so the two never disagree): for each market the ledger holds, the day books the session that ends on that Beijing date (the mainland's and Hong Kong's of the day itself, New York's of the night before) and waits until its close is final (the mainland 15:00, Hong Kong 16:10, early closes included); a session of that market still under way is waited for as well (New York's from 22:30 Beijing, until its close is final the next morning). So with a US holding a day is complete at 16:10, an entry at 21:00 makes the day's report again at once, and one between 22:30 and the next morning (dated that day, within its window to 09:00) waits for New York's close. Earlier, a snapshot would record the previous session's prices as the day's close. The receipt says the entry is booked and which close the report waits for, in the close snapshot's words (等待港股收盘价确定，16:10 后记入), and that it follows automatically, and 日报 shows the job as 等待收盘 with the time, not as failed. The same happens when the snapshot engine itself says a close is not final yet. The card service then runs the job itself: it keeps the waiting state and the time with the operation (so a restart keeps them), looks every 30 seconds for jobs whose time has come and queues each one once; the run takes the portfolio lock and follows the checkpoint, so a report the close snapshot made meanwhile from the same holdings is not made or pushed again. 重试日报 stays available while a job waits: a tap before the time only waits again, and a tap while the timer's run is queued or running adds nothing. If the service is down at the time, the job runs once when the service is back, as long as that is before 09:00 the next morning; after that it is not made by the card any more and 日报 shows it as 未生成 (the ledger is unchanged). An entry dated before the previous day, or made after 09:00 the next morning, does not remake old snapshots (see [LEDGER.md](LEDGER.md)).

**Who may confirm.** As for every card action: only users on the companion's allowlist (`allowed_users`), on a card sent to them in the bot's private chat; a forwarded card or another chat is refused.

**启用交易账本后：**组合目录中的 `ledger.sqlite3` 有已确认版本时，卡片记入账本；没有账本时保持上述行为。卡片相当于网页 记一笔 之外的又一个入口：清单中每一项变成一笔或几笔账本记录，**核对全部变动** 显示账本自己的预览，在预览上点 **确认入账并更新日报** 即本人确认这份方案，回执列出已入账的记录。伴随服务在本进程内直接使用 `core.ledger`（与 `/api/ledger` 相同的代码），后端未运行时也能记账；伴随服务与后端必须使用同一个组合目录。

买卖按市场币种记一笔 `buy` / `sell`，手续费留空时按账户费率估算（与 记一笔 相同，标明 估算）；港股通按交割单导入的方式拆成换汇和港币成交，人民币现金不足时与其他买入一样需要选择允许融资；资金转入转出记 `deposit` / `withdrawal`，外币按填写的人民币本金推算汇率；本金更新按差额记人民币转入或转出；基金估值记 `fund_value`（与账本相同时不记录）；“现金现在是 X”在其他各项之后与账本余额比较，差额为正记一笔 `interest`、为负记一笔 `fee` 并附说明，不改变累计净投入，预览中先列出差额和记法。直接改数量、成本价或只改本金会被拒绝：请在网页 账本 › 流水 冲销原记录后用 记一笔 重新录入。重复点击、飞书重发、预览后重启、两张卡片都不会重复入账；卡片的方案只能在卡片上确认，网页“待确认”中显示为“在飞书卡片上确认”，只能放弃；清单确认后其中每一项都算已处理（包括不需要调整的现金对账），清单清空；修改、清空或重新预览清单会撤回之前的方案，新预览不需要入账时也一样；已入账后又在网页冲销的清单项会被拒绝，需删除后重新添加；预览后账本有变化时作废原预览并重新生成。入账后按清单日期重新生成快照和日报；同一持仓不会重复生成或推送，晚间入账会让当日快照和日报按新持仓重做。与收盘流程相同，只有配置了 `feishu_chat_id` 才推送；未配置时日报生成并保存即算完成，“日报”里显示已生成并保存（没有推送，也不是失败）。没有新增记录的确认（今日无变动等）不会重做或再次推送当日已完成的日报。快照引擎还不能记入当日收盘价之前（与引擎用同一条规则：账本持有的每个市场，当日记入的是在这个北京日期收盘的那一场，A股、港股是当天的，美股是前一晚的；该市场正在交易的一场也要等它收盘确定。所以持有美股时当天 16:10 港股确定后即可生成，21:00 入账会立即重做当日日报，22:30 之后到次日美股收盘确定之前入账则要等），卡片照常入账但不生成快照，回执说明在等哪个市场的收盘价、几点之后自动生成，“日报”里显示“等待收盘”而不是失败；到时伴随服务自动运行这次日报任务，只运行一次（等待状态和时间与这次入账一起保存，重启后仍在；收盘快照若已按同一持仓生成，不会重复生成或推送）。等待期间仍可点“重试日报”，不会因此多运行一次。伴随服务在那个时间没有运行时，恢复后若还在次日 09:00 之前就补做一次；超过后不再生成，“日报”里显示“未生成”。只有白名单用户能在发给自己的私聊卡片上确认。

Financial changes require a separate confirmation. If the holdings change after preview, generate a new preview. Commands, card revisions and nonces are stored locally to reject stale actions and deduplicate repeated clicks; confirmation also has a persistent operation identifier. A saved-asset receipt and report completion are separate states. After confirmation, the existing daily pipeline can fetch quotes and **send reports to its already configured Feishu/Telegram destinations**, which may differ from the card's private chat. Verify those destinations before using daily confirmation. Retry a failed report from Reports; it does not repeat the asset mutation. An uncertain delivery state may require manual inspection rather than automatic resend.

资产变化必须单独确认；预览后持仓发生变化时，需要重新预览。本机保存命令、卡片版本和一次性操作标识，拒绝旧操作并对重复点击去重，确认也使用持久化操作编号。**资产已保存**与**日报已完成**是不同状态。确认后，原有日报管道可能获取行情，并**推送到已配置的飞书或 Telegram 目标**，这些目标可能不同于当前卡片私聊；使用前请核对。失败日报可从“日报”单独重试，不会重复修改资产。发送状态无法确认时，系统可能要求人工核查，而不是自动重发。

Research cards reuse existing [research datasets, templates and model connections](RESEARCH.md). Configure API providers, third-party compatible providers, or an available official Codex/Claude Code subscription connection through the research connection settings. A subscription or configured key does not guarantee model availability or quota. Cards do not collect API keys, import CSV files, or configure model connections. Initial dataset and connection setup uses the research UI, CLI or API; daily operation of configured experiments can stay in Feishu.

研究卡片复用已有[数据集、模板和模型连接](RESEARCH_CN.md)。通过研究连接设置接入 API、第三方兼容服务，或可用的官方 Codex/Claude Code 订阅连接；已配置密钥或订阅不保证模型权限与额度可用。卡片不收集 API Key，也不提供 CSV 导入和连接配置表单。首次准备数据与连接使用研究界面、CLI 或 API，配置好的日常实验操作可在飞书内完成。

Manual research does not call a model. Agent research discloses that the selected model receives the research question, dataset metadata and computed evidence before the user confirms startup; it then runs within the displayed experiment and time budgets. Progress can update asynchronously, and cancellation is a request rather than an immediate completion guarantee. Final cards include evaluation metrics, curves where available, and learning/method notes. Research is for studying and evaluating stock methods, not automatic live trading.

手动实验不调用模型。Agent 启动预览会说明研究问题、数据元信息和计算证据将发送给所选模型，用户确认后才在所示次数与时间预算内运行。进度可异步更新；取消是请求，不代表立即停止。结果卡展示评价指标、可用曲线和学习及方法说明。这些能力用于股票方法学习与验证，不执行自动实盘交易。

## Pages from extensions / 扩展页面

A separately installed module named in `TRACKER_EXTENSIONS` can add card pages by defining `workbench_pages()` ([EXTENSIONS.md](EXTENSIONS.md#feishu-workbench-pages)). Set the variable in the environment of `feishu_workbench.control start`; the companion does not read `.env`. It imports every named module once at start and uses only `workbench_pages()`. A module that fails to import, or whose pages are refused, is logged in `service.log` and left out. Without the variable nothing changes. A page's actions on cards the companion renders are checked like built-in ones: the allowlist, the card's nonce and session, and a `.confirm` only from the page's own `_preview` card. Its views go through the same card budget. An `entry` adds a button to the home card. A page can also declare `external` actions for buttons on cards another process sent with the same bot (value `{"qr_workbench": 1, "action": …}` without a nonce, which the bridge already forwards). Such a press is answered at once from the allowlist alone; the page itself must check that it sent that card and that it is still current, and may only do what is harmless to repeat. Built-in actions always need their nonce. A page cannot take a built-in action or view name.

`TRACKER_EXTENSIONS` 中列出、定义了 `workbench_pages()` 的独立模块可以为卡片增加页面（见 [EXTENSIONS.md](EXTENSIONS.md#feishu-workbench-pages)）。变量须设置在运行 `feishu_workbench.control start` 的环境中，伴随服务不读取 `.env`。启动时导入列出的每个模块一次，只使用 `workbench_pages()`；导入失败或页面被拒绝的模块记入 `service.log` 并跳过。未设置时行为不变。页面在伴随服务所发卡片上的操作与内置操作同样检查：白名单、卡片 nonce 与会话，`.confirm` 只能在该页面自己的 `_preview` 卡片上确认。页面视图受同样的卡片大小限制，`entry` 会在主页卡片上加一个按钮。页面还可以声明 `external` 操作，用于同一机器人由其他进程发送的卡片（按钮值为不带 nonce 的 `{"qr_workbench": 1, "action": …}`，桥接模块已会转发）。这类点击只按白名单检查后立即应答：页面必须自行核对这张卡片是否由它发送、是否仍然有效，并且只能执行可重复的无害操作。内置操作始终需要 nonce；页面不能占用内置的操作名或视图名。

## Privacy and troubleshooting / 数据边界与故障处理

Private-chat cards are messages stored by Feishu. Disabling card forwarding is not a confidentiality boundary: recipients can still read or capture the content. Only allow trusted workspace owners. The loopback token, application credentials, local session databases, logs, portfolio files and research exports must remain private. Research exports are not automatically redacted. The callback bridge does not send these operations to a chat model; remote model transmission occurs when an Agent research connection is explicitly used.

私聊卡片仍是由飞书存储的消息。关闭卡片转发不是保密边界，接收者仍可阅读或截图。只对白名单中的可信工作区使用者开放。桥接令牌、应用凭证、会话数据库、日志、持仓文件与研究导出应妥善保管；研究导出不会自动脱敏。卡片桥接不会将这些操作交给聊天模型，使用 Agent 研究连接时才会向相应模型发送研究上下文。

| Symptom / 现象 | Check / 检查 |
|---|---|
| “App has not configured card callbacks” / “该应用尚未配置卡片回调” | Enable `card.action.trigger` in the console's Callback Configuration, then save/publish / 必须在控制台回调配置中启用该回调，并保存、发布 |
| Text chat works, buttons do not / 文字正常、按钮无响应 | Check the separate `card.action.trigger` callback subscription, published app configuration, and whether the plugin returns the bridge response / 检查独立回调订阅、应用配置发布和插件是否返回桥接响应 |
| No native menu / 没有原生菜单 | Publish the console menu; use exact text `工作台` in the meantime / 在控制台发布菜单，也可先用精确文字入口 |
| Unauthorized / 没有权限 | Check the app-specific `open_id`, connection `account_id`, and allowlist; a forwarded card belongs to its original session / 核对当前应用下的 open_id、连接名与白名单，转来的卡片仍属于原会话 |
| Cannot confirm / 无法确认 | Reopen and preview current data. With a ledger, a preview the ledger moved past is shown again to confirm; a refusal naming `PORTFOLIO_DIR` means the companion and the backend use different portfolio directories; a card's proposal in the web's 待确认 is confirmed on the card / 重新打开并预览当前数据。账本启用时，账本已变化的预览会重新生成后再确认；提示 `PORTFOLIO_DIR` 不一致时，伴随服务与后端用的不是同一个组合目录；网页“待确认”中来自卡片的方案请在卡片上确认 |
| Asset saved, report pending / 资产已保存、日报未完成 | Inspect Reports and the existing notification setup; retry only the report / 查看日报状态与已有通知配置，只重试日报 |
| Research unavailable / 研究暂不可用 | Check the local research API, selected dataset and connection; use history/refresh before restarting an experiment / 检查本机研究 API、数据集和连接，重新启动实验前先查看历史或刷新状态 |
| Companion fails to start / 伴随服务启动失败 | Inspect private `dashboard/data/feishu-workbench/service.log`, file permissions and port availability; do not publish raw logs / 查看私有日志、文件权限与端口占用，不要直接公开原始日志 |

Offline tests cover card structure, callback routing, stale-action rejection, duplicate operations and recovery cases. They do not replace checking actual card rendering and callbacks in your own Feishu application after publishing its configuration. Start with navigation and synthetic manual research before confirming real portfolio changes.

离线测试覆盖卡片结构、回调路由、旧操作拒绝、重复操作与恢复场景，但不能替代应用配置发布后，在自己的飞书应用中验证真实渲染和回调。首次验收先测试页面导航与合成数据手动研究，再确认真实组合变化。
