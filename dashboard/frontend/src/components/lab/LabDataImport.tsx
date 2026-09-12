import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Database, Loader2, Upload } from "lucide-react";
import { labService, type LabDataset } from "@/services/lab";
import { getApiErrorMessage } from "@/services/api";
import {
  buttonClass,
  Failure,
  Field,
  inputClass,
  Panel,
  primaryClass,
} from "./LabUi";

export default function DataImport({
  onImported,
}: {
  onImported: (dataset: LabDataset) => void;
}) {
  const [source, setSource] = useState<"csv" | "cache" | "market">("cache");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const client = useQueryClient();
  async function perform(task: () => Promise<LabDataset>) {
    setBusy(true);
    setError("");
    try {
      const result = await task();
      await client.invalidateQueries({ queryKey: ["lab-datasets"] });
      onImported(result);
    } catch (e) {
      setError(getApiErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Panel
      title="准备研究数据"
      detail="使用已有缓存、获取日线行情，或导入自己的 CSV。每次导入保留独立的数据版本。"
    >
      <div className="space-y-4">
        <Field label="数据来源">
          <select
            className={inputClass}
            value={source}
            disabled={busy}
            onChange={(e) => setSource(e.target.value as typeof source)}
          >
            <option value="cache">本地行情缓存</option>
            <option value="market">获取市场行情</option>
            <option value="csv">导入 CSV</option>
          </select>
        </Field>
        {source === "csv" ? (
          <div className="space-y-3">
            <p className="text-xs leading-6 text-slate-400">
              UTF-8 CSV，列名：date, symbol, open, high, low, close,
              volume。volume 单位为股数；A 股以“手”提供的 CSV 需先乘以
              100。日期使用 YYYY-MM-DD。最多 8 MB。
            </p>
            <a
              href="/api/lab/datasets/template.csv"
              download
              className="inline-flex text-xs text-cyan-300 underline underline-offset-4"
            >
              下载 CSV 格式示例
            </a>
            <label className={`${buttonClass} w-full cursor-pointer`}>
              <Upload size={16} />
              选择行情 CSV
              <input
                type="file"
                aria-label="导入研究行情 CSV"
                className="sr-only"
                accept=".csv,text/csv"
                disabled={busy}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    if (file.size > 8 * 1024 * 1024) {
                      setError("CSV 文件不能超过 8 MB");
                      return;
                    }
                    void perform(() => labService.importDataset(file));
                  }
                  e.target.value = "";
                }}
              />
            </label>
          </div>
        ) : (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              void perform(() =>
                labService.marketDataset({
                  name: String(f.get("name")),
                  symbols: String(f.get("symbols"))
                    .split(/[\s,，]+/)
                    .filter(Boolean),
                  start: String(f.get("start")),
                  end: String(f.get("end")),
                  adjustment: String(f.get("adjustment")),
                  source,
                }),
              );
            }}
          >
            <Field label="数据集名称">
              <input
                className={inputClass}
                name="name"
                required
                maxLength={80}
                placeholder="股票日线研究"
                defaultValue="股票日线研究"
              />
            </Field>
            <Field
              label="标的代码"
              help="使用行情缓存中的代码；多个标的用逗号分隔，最多 10 个。"
            >
              <input
                className={inputClass}
                name="symbols"
                required
                placeholder="600519, 000858"
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="开始日期">
                <input
                  className={inputClass}
                  name="start"
                  type="date"
                  required
                  defaultValue={`${new Date().getFullYear() - 3}-01-01`}
                />
              </Field>
              <Field label="结束日期">
                <input
                  className={inputClass}
                  name="end"
                  type="date"
                  required
                  defaultValue={new Date().toISOString().slice(0, 10)}
                />
              </Field>
            </div>
            <Field label="复权方式">
              <select className={inputClass} name="adjustment">
                <option value="qfq">前复权</option>
                <option value="hfq">后复权</option>
                <option value="none">不复权</option>
              </select>
            </Field>
            <button className={primaryClass} disabled={busy}>
              {busy ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Database size={16} />
              )}
              {source === "cache" ? "从缓存创建数据集" : "获取行情并创建数据集"}
            </button>
          </form>
        )}
        {busy && (
          <p role="status" className="text-xs leading-6 text-cyan-200">
            正在准备数据，请稍候。完成后会自动选中新的数据集。
          </p>
        )}
        {error && <Failure message={error} />}
      </div>
    </Panel>
  );
}
