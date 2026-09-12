import { useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { BookOpen, Download } from "lucide-react";
import type { LabRun } from "@/services/lab";
import { Panel, buttonClass } from "./LabUi";

export const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
export const finite = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const number = (value: unknown, digits = 4) =>
  finite(value) === null ? "—" : (value as number).toFixed(digits);
const strings = (value: unknown): string[] =>
  Array.isArray(value)
    ? value
        .map((x) =>
          typeof x === "string"
            ? x
            : String(record(x).message || record(x).description || ""),
        )
        .filter(Boolean)
    : [];
export function metric(
  run: LabRun,
  phase = "test",
  key = "model_mae",
): unknown {
  return record(record(run.result?.metrics)[phase])[key];
}

const methodNotes: Array<[string, string]> = [
  [
    "Research only:",
    "模拟采用可分割的多头仓位，未模拟整手、涨跌停排队、融资和券商成交。",
  ],
  [
    "Close-based labels",
    "收盘价标签用于预测；仓位在下一开盘价调整，标签收益不同于实际执行收益。",
  ],
  [
    "Overlapping forward labels",
    "相邻预测标签可能重叠，误差只作描述，不能直接当作统计显著性。",
  ],
  [
    "The final test interval",
    "测试集只有在不用于选择新方案时，才保持独立评价的意义。",
  ],
  ["Final positions", "期末按收盘价估值，未假设强制卖出及其退出费用。"],
  [
    "Historical adjustments",
    "复权修订和当前股票池可能带来偏差；本次输入版本已保留。",
  ],
  [
    "Aggregate is an equal-weight",
    "多股票曲线为共同测试日期的等权比较，未完整模拟跨股票调仓成本。",
  ],
];
function warningLabel(warning: string): string {
  const note = methodNotes.find(([prefix]) => warning.startsWith(prefix));
  if (note) return note[1];
  return warning
    .replace("missing OHLCV ratio", "行情字段缺失比例")
    .replace("zero-volume ratio", "零成交量比例")
    .replace("large calendar gap:", "最大数据日期间隔：")
    .replace(/ days$/, " 天");
}
const dataWarning = (warning: string) =>
  !methodNotes.some(([prefix]) => warning.startsWith(prefix)) &&
  /synthetic|missing|zero-volume|calendar gap|合成|缺失|数据异常/i.test(
    warning,
  );

function comparisonProtocol(run: LabRun): string {
  const manifest = record(run.result?.manifest),
    config = record(manifest.config);
  const finalTemplate = String(run.result?.template || run.request.template_id);
  const target =
    config.target ||
    (finalTemplate === "volatility"
      ? "forward_volatility"
      : ["momentum", "mean_reversion"].includes(finalTemplate)
        ? "forward_return"
        : finalTemplate);
  return JSON.stringify([
    manifest.input_hash ||
      record(run.result?.dataset).id ||
      run.request.dataset_id,
    target,
    config.horizon ?? run.request.params.horizon,
    config.train_fraction ?? run.request.params.train_ratio,
    config.validation_fraction ?? run.request.params.validation_ratio,
  ]);
}

export function ResultComparison({ runs }: { runs: LabRun[] }) {
  if (runs.length < 2) return null;
  const first = runs[0];
  const comparable = runs.every(
    (r) => comparisonProtocol(r) === comparisonProtocol(first),
  );
  return (
    <Panel
      title="实验比较"
      detail={
        comparable
          ? "相同数据版本、预测目标和时间划分。使用验证集比较方案，最终测试仅用于评价。"
          : "这些实验的数据、预测目标、预测期或时间划分不同，结果不宜直接排名。"
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full whitespace-nowrap text-left text-sm">
          <thead className="text-xs text-slate-400">
            <tr>
              <th className="pb-3 pr-5">研究</th>
              <th className="pb-3 pr-5">验证 MAE</th>
              <th className="pb-3 pr-5">测试 MAE</th>
              <th className="pb-3">测试相对基线改善</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id} className="border-t border-slate-700">
                <td
                  className="max-w-64 truncate py-3 pr-5"
                  title={run.request.objective}
                >
                  {run.request.objective}
                </td>
                <td className="pr-5 font-mono">
                  {number(metric(run, "validation"))}
                </td>
                <td className="pr-5 font-mono">{number(metric(run))}</td>
                <td className="font-mono">
                  {number(metric(run, "test", "improvement_pct"), 2)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

export default function LabResult({ run }: { run: LabRun }) {
  const [showMethod, setShowMethod] = useState(false);
  const result = run.result || {};
  const metrics = record(result.metrics);
  const test = record(metrics.test),
    validation = record(metrics.validation);
  const manifest = record(result.manifest);
  const equity = Array.isArray(result.equity)
    ? (result.equity as Array<Record<string, unknown>>)
    : [];
  const warnings = strings(result.warnings);
  const dataWarnings = warnings.filter(dataWarning);
  const assumptions = warnings.filter((warning) => !dataWarning(warning));
  const lessons = strings(result.lessons);
  const review = record(result.agent_review);
  const reviewText = ["summary", "assessment", "conclusion", "analysis"]
    .map((k) => review[k])
    .find((v) => typeof v === "string");
  const evidence = Array.isArray(review.evidence)
    ? review.evidence.map(record)
    : [];
  const limitations = strings(review.limitations);
  const experiments = Array.isArray(result.experiments)
    ? result.experiments.map(record)
    : [];
  return (
    <div className="space-y-4">
      <Panel
        title="最终评估"
        detail="参数选定并冻结后才查看测试集。一次好结果，需要跨时间和市场继续验证。"
        action={
          <a
            href={`/api/lab/runs/${encodeURIComponent(run.id)}/export`}
            download
            className={buttonClass}
          >
            <Download size={14} />
            导出完整研究
          </a>
        }
      >
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[
            ["验证误差 MAE", number(validation.model_mae), "用于比较候选方案"],
            ["测试误差 MAE", number(test.model_mae), "越低，预测越接近实际"],
            ["基线测试误差", number(test.baseline_mae), "与简单预测方法对照"],
            [
              "相对基线改善",
              finite(test.improvement_pct) === null
                ? "—"
                : `${number(test.improvement_pct, 2)}%`,
              "负值表示不及基线",
            ],
          ].map(([title, value, help]) => (
            <div
              key={title}
              className="rounded-xl border border-slate-700/70 bg-slate-950/70 p-3"
            >
              <div className="text-xs text-slate-400">{title}</div>
              <div className="my-2 font-mono text-xl font-medium tabular-nums text-slate-100">
                {value}
              </div>
              <p className="text-[11px] leading-5 text-slate-500">{help}</p>
            </div>
          ))}
        </div>
        {dataWarnings.length > 0 && (
          <div
            role="status"
            className="mt-4 space-y-2 rounded-xl border border-amber-500/20 bg-amber-500/5 p-3 text-xs leading-6 text-amber-200"
          >
            {dataWarnings.map((w, i) => (
              <p key={i}>{warningLabel(w)}</p>
            ))}
          </div>
        )}
        <p className="mt-3 text-xs leading-6 text-slate-500">
          验证样本 {finite(validation.samples) ?? "—"} · 测试样本{" "}
          {finite(test.samples) ?? "—"} · 误差单位与预测目标一致
        </p>
      </Panel>
      {equity.length > 1 && (
        <Panel
          title="测试期净值与基准"
          detail="同一测试区间，从 1 开始比较；模拟结果与真实成交可能不同。"
        >
          <div
            className="h-64 w-full min-w-0"
            role="img"
            aria-label="测试期策略与基准净值曲线"
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={equity}
                margin={{ top: 8, right: 8, left: -15, bottom: 0 }}
              >
                <CartesianGrid
                  vertical={false}
                  stroke="#253447"
                  strokeDasharray="3 4"
                />
                <XAxis
                  dataKey="date"
                  tick={{ fill: "#94a3b8", fontSize: 10 }}
                  minTickGap={35}
                  tickFormatter={(v) => String(v).slice(5, 10)}
                />
                <YAxis
                  domain={["auto", "auto"]}
                  tick={{ fill: "#94a3b8", fontSize: 10 }}
                  tickFormatter={(v) => Number(v).toFixed(2)}
                />
                <Tooltip
                  contentStyle={{
                    background: "#0f172a",
                    border: "1px solid #334155",
                    borderRadius: 12,
                    color: "#e2e8f0",
                  }}
                  formatter={(v: unknown) => number(v)}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line
                  type="linear"
                  dataKey="strategy"
                  name="策略（含成本）"
                  stroke="#67e8f9"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
                <Line
                  type="linear"
                  dataKey="benchmark"
                  name="基准"
                  stroke="#94a3b8"
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      )}
      <Panel
        title="理解这次研究"
        action={<BookOpen size={18} className="text-cyan-300" />}
      >
        <div className="space-y-3 text-sm leading-7 text-slate-300">
          {typeof reviewText === "string" && (
            <p className="whitespace-pre-wrap break-words">{reviewText}</p>
          )}
          {evidence.map((entry, i) => (
            <div key={i} className="border-l-2 border-cyan-400/30 pl-3">
              <p className="break-words">{String(entry.observation || "")}</p>
              <p className="break-all font-mono text-[11px] text-slate-500">
                证据实验：{String(entry.experiment_id || "")}
              </p>
            </div>
          ))}
          {limitations.map((text, i) => (
            <p key={`limit-${i}`} className="text-xs text-amber-200">
              {text}
            </p>
          ))}
          {lessons.map((lesson, i) => (
            <p key={i}>{lesson}</p>
          ))}
          {!lessons.length && !reviewText && (
            <p>
              MAE
              衡量预测与真实值之间的平均绝对差距。请把模型与简单基线放在同一数据和时间区间内比较，并关注测试集上改善是否仍然成立。
            </p>
          )}
          <p className="text-xs text-slate-500">
            预测准确度与交易收益是两个问题。换手、费用、持仓约束和行情质量都会改变实际效果。
          </p>
        </div>
      </Panel>
      {assumptions.length > 0 && (
        <details className="rounded-2xl border border-slate-700/70 bg-slate-900/80 p-4 sm:p-5">
          <summary className="cursor-pointer text-sm font-medium text-slate-200">
            方法假设与限制 · {assumptions.length} 项
            <span className="mt-2 block text-xs font-normal leading-6 text-slate-500">
              查看成交假设、标签依赖和样本偏差，理解模拟结果的适用范围。
            </span>
          </summary>
          <div className="mt-4 space-y-4 border-t border-slate-700 pt-4">
            {assumptions.map((warning, index) => (
              <div key={index}>
                <p className="text-xs leading-6 text-slate-300">
                  {warningLabel(warning)}
                </p>
                {warningLabel(warning) !== warning && (
                  <p className="mt-1 text-[11px] leading-5 text-slate-500">
                    {warning}
                  </p>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
      {experiments.length > 0 && (
        <Panel
          title="候选实验"
          detail="自动研究通过验证集反馈改进方案，测试结果不会反馈到本轮调参。"
        >
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm whitespace-nowrap">
              <thead className="text-xs text-slate-400">
                <tr>
                  <th className="pb-3 pr-4">实验</th>
                  <th className="pb-3 pr-4">模板 / 回看 / 预测期</th>
                  <th className="pb-3 pr-4">验证 MAE</th>
                  <th className="pb-3">状态</th>
                </tr>
              </thead>
              <tbody>
                {experiments.map((e, i) => {
                  const spec = record(e.spec),
                    val = record(record(record(e.result).metrics).validation);
                  return (
                    <tr
                      key={String(e.id || i)}
                      className="border-t border-slate-700"
                    >
                      <td className="py-3 pr-4">
                        {i + 1}
                        {e.id === result.selected_experiment && (
                          <span className="ml-2 text-xs text-cyan-300">
                            已选定
                          </span>
                        )}
                      </td>
                      <td className="pr-4">
                        {String(spec.template_id || spec.template || "—")} /{" "}
                        {String(spec.lookback ?? "—")} /{" "}
                        {String(spec.horizon ?? "—")}
                      </td>
                      <td className="font-mono pr-4">
                        {number(val.model_mae)}
                      </td>
                      <td
                        title={String(e.error || "")}
                        className="text-xs text-slate-400"
                      >
                        {e.status === "completed"
                          ? "完成"
                          : String(e.error || "失败")}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
      <Panel
        title="数据与方法"
        detail="保留本次输入和方法记录，便于复查与复现。"
      >
        <dl className="grid gap-3 text-xs sm:grid-cols-2">
          <div>
            <dt className="text-slate-500">数据来源</dt>
            <dd className="mt-1 break-words text-slate-300">
              {String(
                manifest.data_source || record(result.dataset).source || "—",
              )}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">算法版本</dt>
            <dd className="mt-1 break-words font-mono">
              {String(manifest.algorithm_version || "—")}
            </dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-slate-500">数据指纹</dt>
            <dd className="mt-1 break-all font-mono text-slate-400">
              {String(
                manifest.input_hash || record(result.dataset).hash || "—",
              )}
            </dd>
          </div>
        </dl>
        <button
          className={`${buttonClass} mt-4`}
          aria-expanded={showMethod}
          onClick={() => setShowMethod(!showMethod)}
        >
          {showMethod ? "收起" : "查看"}方法配置
        </button>
        {showMethod && (
          <pre className="mt-3 max-h-80 overflow-auto rounded-lg bg-slate-950 p-3 text-xs leading-6 text-slate-400">
            {JSON.stringify(manifest.config || run.request.params, null, 2)}
          </pre>
        )}
      </Panel>
    </div>
  );
}
