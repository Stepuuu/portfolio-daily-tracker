import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  BookOpen,
  Loader2,
  Save,
  Settings as SettingsIcon,
} from "lucide-react";
import api, { getApiErrorMessage } from "@/services/api";
import LabConnections from "@/components/lab/LabConnections";
import {
  buttonClass,
  Failure,
  Field,
  inputClass,
  Loading,
  Panel,
  primaryClass,
} from "@/components/lab/LabUi";

interface ModelInfo {
  id: string;
  name: string;
  description: string;
  supports_vision: boolean;
}
interface ConfigResponse {
  current_api_group: string;
  current_model: string;
  api_groups: Record<
    string,
    { name: string; description: string; models: ModelInfo[] }
  >;
  available_models: ModelInfo[];
}
export default function Settings() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["settings"],
    queryFn: async () =>
      (await api.get<ConfigResponse>("/settings/config")).data,
  });
  const [group, setGroup] = useState(""),
    [model, setModel] = useState("");
  const [dirty, setDirty] = useState(false),
    [busy, setBusy] = useState(false);
  const [error, setError] = useState(""),
    [message, setMessage] = useState("");
  useEffect(() => {
    if (query.data && !dirty) {
      setGroup(query.data.current_api_group);
      setModel(query.data.current_model);
    }
  }, [query.data, dirty]);
  const models = query.data?.api_groups[group]?.models || [];
  async function save() {
    if (!query.data) return;
    setBusy(true);
    setError("");
    setMessage("");
    const changed: string[] = [];
    async function update(path: string, body: Record<string, string>) {
      const response = (await api.post(path, body)).data;
      if (response?.success === false)
        throw new Error(response.error || "配置更新失败");
    }
    try {
      if (group !== query.data.current_api_group) {
        await update("/settings/api-group", { group_name: group });
        changed.push("API 组");
      }
      if (model !== query.data.current_model || changed.length) {
        await update("/settings/model", { model_id: model });
        changed.push("模型");
      }
      setDirty(false);
      await client.invalidateQueries({ queryKey: ["settings"] });
      setMessage(
        changed.length ? `${changed.join("、")}已更新` : "没有需要保存的改动",
      );
    } catch (e) {
      setError(
        `${changed.length ? `${changed.join("、")}已更新，但后续保存失败：` : ""}${getApiErrorMessage(e)}`,
      );
      await client.invalidateQueries({ queryKey: ["settings"] });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="mx-auto max-w-5xl space-y-5 text-slate-100">
      <header className="border-b border-slate-700 pb-5">
        <h1 className="flex items-center gap-3 text-2xl font-semibold">
          <SettingsIcon className="text-cyan-300" />
          设置
        </h1>
        <p className="mt-3 text-sm text-slate-400">
          管理研究模型接入与对话默认模型。
        </p>
      </header>
      <LabConnections />
      <Panel
        title="对话默认模型"
        detail="这些设置用于对话助手。研究工作台使用每个实验选择的模型连接。"
      >
        <div className="space-y-4">
          {query.isPending && <Loading />}
          {query.isError && (
            <Failure
              message={getApiErrorMessage(query.error)}
              retry={() => void query.refetch()}
            />
          )}
          {error && <Failure message={error} />}
          {message && (
            <p
              role="status"
              className="rounded-xl bg-cyan-500/10 p-3 text-sm text-cyan-100"
            >
              {message}
            </p>
          )}
          {query.data && (
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                void save();
              }}
            >
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="API 组">
                  <select
                    className={inputClass}
                    value={group}
                    disabled={busy}
                    onChange={(e) => {
                      setGroup(e.target.value);
                      setModel(
                        query.data!.api_groups[e.target.value]?.models[0]?.id ||
                          "",
                      );
                      setDirty(true);
                      setMessage("");
                    }}
                  >
                    {Object.entries(query.data.api_groups).map(
                      ([key, value]) => (
                        <option key={key} value={key}>
                          {value.name}
                        </option>
                      ),
                    )}
                  </select>
                </Field>
                <Field label="模型">
                  <select
                    className={inputClass}
                    value={model}
                    disabled={busy}
                    onChange={(e) => {
                      setModel(e.target.value);
                      setDirty(true);
                      setMessage("");
                    }}
                  >
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                        {m.supports_vision ? " · 支持图片" : ""}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              <p className="text-xs leading-6 text-slate-500">
                {models.find((m) => m.id === model)?.description ||
                  query.data.api_groups[group]?.description}
              </p>
              <button
                className={primaryClass}
                disabled={busy || !group || !model}
              >
                {busy ? (
                  <Loader2 size={15} className="animate-spin" />
                ) : (
                  <Save size={15} />
                )}
                保存对话设置
              </button>
            </form>
          )}
        </div>
      </Panel>
      <Panel
        title="账户记录"
        detail="现金与持仓通过账本记录，并在确认前展示变化。"
      >
        <Link className={buttonClass} to="/ledger">
          <BookOpen size={16} />
          前往交易账本
          <ArrowRight size={14} />
        </Link>
      </Panel>
      <p className="text-xs text-slate-500">
        后端连接使用当前站点的 /api，遵循启动时设置的端口与代理。
      </p>
    </div>
  );
}
