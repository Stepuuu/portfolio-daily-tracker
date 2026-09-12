import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Cable, Loader2, Plus, Save } from "lucide-react";
import { labService, type LabConnection } from "@/services/lab";
import { getApiErrorMessage } from "@/services/api";
import {
  buttonClass,
  Failure,
  Field,
  inputClass,
  Loading,
  Panel,
  primaryClass,
} from "./LabUi";

const emptyProfile = (): LabConnection => ({
  id: `connection-${Date.now()}`,
  name: "",
  provider: "openai",
  model: "",
  base_url: "",
  api_key_env: "OPENAI_API_KEY",
  timeout: 120,
});
export default function LabConnections() {
  const client = useQueryClient();
  const profiles = useQuery({
    queryKey: ["lab-connections"],
    queryFn: labService.connections,
  });
  const capabilities = useQuery({
    queryKey: ["lab-capabilities"],
    queryFn: labService.capabilities,
  });
  const [draft, setDraft] = useState<LabConnection | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const providerInfo = capabilities.data?.providers.find(
    (p) => p.id === draft?.provider,
  );
  const cli =
    draft?.provider.endsWith("_cli") || providerInfo?.auth_mode === "cli";
  async function check(id: string) {
    setBusy(id);
    setError("");
    setNotice("");
    try {
      const result = await labService.checkConnection(id);
      const ready =
        (result.available ?? result.ok ?? result.configured) &&
        result.authenticated !== false;
      setNotice(
        `${ready ? "接入条件已就绪" : "接入条件尚未满足"}：${result.reason || result.message || "此检查不发送模型请求；实际可用性会在研究开始时验证。"}`,
      );
    } catch (e) {
      setError(getApiErrorMessage(e));
    } finally {
      setBusy("");
    }
  }
  return (
    <Panel
      title="研究模型接入"
      detail="手动实验可以独立运行。自动研究可使用兼容 API 或已经安装并登录的官方客户端。"
      action={
        <button
          className={buttonClass}
          onClick={() => {
            setDraft({
              ...emptyProfile(),
              provider: capabilities.data?.providers[0]?.id || "openai",
            });
            setError("");
            setNotice("");
          }}
        >
          <Plus size={15} />
          新增连接
        </button>
      }
    >
      <div className="space-y-4">
        {error && <Failure message={error} />}
        {notice && (
          <p
            role="status"
            className="rounded-xl border border-cyan-500/25 bg-cyan-500/10 p-3 text-sm leading-6 text-cyan-100"
          >
            {notice}
          </p>
        )}
        {profiles.isPending && <Loading />}
        {profiles.isError && (
          <Failure
            message={getApiErrorMessage(profiles.error)}
            retry={() => void profiles.refetch()}
          />
        )}
        <div className="grid gap-3 md:grid-cols-2">
          {profiles.data?.map((profile) => (
            <article
              key={profile.id}
              className="rounded-xl border border-slate-700 bg-slate-950/70 p-4"
            >
              <div className="flex items-start gap-3">
                <Cable className="mt-1 text-cyan-300" size={18} />
                <div className="min-w-0 flex-1">
                  <h3 className="break-words text-sm font-medium">
                    {profile.name}
                  </h3>
                  <p className="mt-1 break-all text-xs text-slate-400">
                    {profile.provider} · {profile.model || "客户端默认模型"}
                  </p>
                </div>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  className={buttonClass}
                  onClick={() => {
                    setDraft({ ...profile });
                    setNotice("");
                  }}
                >
                  编辑
                </button>
                <button
                  className={buttonClass}
                  disabled={!!busy}
                  onClick={() => void check(profile.id)}
                >
                  {busy === profile.id ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Check size={14} />
                  )}
                  检查接入条件
                </button>
              </div>
            </article>
          ))}
        </div>
        {profiles.isSuccess && !profiles.data.length && !draft && (
          <p className="rounded-xl border border-dashed border-slate-700 p-5 text-sm leading-6 text-slate-400">
            还没有研究模型连接。可以先运行手动实验，或添加一个连接开始自动研究。
          </p>
        )}
        {capabilities.isError && (
          <Failure
            message={getApiErrorMessage(capabilities.error)}
            retry={() => void capabilities.refetch()}
          />
        )}
        {!!capabilities.data?.providers?.length && (
          <div className="flex flex-wrap gap-2">
            {capabilities.data.providers.map((p) => (
              <span
                key={p.id}
                title={p.reason}
                className={`rounded-lg border px-2 py-1 text-xs ${p.available ? "border-emerald-500/25 text-emerald-300" : "border-slate-700 text-slate-500"}`}
              >
                {p.label} · {p.available ? "可配置" : p.reason || "未就绪"}
              </span>
            ))}
          </div>
        )}
        {draft && (
          <form
            className="space-y-4 border-t border-slate-700 pt-5"
            onSubmit={(e) => {
              e.preventDefault();
              setBusy("save");
              setError("");
              setNotice("");
              const profile = {
                ...draft,
                base_url: cli ? null : draft.base_url || null,
                api_key_env: cli ? null : draft.api_key_env || null,
              };
              void labService
                .saveConnection(profile)
                .then(async () => {
                  await client.invalidateQueries({
                    queryKey: ["lab-connections"],
                  });
                  setDraft(null);
                  setNotice(
                    "连接配置已保存。可以检查接入条件，再返回工作台开始研究。",
                  );
                })
                .catch((e) => setError(getApiErrorMessage(e)))
                .finally(() => setBusy(""));
            }}
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="连接名称">
                <input
                  className={inputClass}
                  required
                  maxLength={80}
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  placeholder="我的研究模型"
                />
              </Field>
              <Field label="接入方式">
                <select
                  className={inputClass}
                  value={draft.provider}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      provider: e.target.value as LabConnection["provider"],
                      api_key_env:
                        e.target.value === "anthropic"
                          ? "ANTHROPIC_API_KEY"
                          : "OPENAI_API_KEY",
                      base_url: "",
                    })
                  }
                >
                  {!capabilities.data?.providers.some(
                    (p) => p.id === draft.provider,
                  ) && <option value={draft.provider}>{draft.provider}</option>}
                  {capabilities.data?.providers.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.label}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label="模型名称"
                help={
                  cli
                    ? "留空使用客户端默认模型。"
                    : "填写服务提供方实际支持的模型 ID。"
                }
              >
                <input
                  className={inputClass}
                  required={
                    draft.provider === "openai" ||
                    draft.provider === "anthropic"
                  }
                  value={draft.model}
                  onChange={(e) =>
                    setDraft({ ...draft, model: e.target.value })
                  }
                />
              </Field>
              <Field label="单次模型调用超时（秒）">
                <input
                  className={inputClass}
                  type="number"
                  min={10}
                  max={300}
                  required
                  value={draft.timeout}
                  onChange={(e) =>
                    setDraft({ ...draft, timeout: Number(e.target.value) })
                  }
                />
              </Field>
              {!cli && (
                <>
                  <Field
                    label="API 地址（可选）"
                    help="留空使用服务默认地址。地址中不要包含密钥。"
                  >
                    <input
                      className={inputClass}
                      type="url"
                      value={draft.base_url || ""}
                      onChange={(e) =>
                        setDraft({ ...draft, base_url: e.target.value })
                      }
                      placeholder="https://api.example.com/v1"
                    />
                  </Field>
                  <Field
                    label="密钥环境变量名"
                    help="填写变量名称；密钥在启动后端的环境中设置。"
                  >
                    <input
                      className={inputClass}
                      pattern="[A-Z][A-Z0-9_]*"
                      value={draft.api_key_env || ""}
                      onChange={(e) =>
                        setDraft({ ...draft, api_key_env: e.target.value })
                      }
                      placeholder="OPENAI_API_KEY"
                    />
                  </Field>
                </>
              )}
            </div>
            {cli && (
              <p className="text-xs leading-6 text-slate-400">
                使用后端所在环境中客户端已有的登录状态。连接检查确认客户端可找到；授权或网络问题会在任务事件中显示。
              </p>
            )}
            <div className="flex gap-3">
              <button className={primaryClass} disabled={!!busy}>
                {busy === "save" ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <Save size={16} />
                )}
                保存连接
              </button>
              <button
                type="button"
                className={buttonClass}
                disabled={!!busy}
                onClick={() => setDraft(null)}
              >
                取消编辑
              </button>
            </div>
          </form>
        )}
      </div>
    </Panel>
  );
}
