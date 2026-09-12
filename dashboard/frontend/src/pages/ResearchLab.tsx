import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import {
  ArrowRight,
  Beaker,
  BookOpen,
  Check,
  Database,
  History,
  Loader2,
  Play,
  RefreshCw,
  Settings2,
  Square,
} from "lucide-react";
import {
  defaultParams,
  isActiveRun,
  isBuiltinTemplate,
  templateParams,
  type JsonValue,
  labService,
  type LabDataset,
  type LabRequest,
  type LabRun,
} from "@/services/lab";
import { getApiErrorMessage } from "@/services/api";
import LabConnections from "@/components/lab/LabConnections";
import LabParameters from "@/components/lab/LabParameters";
import DataImport from "@/components/lab/LabDataImport";
import Schedules from "@/components/lab/LabSchedules";
import LabResult, {
  ResultComparison,
  record,
} from "@/components/lab/LabResult";
import {
  buttonClass,
  dateTime,
  Failure,
  Field,
  inputClass,
  Loading,
  Panel,
  primaryClass,
  Status,
} from "@/components/lab/LabUi";

const STORAGE = "portfolio-research-draft-v1";
const templateObjectives: Record<string, string> = {
  momentum: "近期价格动量能否改善未来收益预测？",
  mean_reversion: "近期价格偏离均值后，是否更容易出现收益回归？",
  volatility: "历史波动特征能否改善未来波动率预测？",
};
const genericObjective = "这个研究模板能否优于简单基线？";
function objectiveForTemplate(objective: string, templateId: string): string {
  const defaults = [
    ...Object.values(templateObjectives),
    genericObjective,
    "过去一段时间的价格动量，能否预测未来五个交易日的收益？",
  ];
  return defaults.includes(objective)
    ? templateObjectives[templateId] || genericObjective
    : objective;
}
const newDraft = (): LabRequest => ({
  mode: "manual",
  objective: templateObjectives.momentum,
  dataset_id: "",
  template_id: "momentum",
  params: { ...defaultParams },
  max_experiments: 3,
  timeout_seconds: 600,
});
function loadDraft(): LabRequest {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE) || "null");
    if (
      value &&
      ["manual", "agent"].includes(value.mode) &&
      typeof value.objective === "string"
    ) {
      const { client_request_id: _request, ...saved } = value;
      return {
        ...newDraft(),
        ...saved,
        params: isBuiltinTemplate(saved.template_id)
          ? { ...defaultParams, ...saved.params }
          : saved.params || {},
      };
    }
  } catch {
    /* A blocked or corrupt browser store must not prevent research. */
  }
  return newDraft();
}
function datasetParams(
  dataset: LabDataset,
  params: LabRequest["params"],
): LabRequest["params"] {
  const markets = dataset.symbols.map((symbol) =>
    /^(?:(?:SHA|SHE|SH|SZ):?)?\d{6}(?:\.(?:SH|SZ))?$/.test(symbol)
      ? "a_share"
      : /^HKG:|^\d{4,5}(?:\.HK)?$/.test(symbol)
        ? "hk"
        : "us",
  );
  return {
    ...params,
    ...(Object.prototype.hasOwnProperty.call(params, "market")
      ? {
          market: markets.every((m) => m === markets[0])
            ? (markets[0] as LabRequest["params"]["market"])
            : params.market,
        }
      : {}),
    ...(Object.prototype.hasOwnProperty.call(params, "adjustment")
      ? { adjustment: dataset.adjustment || params.adjustment }
      : {}),
  };
}
const stages: Record<string, string> = {
  queued: "等待执行",
  started: "开始研究",
  planning: "规划研究",
  planned: "方案已生成",
  experiment: "执行实验",
  evaluated: "检查验证结果",
  reviewing: "分析验证反馈",
  frozen: "冻结方案",
  reporting: "解释测试结果",
  reported: "整理报告",
  completed: "研究完成",
  failed: "研究失败",
  cancelled: "已取消",
};

export default function ResearchLab() {
  const client = useQueryClient();
  const [search, setSearch] = useSearchParams();
  const selectedId = search.get("run") || "";
  const [draft, setDraft] = useState<LabRequest>(loadDraft);
  const [view, setView] = useState<
    "work" | "history" | "learn" | "connections"
  >("work");
  const [showData, setShowData] = useState(false),
    [advanced, setAdvanced] = useState(false);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [storageWarning, setStorageWarning] = useState(false),
    [busy, setBusy] = useState(false);
  const [compared, setCompared] = useState<LabRun[]>([]);
  const submissionKey = useRef<{ payload: string; key: string } | null>(null);
  const templates = useQuery({
    queryKey: ["lab-templates"],
    queryFn: labService.templates,
  });
  const datasets = useQuery({
    queryKey: ["lab-datasets"],
    queryFn: labService.datasets,
  });
  const connections = useQuery({
    queryKey: ["lab-connections"],
    queryFn: labService.connections,
  });
  const runs = useQuery({
    queryKey: ["lab-runs"],
    queryFn: labService.runs,
    refetchInterval: 5000,
  });
  const selected = useQuery({
    queryKey: ["lab-run", selectedId],
    queryFn: () => labService.run(selectedId),
    enabled: !!selectedId,
    refetchInterval: (q) =>
      isActiveRun(q.state.data) || q.state.status === "error" ? 2500 : false,
    retry: 2,
  });
  const data = datasets.data?.find((d) => d.id === draft.dataset_id);
  const template = templates.data?.find((t) => t.id === draft.template_id);
  const builtin = isBuiltinTemplate(draft.template_id);
  const run = selected.data;
  const plan = record(run?.progress?.plan || run?.result?.plan);
  const hypotheses = Array.isArray(plan.hypotheses)
    ? plan.hypotheses.filter((v): v is string => typeof v === "string")
    : [];
  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        localStorage.setItem(STORAGE, JSON.stringify(draft));
        setStorageWarning(false);
      } catch {
        setStorageWarning(true);
      }
    }, 350);
    return () => window.clearTimeout(timer);
  }, [draft]);
  useEffect(() => {
    if (!draft.dataset_id && datasets.data?.length)
      setDraft((d) => ({
        ...d,
        dataset_id: datasets.data![0].id,
        params: datasetParams(datasets.data![0], d.params),
      }));
  }, [datasets.data, draft.dataset_id]);
  useEffect(() => {
    if (run?.status === "completed" || run?.status === "failed")
      void client.invalidateQueries({ queryKey: ["lab-runs"] });
  }, [run?.status, client]);
  function selectRun(id: string) {
    setSearch({ run: id });
    setView("work");
  }
  function param(
    key: keyof LabRequest["params"],
    value: JsonValue | undefined,
  ) {
    setDraft((d) => ({ ...d, params: { ...d.params, [key]: value } }));
  }
  async function submit() {
    setError("");
    setNotice("");
    setBusy(true);
    const payload = JSON.stringify(draft);
    if (submissionKey.current?.payload !== payload)
      submissionKey.current = {
        payload,
        key: `lab-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      };
    try {
      const created = await labService.submit({
        ...draft,
        connection_id: draft.mode === "agent" ? draft.connection_id : undefined,
        client_request_id: submissionKey.current.key,
      });
      selectRun(created.id);
      submissionKey.current = null;
      await client.invalidateQueries({ queryKey: ["lab-runs"] });
      setNotice("研究已提交。可以离开页面，之后从实验记录继续查看。");
    } catch (e) {
      setError(getApiErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function action(kind: "cancel" | "retry") {
    if (!run) return;
    setBusy(true);
    setError("");
    try {
      const result = await labService[kind](run.id);
      if (kind === "retry") selectRun(result.id);
      await Promise.all([
        selected.refetch(),
        client.invalidateQueries({ queryKey: ["lab-runs"] }),
      ]);
    } catch (e) {
      setError(getApiErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const missingDataset = !!draft.dataset_id && datasets.isSuccess && !data;
  const canRun =
    !!data &&
    !!template &&
    draft.objective.trim().length >= 4 &&
    (draft.mode === "manual" ||
      !!connections.data?.find((c) => c.id === draft.connection_id));
  return (
    <div className="mx-auto max-w-[1560px] space-y-5 text-slate-100">
      <header className="flex flex-wrap items-end justify-between gap-5 border-b border-slate-700/80 pb-6 pt-2">
        <div>
          <p className="mb-3 font-mono text-[11px] uppercase tracking-[0.2em] text-cyan-300">
            Research / Learn / Iterate
          </p>
          <h1 className="flex items-center gap-3 text-2xl font-semibold tracking-tight sm:text-3xl">
            <Beaker size={27} className="text-cyan-300" />
            股票研究工作台
          </h1>
          <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-400">
            把一个问题变成可检查的实验。手动探索，或让 Agent
            完成规划、验证与复盘。
          </p>
        </div>
        <Link className={buttonClass} to="/backtest">
          经典回测工具
          <ArrowRight size={14} />
        </Link>
      </header>
      <nav aria-label="研究工作台视图" className="flex flex-wrap gap-2">
        {(
          [
            { id: "work", label: "研究实验", icon: Beaker },
            { id: "history", label: "实验记录", icon: History },
            { id: "learn", label: "方法与学习", icon: BookOpen },
            { id: "connections", label: "接入与安排", icon: Settings2 },
          ] as const
        ).map((t) => (
          <button
            key={t.id}
            className={`${buttonClass} ${view === t.id ? "border-cyan-500/50 bg-cyan-500/10 text-cyan-200" : ""}`}
            aria-current={view === t.id ? "page" : undefined}
            onClick={() => setView(t.id)}
          >
            <t.icon size={15} />
            {t.label}
          </button>
        ))}
      </nav>
      {error && <Failure message={error} />}
      {notice && (
        <p
          role="status"
          className="rounded-xl border border-cyan-500/20 bg-cyan-500/5 p-3 text-sm leading-6 text-cyan-100"
        >
          {notice}
        </p>
      )}
      {storageWarning && (
        <Failure message="浏览器无法保存草稿。当前编辑仍然可用，请在离开页面前提交实验。" />
      )}
      {view === "work" && (
        <div className="grid items-start gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
          <div className="space-y-4">
            <Panel
              title="研究配置"
              detail="草稿保存在当前浏览器，切换页面后可以继续。"
            >
              <form
                className="space-y-5"
                onSubmit={(e) => {
                  e.preventDefault();
                  void submit();
                }}
              >
                <div
                  className="grid grid-cols-2 gap-2"
                  role="group"
                  aria-label="研究模式"
                >
                  {(["manual", "agent"] as const).map((mode) => (
                    <button
                      type="button"
                      key={mode}
                      aria-pressed={draft.mode === mode}
                      onClick={() => setDraft((d) => ({ ...d, mode }))}
                      className={`rounded-xl border p-3 text-left ${draft.mode === mode ? "border-cyan-400/50 bg-cyan-400/10" : "border-slate-700 bg-slate-950"}`}
                    >
                      <span className="block text-sm font-medium">
                        {mode === "manual" ? "手动实验" : "Agent 自动研究"}
                      </span>
                      <span className="mt-1 block text-[11px] leading-5 text-slate-500">
                        {mode === "manual"
                          ? "自己设定，运行并解释"
                          : "自主规划，有限迭代"}
                      </span>
                    </button>
                  ))}
                </div>
                <Field label="想研究的问题">
                  <textarea
                    className={`${inputClass} resize-y leading-6`}
                    rows={3}
                    required
                    minLength={4}
                    maxLength={2000}
                    value={draft.objective}
                    onChange={(e) =>
                      setDraft((d) => ({ ...d, objective: e.target.value }))
                    }
                  />
                </Field>
                <Field label="研究数据">
                  <select
                    className={inputClass}
                    required
                    value={draft.dataset_id}
                    onChange={(e) => {
                      const ds = datasets.data?.find(
                        (x) => x.id === e.target.value,
                      );
                      setDraft((d) => ({
                        ...d,
                        dataset_id: e.target.value,
                        params: ds ? datasetParams(ds, d.params) : d.params,
                      }));
                    }}
                  >
                    <option value="">选择一个数据集</option>
                    {datasets.data?.map((ds) => (
                      <option key={ds.id} value={ds.id}>
                        {ds.name}
                        {ds.synthetic ? " · 演示" : ""}
                      </option>
                    ))}
                  </select>
                </Field>
                {datasets.isError && (
                  <Failure
                    message={getApiErrorMessage(datasets.error)}
                    retry={() => void datasets.refetch()}
                  />
                )}
                {missingDataset && (
                  <Failure message="草稿中的数据集已不可用，请重新选择。" />
                )}
                {data && (
                  <div className="rounded-xl border border-slate-700 bg-slate-950/60 p-3 text-xs leading-6 text-slate-400">
                    <p className="break-words">{data.symbols.join(" · ")}</p>
                    <p>
                      {data.start} → {data.end}
                    </p>
                    <p>
                      {data.rows.toLocaleString()} 行 · {data.source}
                      {data.synthetic && (
                        <span className="ml-2 text-amber-300">
                          合成演示，仅供学习
                        </span>
                      )}
                    </p>
                  </div>
                )}
                {builtin && (
                  <Field
                    label="研究市场"
                    help="按标的代码推测，请核对；多市场数据建议分别研究。"
                  >
                    <select
                      className={inputClass}
                      value={draft.params.market}
                      onChange={(e) => param("market", e.target.value)}
                    >
                      <option value="a_share">A 股</option>
                      <option value="hk">港股</option>
                      <option value="us">美股</option>
                    </select>
                  </Field>
                )}
                <button
                  type="button"
                  className={`${buttonClass} w-full`}
                  onClick={() => setShowData(!showData)}
                >
                  <Database size={15} />
                  {showData ? "收起数据导入" : "添加股票行情 / CSV"}
                </button>
                <Field label="研究模板">
                  <select
                    className={inputClass}
                    value={draft.template_id}
                    onChange={(e) => {
                      const t = templates.data?.find(
                        (t) => t.id === e.target.value,
                      );
                      setDraft((d) => ({
                        ...d,
                        template_id: e.target.value,
                        objective: objectiveForTemplate(
                          d.objective,
                          e.target.value,
                        ),
                        params: t ? templateParams(t, d.params) : {},
                      }));
                    }}
                  >
                    {templates.data?.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </Field>
                {templates.isError && (
                  <Failure
                    message={getApiErrorMessage(templates.error)}
                    retry={() => void templates.refetch()}
                  />
                )}
                {template && (
                  <p className="text-xs leading-6 text-slate-400">
                    {template.description}
                  </p>
                )}
                {builtin && (
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="历史回看（交易日）">
                      <input
                        className={inputClass}
                        type="number"
                        min={5}
                        max={120}
                        required
                        value={draft.params.lookback}
                        onChange={(e) =>
                          param("lookback", Number(e.target.value))
                        }
                      />
                    </Field>
                    <Field label="预测期（交易日）">
                      <input
                        className={inputClass}
                        type="number"
                        min={1}
                        max={30}
                        required
                        value={draft.params.horizon}
                        onChange={(e) =>
                          param("horizon", Number(e.target.value))
                        }
                      />
                    </Field>
                  </div>
                )}
                {!builtin && template && (
                  <LabParameters
                    key={template.id}
                    schema={template.parameters || {}}
                    values={draft.params}
                    onChange={param}
                  />
                )}
                {draft.mode === "agent" && (
                  <div className="space-y-3 rounded-xl border border-cyan-500/20 bg-cyan-500/5 p-3">
                    <Field label="Agent 模型连接">
                      <select
                        className={inputClass}
                        required
                        value={draft.connection_id || ""}
                        onChange={(e) =>
                          setDraft((d) => ({
                            ...d,
                            connection_id: e.target.value,
                          }))
                        }
                      >
                        <option value="">选择模型连接</option>
                        {connections.data?.map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.name}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <button
                      type="button"
                      className="text-xs text-cyan-300 underline underline-offset-4"
                      onClick={() => setView("connections")}
                    >
                      配置 API / 官方客户端接入
                    </button>
                    <div className="grid grid-cols-2 gap-3">
                      <Field label="最多实验次数">
                        <input
                          className={inputClass}
                          type="number"
                          required
                          min={1}
                          max={6}
                          value={draft.max_experiments}
                          onChange={(e) =>
                            setDraft((d) => ({
                              ...d,
                              max_experiments: Number(e.target.value),
                            }))
                          }
                        />
                      </Field>
                      <Field label="总时限（秒）">
                        <input
                          className={inputClass}
                          type="number"
                          required
                          min={30}
                          max={1800}
                          value={draft.timeout_seconds}
                          onChange={(e) =>
                            setDraft((d) => ({
                              ...d,
                              timeout_seconds: Number(e.target.value),
                            }))
                          }
                        />
                      </Field>
                    </div>
                    <p className="text-xs leading-6 text-slate-400">
                      启动后自主规划与迭代。候选方案只依据验证集调整，冻结后再查看测试集；随时可以取消。
                    </p>
                  </div>
                )}
                {builtin && (
                  <button
                    type="button"
                    className="flex items-center gap-2 text-xs text-slate-400"
                    aria-expanded={advanced}
                    onClick={() => setAdvanced(!advanced)}
                  >
                    <Settings2 size={14} />
                    费用、时间划分与复现参数
                  </button>
                )}
                {builtin && advanced && (
                  <div className="grid grid-cols-2 gap-3">
                    {[
                      {
                        key: "fee_bps",
                        label: "手续费（基点）",
                        min: 0,
                        max: 200,
                        step: 1,
                      },
                      {
                        key: "slippage_bps",
                        label: "滑点（基点）",
                        min: 0,
                        max: 200,
                        step: 1,
                      },
                      {
                        key: "train_ratio",
                        label: "训练集比例",
                        min: 0.4,
                        max: 0.8,
                        step: 0.05,
                      },
                      {
                        key: "validation_ratio",
                        label: "验证集比例",
                        min: 0.1,
                        max: 0.3,
                        step: 0.05,
                      },
                      {
                        key: "seed",
                        label: "随机种子",
                        min: 0,
                        max: 2147483647,
                        step: 1,
                      },
                    ].map((p) => (
                      <Field key={p.key} label={p.label}>
                        <input
                          className={inputClass}
                          type="number"
                          required
                          min={p.min}
                          max={p.max}
                          step={p.step}
                          value={String(draft.params[p.key] ?? "")}
                          onChange={(e) =>
                            param(
                              p.key as keyof typeof draft.params,
                              Number(e.target.value),
                            )
                          }
                        />
                      </Field>
                    ))}
                    <Field label="数据复权声明">
                      <select
                        className={inputClass}
                        value={draft.params.adjustment}
                        onChange={(e) => param("adjustment", e.target.value)}
                      >
                        <option value="qfq">前复权</option>
                        <option value="hfq">后复权</option>
                        <option value="none">不复权</option>
                      </select>
                    </Field>
                    <p className="col-span-2 text-xs leading-6 text-slate-500">
                      1 个基点 = 0.01%。测试集至少保留
                      10%；时间划分后还会隔离跨区间标签。
                    </p>
                  </div>
                )}
                <button
                  className={`${primaryClass} w-full py-3`}
                  disabled={busy || !canRun}
                >
                  {busy ? (
                    <Loader2 size={16} className="animate-spin" />
                  ) : (
                    <Play size={16} />
                  )}
                  {draft.mode === "agent" ? "启动自动研究" : "运行手动实验"}
                </button>
                <p className="text-center text-[11px] text-slate-500">
                  仅运行研究实验，结果用于学习与复盘。
                </p>
              </form>
            </Panel>
            {showData && (
              <DataImport
                onImported={(ds) => {
                  setDraft((d) => ({
                    ...d,
                    dataset_id: ds.id,
                    params: datasetParams(ds, d.params),
                  }));
                  setShowData(false);
                  setNotice(`已选择数据集：${ds.name}`);
                }}
              />
            )}
          </div>
          <div className="min-w-0 space-y-4">
            {!selectedId && (
              <Panel
                title="从一个可验证的问题开始"
                detail="选择数据和研究模板，几分钟内完成第一轮实验。"
              >
                <div className="space-y-6 py-4">
                  {[
                    [
                      "01",
                      "定义问题",
                      "明确预测什么、预测多远，再选取覆盖足够时间的行情。",
                    ],
                    [
                      "02",
                      "验证假设",
                      "按时间切分数据，与简单基线比较；计入费用与滑点。",
                    ],
                    [
                      "03",
                      "记录证据",
                      "保存输入版本、实验配置和结果，理解改善在哪里发生。",
                    ],
                  ].map(([n, title, text]) => (
                    <div key={n} className="flex gap-4">
                      <span className="font-mono text-2xl text-slate-600">
                        {n}
                      </span>
                      <div>
                        <h3 className="text-sm font-medium">{title}</h3>
                        <p className="mt-2 text-sm leading-7 text-slate-400">
                          {text}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
                {runs.data?.[0] && (
                  <button
                    className={buttonClass}
                    onClick={() => selectRun(runs.data![0].id)}
                  >
                    <History size={15} />
                    继续查看最近的实验
                  </button>
                )}
              </Panel>
            )}
            {selectedId && selected.isPending && (
              <Loading text="正在读取研究进度…" />
            )}
            {selected.isError && (
              <Failure
                message={`研究进度暂时无法连接，系统会继续尝试恢复。${getApiErrorMessage(selected.error)}`}
                retry={() => void selected.refetch()}
              />
            )}
            {run && (
              <Panel
                title={run.request.objective}
                detail={`${run.request.mode === "agent" ? "Agent 自动研究" : "手动实验"} · ${dateTime(run.created_at)}`}
                action={<Status value={run.status} />}
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="text-sm text-slate-300">
                    {stages[run.stage] || run.stage}
                    {run.progress && (
                      <p className="mt-2 text-xs text-slate-500">
                        已完成 {run.progress.experiments_completed} 个实验 ·{" "}
                        {run.request.mode === "manual"
                          ? "无需模型调用"
                          : `模型调用 ${run.progress.model_calls}${
                              run.progress.max_model_calls
                                ? ` / ${run.progress.max_model_calls}`
                                : ""
                            }`}
                        {run.progress.frozen ? " · 方案已冻结" : ""}
                      </p>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {isActiveRun(run) && (
                      <button
                        className={buttonClass}
                        disabled={busy}
                        onClick={() => void action("cancel")}
                      >
                        <Square size={13} />
                        取消研究
                      </button>
                    )}
                    {["failed", "cancelled"].includes(run.status) && (
                      <button
                        className={buttonClass}
                        disabled={busy}
                        onClick={() => void action("retry")}
                      >
                        <RefreshCw size={14} />
                        重新运行
                      </button>
                    )}
                    <button
                      className={buttonClass}
                      onClick={() => {
                        const { client_request_id: _key, ...rest } =
                          run.request;
                        setDraft(rest);
                        setNotice(
                          "已载入这次研究的配置，可以调整后创建新实验。",
                        );
                      }}
                    >
                      载入配置
                    </button>
                  </div>
                </div>
                {run.error && (
                  <div className="mt-4">
                    <Failure message={run.error} />
                  </div>
                )}
                <ol
                  aria-label="研究进度事件"
                  aria-live="polite"
                  className="mt-5 max-h-72 space-y-3 overflow-y-auto border-t border-slate-700 pt-4"
                >
                  {run.events?.map((event) => (
                    <li
                      key={event.seq}
                      className="flex gap-3 text-xs leading-6"
                    >
                      <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-cyan-300/70" />
                      <span className="shrink-0 font-mono text-slate-500">
                        {dateTime(event.created_at)}
                      </span>
                      <span className="min-w-0 break-words text-slate-300">
                        {event.message}
                      </span>
                    </li>
                  ))}
                </ol>
              </Panel>
            )}
            {hypotheses.length > 0 && (
              <Panel
                title="Agent 的研究假设"
                detail="根据当前问题形成假设，再使用实验结果检验。"
              >
                <div className="space-y-3">
                  {hypotheses.map((text, index) => (
                    <p key={index} className="text-sm leading-7 text-slate-300">
                      <span className="mr-3 font-mono text-cyan-300">
                        {index + 1}.
                      </span>
                      {text}
                    </p>
                  ))}
                </div>
              </Panel>
            )}
            {run?.result && <LabResult run={run} />}
          </div>
        </div>
      )}
      {view === "history" && (
        <div className="space-y-4">
          <Panel
            title="实验记录"
            detail="记录持续保存在服务端。选择结果进行比较，或载入一次研究继续探索。"
            action={
              <button
                className={buttonClass}
                onClick={() => void runs.refetch()}
              >
                <RefreshCw size={14} />
                刷新
              </button>
            }
          >
            {runs.isPending && <Loading />}
            {runs.isError && (
              <Failure
                message={getApiErrorMessage(runs.error)}
                retry={() => void runs.refetch()}
              />
            )}
            {runs.isSuccess && !runs.data.length && (
              <p className="py-8 text-sm text-slate-400">
                还没有实验。运行第一项研究后，结果会保存在这里。
              </p>
            )}
            <div className="space-y-3">
              {runs.data?.map((r) => (
                <article
                  key={r.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-700 p-4"
                >
                  <button
                    className="min-w-0 flex-1 text-left"
                    onClick={() => selectRun(r.id)}
                  >
                    <h3 className="break-words text-sm font-medium">
                      {r.request.objective}
                    </h3>
                    <p className="mt-2 text-xs text-slate-500">
                      {dateTime(r.created_at)} ·{" "}
                      {r.request.mode === "agent" ? "Agent" : "手动"} ·{" "}
                      {r.request.template_id}
                    </p>
                  </button>
                  <Status value={r.status} />
                  {r.status === "completed" && (
                    <button
                      className={buttonClass}
                      disabled={
                        busy ||
                        (!compared.some((x) => x.id === r.id) &&
                          compared.length >= 4)
                      }
                      onClick={() => {
                        if (compared.some((x) => x.id === r.id))
                          setCompared((prev) =>
                            prev.filter((x) => x.id !== r.id),
                          );
                        else {
                          setBusy(true);
                          void labService
                            .run(r.id)
                            .then((detail) =>
                              setCompared((prev) => [...prev, detail]),
                            )
                            .catch((e) => setError(getApiErrorMessage(e)))
                            .finally(() => setBusy(false));
                        }
                      }}
                    >
                      {compared.some((x) => x.id === r.id) && (
                        <Check size={14} />
                      )}
                      比较
                    </button>
                  )}
                </article>
              ))}
            </div>
          </Panel>
          <ResultComparison runs={compared} />
        </div>
      )}
      {view === "learn" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel
            title="研究不是寻找最好看的收益曲线"
            detail="把本轮学习目标拆成几个能检查的问题。"
          >
            <div className="space-y-5 text-sm leading-7 text-slate-300">
              <p>
                <strong className="text-cyan-200">训练集</strong>用来拟合模型；
                <strong className="text-cyan-200">验证集</strong>
                比较参数与方案；
                <strong className="text-cyan-200">测试集</strong>
                在冻结方案后提供最终评价。
              </p>
              <p>
                标签使用未来收益或未来波动率，所以时间边界附近的样本必须隔离，避免把未来的信息混进模型。
              </p>
              <p>
                模型应首先超过简单基线，再讨论交易效果。误差较低不必然带来高收益；交易频率和成本会改变结果。
              </p>
              <p>
                自动研究在明确的实验次数与时限内运行。保存失败结果同样有价值：它们告诉你哪些假设不成立。
              </p>
            </div>
          </Panel>
          <div className="space-y-4">
            {templates.data?.map((t) => (
              <Panel key={t.id} title={t.name} detail={t.description}>
                <p className="text-sm leading-7 text-slate-300">
                  {t.lesson ||
                    (t.target === "forward_volatility"
                      ? "研究未来波动的大小；这类目标不直接预测价格涨跌方向。"
                      : "研究未来收益方向与幅度，重点检查样本外预测是否优于基线。")}
                </p>
                <button
                  className={`${buttonClass} mt-4`}
                  onClick={() => {
                    setDraft((d) => ({
                      ...d,
                      template_id: t.id,
                      objective: objectiveForTemplate(d.objective, t.id),
                      params: templateParams(t, d.params),
                    }));
                    setView("work");
                  }}
                >
                  用这个模板开始
                  <ArrowRight size={14} />
                </button>
              </Panel>
            ))}
          </div>
        </div>
      )}
      {view === "connections" && (
        <div className="space-y-5">
          <LabConnections />
          <Schedules draft={draft} canRefresh={Boolean(data?.selection)} />
        </div>
      )}
    </div>
  );
}
