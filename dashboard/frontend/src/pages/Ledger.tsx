import { cloneElement, useState } from "react";
import type { FormEvent, ReactElement } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Check, Download, RefreshCw, Undo2 } from "lucide-react";
import api, { getApiErrorMessage } from "@/services/api";

type Position = {
  ticker: string;
  name: string;
  quantity: string;
  cost_price: string;
  currency: string;
};
type Account = {
  cash_balances: Record<string, string>;
  positions: Record<string, Position>;
  contributed_cny: string;
  fund_cny: string;
  realized: Record<string, string>;
};
type State = { accounts: Record<string, Account>; date: string | null };
type Event = {
  id: string;
  kind: string;
  date: string;
  account?: string;
  ticker?: string;
  quantity?: string;
  price?: string;
  currency?: string;
  amount?: string;
  note?: string;
  target_id?: string;
  fee?: string;
};
type Proposal = {
  id: string;
  revision: number;
  before: State;
  after: State;
  events: Event[];
  duplicates: number;
  warnings: string[];
};
type LedgerData = State & { revision: number; pending: Proposal[] };
type JournalEntry = {
  id: string;
  ticker: string;
  thesis: string;
  invalidation: string;
  review_on: string;
  created_at: string;
};
const kinds: Record<string, string> = {
  opening: "期初余额",
  buy: "买入",
  sell: "卖出",
  deposit: "入金",
  withdrawal: "出金",
  dividend: "股息",
  fee: "费用",
  transfer: "账户转账",
  fx: "换汇",
  split: "拆股",
  reverse: "冲销",
  fund_value: "基金估值调整",
  fund_buy: "基金申购",
  fund_sell: "基金赎回",
};
const today = () => new Date().toLocaleDateString("en-CA");
const box = "rounded-xl border border-slate-700 bg-slate-800/60 p-4 md:p-6";
const input =
  "mt-1 w-full min-w-0 rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary-500";
const button =
  "inline-flex items-center justify-center gap-2 rounded-lg border border-slate-600 px-3 py-2 text-sm hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed";
const primary = `${button} bg-primary-600 border-primary-500 text-white hover:bg-primary-500`;
const num = (v: string | undefined) =>
  v === undefined
    ? "0"
    : Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 8 });

function Field({
  title,
  children,
}: {
  title: string;
  children: ReactElement<{ "aria-label"?: string }>;
}) {
  return (
    <label className="block min-w-0 text-sm text-slate-300">
      {title}
      {cloneElement(children, { "aria-label": title })}
    </label>
  );
}

function Changes({ proposal }: { proposal: Proposal }) {
  const names = Array.from(
    new Set([
      ...Object.keys(proposal.before.accounts),
      ...Object.keys(proposal.after.accounts),
    ]),
  );
  return (
    <div className="space-y-4">
      {names.map((name) => {
        const before = Object.prototype.hasOwnProperty.call(
          proposal.before.accounts,
          name,
        )
          ? proposal.before.accounts[name]
          : undefined;
        const after = Object.prototype.hasOwnProperty.call(
          proposal.after.accounts,
          name,
        )
          ? proposal.after.accounts[name]
          : undefined;
        const rows: { name: string; from?: string; to?: string }[] = [];
        for (const unit of new Set([
          ...Object.keys(before?.cash_balances || {}),
          ...Object.keys(after?.cash_balances || {}),
        ])) {
          if (before?.cash_balances[unit] !== after?.cash_balances[unit])
            rows.push({
              name: `${unit} 现金`,
              from: before?.cash_balances[unit],
              to: after?.cash_balances[unit],
            });
        }
        for (const ticker of new Set([
          ...Object.keys(before?.positions || {}),
          ...Object.keys(after?.positions || {}),
        ])) {
          const prev = before?.positions[ticker],
            next = after?.positions[ticker];
          if (prev?.quantity !== next?.quantity)
            rows.push({
              name: `${ticker} 股数`,
              from: prev?.quantity,
              to: next?.quantity,
            });
          if (prev?.cost_price !== next?.cost_price)
            rows.push({
              name: `${ticker} 单位成本`,
              from: prev?.cost_price,
              to: next?.cost_price,
            });
        }
        if (before?.contributed_cny !== after?.contributed_cny)
          rows.push({
            name: "累计净投入 CNY",
            from: before?.contributed_cny,
            to: after?.contributed_cny,
          });
        if (before?.fund_cny !== after?.fund_cny)
          rows.push({
            name: "基金估值 CNY",
            from: before?.fund_cny,
            to: after?.fund_cny,
          });
        for (const unit of new Set([
          ...Object.keys(before?.realized || {}),
          ...Object.keys(after?.realized || {}),
        ])) {
          if (before?.realized[unit] !== after?.realized[unit])
            rows.push({
              name: `${unit} 已实现交易盈亏`,
              from: before?.realized[unit],
              to: after?.realized[unit],
            });
        }
        if (!rows.length && before && after) return null;
        return (
          <div key={name}>
            <h3 className="font-medium mb-2 break-words">
              {name}{" "}
              {!before && (
                <span className="text-primary-400 text-xs">新账户</span>
              )}
            </h3>
            <div className="space-y-2">
              {rows.map((row) => (
                <div
                  key={row.name}
                  className="flex flex-wrap justify-between gap-2 text-sm"
                >
                  <span className="text-slate-400">{row.name}</span>
                  <span className="font-mono tabular-nums">
                    {num(row.from)} <span className="text-slate-500">→</span>{" "}
                    {num(row.to)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function Ledger() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["ledger"],
    queryFn: async () => (await api.get<LedgerData>("/ledger")).data,
    refetchInterval: 15000,
  });
  const history = useQuery({
    queryKey: ["ledger-events"],
    queryFn: async () =>
      (await api.get<{ events: Event[] }>("/ledger/events")).data,
  });
  const journal = useQuery({
    queryKey: ["ledger-journal"],
    queryFn: async () =>
      (await api.get<{ entries: JournalEntry[] }>("/ledger/journal")).data,
  });
  const [tab, setTab] = useState<"record" | "history" | "journal">("record");
  const [kind, setKind] = useState("buy");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [openingCount, setOpeningCount] = useState(1);
  const [source, setSource] = useState("broker-csv");
  const [migrationDate, setMigrationDate] = useState(today());
  const data = query.data;
  const accounts = Object.keys(data?.accounts || {});
  const selectedKind = accounts.length ? kind : "opening";
  const refresh = async () => {
    await client.invalidateQueries({
      predicate: (q) => String(q.queryKey[0]).startsWith("ledger"),
    });
    await client.invalidateQueries({ queryKey: ["portfolio"] });
  };
  const run = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await operation();
    } catch (e) {
      setError(getApiErrorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const preview = async (events: Record<string, unknown>[]) => {
    const result = await api.post<Proposal>("/ledger/proposals", { events });
    setProposal(result.data);
    await refresh();
  };
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const get = (key: string) => String(form.get(key) || "").trim();
    const event: Record<string, unknown> = {
      kind: selectedKind,
      date: get("date"),
      account: get("account"),
    };
    for (const key of [
      "ticker",
      "currency",
      "quantity",
      "price",
      "fee",
      "amount",
      "fx_rate",
      "to_account",
      "to_currency",
      "received_amount",
      "ratio",
      "note",
    ]) {
      if (get(key)) event[key] = get(key);
    }
    if (selectedKind === "opening") {
      event.cash_balances = Object.fromEntries(
        ["CNY", "HKD", "USD"].map((unit) => [unit, get(unit) || "0"]),
      );
      event.contributed_cny = get("contributed_cny");
      event.fund_cny = get("fund_cny") || "0";
      event.positions = Array.from({ length: openingCount }, (_, i) => ({
        ticker: get(`opening_ticker_${i}`),
        quantity: get(`opening_quantity_${i}`),
        cost_price: get(`opening_cost_${i}`),
      })).filter((p) => p.ticker);
    }
    if (form.get("allow_margin")) event.allow_margin = true;
    void run(() => preview([event]));
  };
  const trade = ["buy", "sell"].includes(selectedKind);
  const fund = ["fund_buy", "fund_sell", "fund_value"].includes(selectedKind);
  const cash = [
    "deposit",
    "withdrawal",
    "dividend",
    "fee",
    "transfer",
    "fx",
  ].includes(selectedKind);
  const foreignFlow = ["deposit", "withdrawal", "transfer"].includes(
    selectedKind,
  );
  const reversed = new Set(
    history.data?.events
      .filter((e) => e.kind === "reverse")
      .map((e) => e.target_id),
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6 pb-10">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-widest text-primary-400 mb-2">
            Portfolio journal
          </p>
          <h1 className="text-2xl font-semibold flex items-center gap-3">
            <BookOpen className="h-6 w-6" />
            交易账本
          </h1>
          <p className="mt-2 text-sm text-slate-400">
            记录发生了什么，也记录当时为什么做这个决定。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            className={button}
            onClick={() => void refresh()}
            title="刷新账本"
          >
            <RefreshCw size={16} />
            刷新
          </button>
          <a className={button} href="/api/ledger/backup.sqlite3" download>
            <Download size={16} />
            备份
          </a>
        </div>
      </div>
      {query.isPending && <p role="status">正在读取账本…</p>}
      {query.isError && (
        <p role="alert" className="text-red-300">
          账本读取失败：{getApiErrorMessage(query.error)}{" "}
          <button className={button} onClick={() => void query.refetch()}>
            重试
          </button>
        </p>
      )}
      {error && (
        <div
          role="alert"
          className="rounded-lg border border-red-700 bg-red-950/40 p-4 text-red-200 break-words"
        >
          {error}
        </div>
      )}
      {message && (
        <div
          role="status"
          className="rounded-lg border border-emerald-700 bg-emerald-950/40 p-4 text-emerald-200"
        >
          {message}
        </div>
      )}
      {data && (
        <>
          {!accounts.length ? (
            <section className={box}>
              <h2 className="text-lg font-medium mb-2">
                从一份核对过的期初余额开始
              </h2>
              <p className="text-sm text-slate-400 mb-4">
                可以预览已有跟踪持仓，或在下方新建账户。确认后，每日快照将从账本读取持仓；旧快照仍保留。请先核对现金币种、数量和累计净投入。
              </p>
              <div className="flex flex-wrap gap-3 items-end">
                <Field title="期初日期">
                  <input
                    aria-label="期初日期"
                    type="date"
                    value={migrationDate}
                    onChange={(e) => setMigrationDate(e.target.value)}
                    className={input}
                  />
                </Field>
                <button
                  disabled={busy}
                  className={button}
                  onClick={() =>
                    void run(async () => {
                      setProposal(
                        (
                          await api.post<Proposal>("/ledger/opening-preview", {
                            date: migrationDate,
                          })
                        ).data,
                      );
                      await refresh();
                    })
                  }
                >
                  预览已有持仓
                </button>
              </div>
            </section>
          ) : (
            <section className="grid gap-3 lg:grid-cols-2">
              {Object.entries(data.accounts).map(([name, account]) => (
                <div key={name} className={box}>
                  <div className="flex justify-between gap-2 mb-4">
                    <h2 className="font-medium break-words">{name}</h2>
                    <span className="text-xs text-slate-400 shrink-0">
                      {Object.keys(account.positions).length} 项持仓
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-x-6 gap-y-3">
                    {Object.entries(account.cash_balances).map(
                      ([unit, value]) => (
                        <div key={unit}>
                          <p className="text-xs text-slate-400">{unit} 现金</p>
                          <p
                            className={`font-mono text-lg ${Number(value) < 0 ? "text-amber-300" : ""}`}
                          >
                            {num(value)}
                          </p>
                        </div>
                      ),
                    )}
                  </div>
                  <p className="text-xs text-slate-500 mt-4">
                    累计净投入 CNY {num(account.contributed_cny)} · 基金估值 CNY{" "}
                    {num(account.fund_cny)} · 数量及成本见流水下方
                  </p>
                </div>
              ))}
            </section>
          )}
          <div
            className="flex gap-2 border-b border-slate-700 pb-3"
            role="tablist"
            aria-label="账本视图"
          >
            {(["record", "history", "journal"] as const).map((t) => (
              <button
                role="tab"
                aria-selected={tab === t}
                key={t}
                onClick={() => setTab(t)}
                className={tab === t ? primary : button}
              >
                {
                  {
                    record: "录入与确认",
                    history: "流水与持仓",
                    journal: "研究复盘",
                  }[t]
                }
              </button>
            ))}
          </div>
          {tab === "record" && (
            <div className="grid gap-6 xl:grid-cols-2">
              <div className="space-y-5">
                <section className={box}>
                  <h2 className="font-medium mb-4">记录一笔变动</h2>
                  <form onSubmit={submit} className="space-y-4">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                      <Field title="类型">
                        <select
                          value={selectedKind}
                          onChange={(e) => setKind(e.target.value)}
                          className={input}
                        >
                          {Object.entries(kinds)
                            .filter(
                              ([key]) =>
                                key !== "reverse" &&
                                (accounts.length || key === "opening"),
                            )
                            .map(([key, name]) => (
                              <option key={key} value={key}>
                                {name}
                              </option>
                            ))}
                        </select>
                      </Field>
                      <Field title="发生日期">
                        <input
                          name="date"
                          type="date"
                          required
                          defaultValue={today()}
                          className={input}
                        />
                      </Field>
                    </div>
                    <Field title="账户">
                      {selectedKind === "opening" ? (
                        <input
                          name="account"
                          required
                          placeholder="例如：长期账户"
                          className={input}
                        />
                      ) : (
                        <select name="account" className={input}>
                          {accounts.map((a) => (
                            <option key={a}>{a}</option>
                          ))}
                        </select>
                      )}
                    </Field>
                    {selectedKind === "opening" && (
                      <>
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                          {["CNY", "HKD", "USD"].map((unit) => (
                            <Field key={unit} title={`${unit} 期初现金`}>
                              <input
                                name={unit}
                                type="number"
                                step="any"
                                defaultValue="0"
                                required
                                className={input}
                              />
                            </Field>
                          ))}
                        </div>
                        <Field title="累计净投入（CNY，按你的历史记录填写）">
                          <input
                            name="contributed_cny"
                            type="number"
                            step="any"
                            required
                            className={input}
                          />
                        </Field>
                        <Field title="基金期初估值（CNY，可选）">
                          <input
                            name="fund_cny"
                            type="number"
                            min="0"
                            step="any"
                            className={input}
                          />
                        </Field>
                        <details>
                          <summary className="text-sm text-slate-300 cursor-pointer">
                            添加期初股票（可选）
                          </summary>
                          {Array.from({ length: openingCount }, (_, i) => (
                            <div
                              key={i}
                              className="space-y-3 mt-3 border-t border-slate-700 pt-3"
                            >
                              <Field title={`期初股票 ${i + 1}`}>
                                <input
                                  name={`opening_ticker_${i}`}
                                  placeholder="NASDAQ:AAPL"
                                  className={input}
                                />
                              </Field>
                              <Field title="期初数量">
                                <input
                                  name={`opening_quantity_${i}`}
                                  type="number"
                                  step="any"
                                  min="0"
                                  className={input}
                                />
                              </Field>
                              <Field title="期初单位成本（原币）">
                                <input
                                  name={`opening_cost_${i}`}
                                  type="number"
                                  step="any"
                                  min="0"
                                  className={input}
                                />
                              </Field>
                            </div>
                          ))}
                          <button
                            type="button"
                            className={`${button} mt-3`}
                            disabled={openingCount >= 100}
                            onClick={() => setOpeningCount((n) => n + 1)}
                          >
                            添加另一项
                          </button>
                        </details>
                      </>
                    )}
                    {(trade || selectedKind === "split") && (
                      <Field title="交易所:股票代码">
                        <input
                          name="ticker"
                          required
                          placeholder="SHA:600519 / HKG:00700 / NASDAQ:AAPL"
                          className={input}
                        />
                      </Field>
                    )}
                    {(trade || cash) && (
                      <Field title="币种">
                        <select name="currency" className={input}>
                          {["CNY", "HKD", "USD"].map((c) => (
                            <option key={c}>{c}</option>
                          ))}
                        </select>
                      </Field>
                    )}
                    {trade && (
                      <>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                          <Field title="成交数量（支持碎股）">
                            <input
                              name="quantity"
                              type="number"
                              step="any"
                              min="0"
                              required
                              className={input}
                            />
                          </Field>
                          <Field title="成交价（原币）">
                            <input
                              name="price"
                              type="number"
                              step="any"
                              min="0"
                              required
                              className={input}
                            />
                          </Field>
                        </div>
                        <Field title="手续费（同币种）">
                          <input
                            name="fee"
                            type="number"
                            step="any"
                            min="0"
                            defaultValue="0"
                            required
                            className={input}
                          />
                        </Field>
                        {selectedKind === "buy" && (
                          <label className="flex items-start gap-2 text-sm text-slate-400">
                            <input
                              name="allow_margin"
                              type="checkbox"
                              className="mt-1"
                            />
                            允许本次买入使用融资，现金不足时记为负余额
                          </label>
                        )}
                      </>
                    )}
                    {(cash || fund) && (
                      <Field
                        title={
                          fund
                            ? "基金金额（CNY；估值调整填写调整后的合计）"
                            : "金额（原币）"
                        }
                      >
                        <input
                          name="amount"
                          type="number"
                          step="any"
                          min="0"
                          required
                          className={input}
                        />
                      </Field>
                    )}
                    {fund && selectedKind !== "fund_value" && (
                      <Field title="基金手续费（CNY）">
                        <input
                          name="fee"
                          type="number"
                          min="0"
                          step="any"
                          defaultValue="0"
                          required
                          className={input}
                        />
                      </Field>
                    )}
                    {foreignFlow && (
                      <Field title="折算汇率：1 单位外币 = 多少 CNY（人民币可留空）">
                        <input
                          name="fx_rate"
                          type="number"
                          step="any"
                          min="0"
                          className={input}
                        />
                      </Field>
                    )}
                    {selectedKind === "transfer" && (
                      <Field title="转入账户">
                        <select name="to_account" className={input}>
                          {accounts.map((a) => (
                            <option key={a}>{a}</option>
                          ))}
                        </select>
                      </Field>
                    )}
                    {selectedKind === "fx" && (
                      <>
                        <Field title="收到的币种">
                          <select name="to_currency" className={input}>
                            {["USD", "HKD", "CNY"].map((c) => (
                              <option key={c}>{c}</option>
                            ))}
                          </select>
                        </Field>
                        <Field title="实际到账金额（已扣除换汇费用）">
                          <input
                            name="received_amount"
                            type="number"
                            step="any"
                            min="0"
                            required
                            className={input}
                          />
                        </Field>
                      </>
                    )}
                    {selectedKind === "split" && (
                      <Field title="新股数 / 原股数（例如 2）">
                        <input
                          name="ratio"
                          type="number"
                          step="any"
                          min="0"
                          required
                          className={input}
                        />
                      </Field>
                    )}
                    <Field title="交易理由或备注（可选，最多 240 字）">
                      <input name="note" maxLength={240} className={input} />
                    </Field>
                    <button disabled={busy} className={primary} type="submit">
                      预览变化
                    </button>
                    <p className="text-xs text-slate-500">
                      预览不会改变余额。确认后才入账。
                    </p>
                  </form>
                </section>
                <section className={box}>
                  <h2 className="font-medium mb-2">批量导入 CSV</h2>
                  <p className="text-sm text-slate-400 mb-3">
                    先下载标准模板。相同来源和流水号会去重；相同流水号内容不同会报错。请为每个券商账户使用稳定、独立的来源名称。
                  </p>
                  <a
                    href="/api/ledger/template.csv"
                    download
                    className="text-primary-400 text-sm underline"
                  >
                    下载模板
                  </a>
                  <Field title="导入来源">
                    <input
                      value={source}
                      onChange={(e) => setSource(e.target.value)}
                      className={input}
                    />
                  </Field>
                  <label className={`${button} mt-3 cursor-pointer`}>
                    选择 CSV 并预览
                    <input
                      aria-label="导入 CSV"
                      type="file"
                      accept=".csv,text/csv"
                      className="sr-only"
                      disabled={busy}
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        e.target.value = "";
                        if (file)
                          void run(async () => {
                            if (file.size > 2000000)
                              throw new Error("CSV 不能超过 2 MB");
                            setProposal(
                              (
                                await api.post<Proposal>(
                                  "/ledger/csv-preview",
                                  { source, content: await file.text() },
                                )
                              ).data,
                            );
                            await refresh();
                          });
                      }}
                    />
                  </label>
                </section>
              </div>
              <div className="space-y-5">
                <section
                  className={`${box} xl:sticky xl:top-2`}
                  aria-label="确认预览"
                >
                  <div className="flex justify-between mb-4">
                    <h2 className="font-medium">核对后入账</h2>
                    <span className="text-xs text-slate-400">
                      当前版本 {data.revision}
                    </span>
                  </div>
                  {proposal ? (
                    <>
                      <p className="text-sm text-slate-400 mb-4">
                        {proposal.events.length} 笔待入账 · 跳过{" "}
                        {proposal.duplicates} 笔重复记录
                      </p>
                      <div className="max-h-48 overflow-auto mb-4 text-sm space-y-2">
                        {proposal.events.map((e) => (
                          <p key={e.id} className="break-words">
                            {e.date} · {kinds[e.kind]} ·{" "}
                            {e.account || "冲销历史记录"} {e.ticker}{" "}
                            {e.note && (
                              <span className="text-slate-400">— {e.note}</span>
                            )}
                          </p>
                        ))}
                      </div>
                      <Changes proposal={proposal} />
                      {proposal.warnings.map((w) => (
                        <p
                          key={w}
                          className="text-amber-300 text-sm mt-3 break-words"
                        >
                          {w}
                        </p>
                      ))}
                      {proposal.revision !== data.revision && (
                        <p role="alert" className="text-amber-300 text-sm mt-4">
                          账本已变化，此预览已过期。请重新录入或导入以核对最新差异。
                        </p>
                      )}
                      <div className="flex flex-wrap gap-2 mt-6">
                        <button
                          className={primary}
                          disabled={busy || proposal.revision !== data.revision}
                          onClick={() =>
                            void run(async () => {
                              const receipt = (
                                await api.post(
                                  `/ledger/proposals/${proposal.id}/confirm`,
                                )
                              ).data;
                              setMessage(
                                `已确认 ${receipt.applied} 笔，账本版本 ${receipt.revision}。重复点击不会再次入账。每日跟踪快照需另行更新行情后生成。`,
                              );
                              setProposal(null);
                              await refresh();
                            })
                          }
                        >
                          <Check size={16} />
                          确认入账
                        </button>
                        <button
                          className={button}
                          disabled={busy}
                          onClick={() =>
                            void run(async () => {
                              await api.delete(
                                `/ledger/proposals/${proposal.id}`,
                              );
                              setProposal(null);
                              await refresh();
                            })
                          }
                        >
                          放弃预览
                        </button>
                      </div>
                    </>
                  ) : (
                    <p className="text-slate-400 text-sm leading-relaxed">
                      提交一笔记录、导入流水，或选择下方 AI
                      生成的待确认方案。这里会展示每个账户的现金、股数和成本变化。
                    </p>
                  )}
                </section>
                {data.pending.length > 0 && (
                  <section className={box}>
                    <h2 className="font-medium mb-3">待确认方案</h2>
                    <div className="space-y-2">
                      {data.pending.map((p) => (
                        <div key={p.id} className="flex gap-2">
                          <button
                            className={`${button} flex-1 justify-start text-left`}
                            onClick={() => setProposal(p)}
                          >
                            {p.events[0]?.date || "重复导入"} ·{" "}
                            {p.events.length} 笔{" "}
                            {p.revision !== data.revision && "（已过期）"}
                          </button>
                          <button
                            aria-label="删除待确认方案"
                            className={button}
                            disabled={busy}
                            onClick={() =>
                              void run(async () => {
                                await api.delete(`/ledger/proposals/${p.id}`);
                                if (proposal?.id === p.id) setProposal(null);
                                await refresh();
                              })
                            }
                          >
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                  </section>
                )}
              </div>
            </div>
          )}
          {tab === "history" && (
            <div className="space-y-5">
              <section className={box}>
                <div className="flex justify-between gap-3 mb-4">
                  <h2 className="font-medium">已确认流水</h2>
                  <a
                    href="/api/ledger/export.json"
                    download
                    className="text-sm text-primary-400"
                  >
                    导出 JSON
                  </a>
                </div>
                {history.isError && (
                  <p role="alert">
                    流水读取失败{" "}
                    <button onClick={() => void history.refetch()}>重试</button>
                  </p>
                )}
                <div className="overflow-x-auto">
                  <table className="w-full text-sm text-left whitespace-nowrap">
                    <thead className="text-slate-400">
                      <tr>
                        {[
                          "日期",
                          "类型",
                          "账户 / 标的",
                          "数量 / 金额",
                          "状态",
                          "",
                        ].map((h, i) => (
                          <th key={i} className="py-3 pr-4 font-normal">
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {history.data?.events
                        .slice()
                        .reverse()
                        .map((e) => (
                          <tr key={e.id} className="border-t border-slate-700">
                            <td className="py-3 pr-4">{e.date}</td>
                            <td className="pr-4">{kinds[e.kind]}</td>
                            <td className="pr-4">
                              {e.account} {e.ticker}
                              <p
                                className="text-xs text-slate-500 max-w-64 truncate"
                                title={e.note}
                              >
                                {e.note}
                              </p>
                            </td>
                            <td className="font-mono pr-4">
                              {e.quantity
                                ? `${num(e.quantity)} × ${num(e.price)}`
                                : num(e.amount)}{" "}
                              {e.currency}
                            </td>
                            <td className="pr-4 text-slate-400">
                              {reversed.has(e.id) ? "已冲销" : "已确认"}
                            </td>
                            <td>
                              {e.kind !== "reverse" && !reversed.has(e.id) && (
                                <button
                                  className={button}
                                  disabled={busy}
                                  onClick={() =>
                                    void run(async () => {
                                      await preview([
                                        {
                                          kind: "reverse",
                                          date: today(),
                                          target_id: e.id,
                                        },
                                      ]);
                                      setTab("record");
                                    })
                                  }
                                >
                                  <Undo2 size={14} />
                                  预览冲销
                                </button>
                              )}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
                {!history.data?.events.length && (
                  <p className="text-sm text-slate-400 py-4">
                    确认第一笔记录后，流水会显示在这里。
                  </p>
                )}
              </section>
              {Object.entries(data.accounts).map(([name, account]) => (
                <section key={name} className={box}>
                  <h2 className="font-medium mb-4">{name} · 已确认持仓</h2>
                  <p className="text-xs text-slate-400 mb-3">
                    成本采用加权平均法，买入费用计入成本。以下不是实时市值。
                  </p>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm text-left whitespace-nowrap">
                      <thead className="text-slate-400">
                        <tr>
                          <th className="py-2 pr-4">标的</th>
                          <th className="pr-4">数量</th>
                          <th>单位成本（原币）</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.values(account.positions).map((p) => (
                          <tr
                            key={p.ticker}
                            className="border-t border-slate-700"
                          >
                            <td className="py-3 pr-4">{p.ticker}</td>
                            <td className="font-mono pr-4">
                              {num(p.quantity)}
                            </td>
                            <td className="font-mono">
                              {num(p.cost_price)} {p.currency}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              ))}
            </div>
          )}
          {tab === "journal" && (
            <div className="grid gap-6 lg:grid-cols-2">
              <section className={box}>
                <h2 className="font-medium mb-2">写给未来的自己</h2>
                <p className="text-sm text-slate-400 mb-4">
                  保留当时的判断，再用新记录补充复盘。研究记录随账本备份保存。
                </p>
                <form
                  className="space-y-4"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const form = e.currentTarget;
                    const f = new FormData(form);
                    void run(async () => {
                      await api.post("/ledger/journal", Object.fromEntries(f));
                      form.reset();
                      setMessage("研究记录已保存");
                      await refresh();
                    });
                  }}
                >
                  <Field title="标的">
                    <input
                      name="ticker"
                      required
                      placeholder="NASDAQ:AAPL"
                      className={input}
                    />
                  </Field>
                  <Field title="投资逻辑 / 本次复盘结论">
                    <textarea
                      name="thesis"
                      required
                      maxLength={4000}
                      rows={4}
                      className={input}
                    />
                  </Field>
                  <Field title="什么情况说明判断失效？">
                    <textarea
                      name="invalidation"
                      required
                      maxLength={4000}
                      rows={3}
                      className={input}
                    />
                  </Field>
                  <Field title="计划复查日期">
                    <input
                      name="review_on"
                      type="date"
                      required
                      className={input}
                    />
                  </Field>
                  <button className={primary} disabled={busy}>
                    保存研究记录
                  </button>
                </form>
              </section>
              <div className="space-y-4">
                {journal.isError && (
                  <p role="alert">
                    研究记录读取失败{" "}
                    <button onClick={() => void journal.refetch()}>重试</button>
                  </p>
                )}
                {journal.data?.entries.map((entry) => (
                  <article key={entry.id} className={box}>
                    <div className="flex flex-wrap justify-between gap-2 mb-3">
                      <h3 className="font-medium">{entry.ticker}</h3>
                      <span
                        className={`text-xs ${entry.review_on <= today() ? "text-amber-300" : "text-slate-400"}`}
                      >
                        复查 {entry.review_on}
                      </span>
                    </div>
                    <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">
                      {entry.thesis}
                    </p>
                    <p className="text-xs text-slate-500 mt-4 mb-1">
                      判断失效条件
                    </p>
                    <p className="whitespace-pre-wrap break-words text-sm text-slate-300">
                      {entry.invalidation}
                    </p>
                  </article>
                ))}
                {!journal.data?.entries.length && (
                  <p className="text-slate-400 text-sm p-4">
                    还没有研究记录。先写下判断，再安排复查。
                  </p>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
