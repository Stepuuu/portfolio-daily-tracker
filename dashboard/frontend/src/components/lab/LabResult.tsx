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
import { UNAVAILABLE } from "@/lib/format";
import { useTokens } from "@/components/ui";
import { Panel, buttonClass } from "./LabUi";

export const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
export const finite = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const number = (value: unknown, digits = 4) =>
  finite(value) === null ? UNAVAILABLE : (value as number).toFixed(digits);
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
        <table className="w-full whitespace-nowrap text-left text-table">
          <thead className="text-caption text-fg-3">
            <tr>
              <th className="pb-3 pr-5">研究</th>
              <th className="pb-3 pr-5">验证 MAE</th>
              <th className="pb-3 pr-5">测试 MAE</th>
              <th className="pb-3">测试相对基线改善</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id} className="border-t border-line">
                <td
                  className="max-w-64 truncate py-3 pr-5"
                  title={run.request.objective}
                >
                  {run.request.objective}
                </td>
                <td className="pr-5 tabular-nums">
                  {number(metric(run, "validation"))}
                </td>
                <td className="pr-5 tabular-nums">{number(metric(run))}</td>
                <td className="tabular-nums">
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
  const tokens = useTokens();
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
                ? UNAVAILABLE
                : `${number(test.improvement_pct, 2)}%`,
              "负值表示不及基线",
            ],
          ].map(([title, value, help]) => (
            <div
              key={title}
              className="rounded-lg border border-line bg-bg p-3"
            >
              <div className="text-caption text-fg-3">{title}</div>
              <div className="my-2 text-page tabular-nums text-fg">
                {value}
              </div>
              <p className="text-caption leading-5 text-fg-3">{help}</p>
            </div>
          ))}
        </div>
        {dataWarnings.length > 0 && (
          <div
            role="status"
            className="mt-4 space-y-2 rounded-md bg-warn/10 px-3 py-2 text-caption leading-6 text-fg"
          >
            {dataWarnings.map((w, i) => (
              <p key={i}>{warningLabel(w)}</p>
            ))}
          </div>
        )}
        <p className="mt-3 text-caption leading-6 text-fg-3">
          验证样本 {finite(validation.samples) ?? UNAVAILABLE} · 测试样本{" "}
          {finite(test.samples) ?? UNAVAILABLE} · 误差单位与预测目标一致
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
                  stroke={tokens.border}
                  strokeDasharray="3 4"
                />
                <XAxis
                  dataKey="date"
                  tick={{ fill: tokens["text-3"], fontSize: 10 }}
                  stroke={tokens.border}
                  minTickGap={35}
                  tickFormatter={(v) => String(v).slice(5, 10)}
                />
                <YAxis
                  domain={["auto", "auto"]}
                  tick={{ fill: tokens["text-3"], fontSize: 10 }}
                  stroke={tokens.border}
                  tickFormatter={(v) => Number(v).toFixed(2)}
                />
                <Tooltip
                  contentStyle={{
                    background: tokens.surface,
                    border: `1px solid ${tokens.border}`,
                    borderRadius: 6,
                    color: tokens.text,
                  }}
                  formatter={(v: unknown) => number(v)}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line
                  type="linear"
                  dataKey="strategy"
                  name="策略（含成本）"
                  stroke={tokens.accent}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
                <Line
                  type="linear"
                  dataKey="benchmark"
                  name="基准"
                  stroke={tokens["text-3"]}
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
        action={<BookOpen size={18} className="text-fg-3" aria-hidden />}
      >
        <div className="space-y-3 text-body leading-7 text-fg-2">
          {typeof reviewText === "string" && (
            <p className="whitespace-pre-wrap break-words">{reviewText}</p>
          )}
          {evidence.map((entry, i) => (
            <div key={i} className="border-l-2 border-accent/30 pl-3">
              <p className="break-words">{String(entry.observation || "")}</p>
              <p className="break-all tabular-nums text-caption text-fg-3">
                证据实验：{String(entry.experiment_id || "")}
              </p>
            </div>
          ))}
          {limitations.map((text, i) => (
            <p key={`limit-${i}`} className="text-caption text-warn">
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
          <p className="text-caption text-fg-3">
            预测准确度与交易收益是两个问题。换手、费用、持仓约束和行情质量都会改变实际效果。
          </p>
        </div>
      </Panel>
      {assumptions.length > 0 && (
        <details className="rounded-lg border border-line bg-surface p-4 sm:p-5">
          <summary className="cursor-pointer text-body font-medium text-fg">
            方法假设与限制 · {assumptions.length} 项
            <span className="mt-2 block text-caption font-normal leading-6 text-fg-3">
              查看成交假设、标签依赖和样本偏差，理解模拟结果的适用范围。
            </span>
          </summary>
          <div className="mt-4 space-y-4 border-t border-line pt-4">
            {assumptions.map((warning, index) => (
              <div key={index}>
                <p className="text-caption leading-6 text-fg-2">
                  {warningLabel(warning)}
                </p>
                {warningLabel(warning) !== warning && (
                  <p className="mt-1 text-caption leading-5 text-fg-3">
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
            <table className="w-full whitespace-nowrap text-left text-table">
              <thead className="text-caption text-fg-3">
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
                      className="border-t border-line"
                    >
                      <td className="py-3 pr-4">
                        {i + 1}
                        {e.id === result.selected_experiment && (
                          <span className="ml-2 text-caption text-accent">
                            已选定
                          </span>
                        )}
                      </td>
                      <td className="pr-4">
                        {String(spec.template_id || spec.template || UNAVAILABLE)} /{" "}
                        {String(spec.lookback ?? UNAVAILABLE)} /{" "}
                        {String(spec.horizon ?? UNAVAILABLE)}
                      </td>
                      <td className="tabular-nums pr-4">
                        {number(val.model_mae)}
                      </td>
                      <td
                        title={String(e.error || "")}
                        className="text-caption text-fg-3"
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
        <dl className="grid gap-3 text-caption sm:grid-cols-2">
          <div>
            <dt className="text-fg-3">数据来源</dt>
            <dd className="mt-1 break-words text-fg-2">
              {String(
                manifest.data_source || record(result.dataset).source || UNAVAILABLE,
              )}
            </dd>
          </div>
          <div>
            <dt className="text-fg-3">算法版本</dt>
            <dd className="mt-1 break-words tabular-nums">
              {String(manifest.algorithm_version || UNAVAILABLE)}
            </dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-fg-3">数据指纹</dt>
            <dd className="mt-1 break-all tabular-nums text-fg-3">
              {String(
                manifest.input_hash || record(result.dataset).hash || UNAVAILABLE,
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
          <pre className="mt-3 max-h-80 overflow-auto rounded-lg bg-bg p-3 text-caption leading-6 text-fg-3">
            {JSON.stringify(manifest.config || run.request.params, null, 2)}
          </pre>
        )}
      </Panel>
    </div>
  );
}
