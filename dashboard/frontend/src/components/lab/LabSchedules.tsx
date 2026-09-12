import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { labService, type LabRequest, type LabSchedule } from "@/services/lab";
import { getApiErrorMessage } from "@/services/api";
import { buttonClass, Failure, Field, inputClass, Panel } from "./LabUi";

export default function Schedules({
  draft,
  canRefresh,
}: {
  draft: LabRequest;
  canRefresh: boolean;
}) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["lab-schedules"],
    queryFn: labService.schedules,
    refetchInterval: 30000,
  });
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  async function save(schedule: LabSchedule) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await labService.saveSchedule(schedule);
      await client.invalidateQueries({ queryKey: ["lab-schedules"] });
      setNotice(schedule.enabled ? "自动研究安排已启用" : "自动研究安排已暂停");
    } catch (e) {
      setError(getApiErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Panel
      title="自动研究安排"
      detail="按当前研究配置定期运行，可以固定数据版本，或在每次运行前更新缓存与市场数据。"
    >
      <div className="space-y-4">
        {error && <Failure message={error} />}
        {notice && (
          <p role="status" className="text-sm text-cyan-200">
            {notice}
          </p>
        )}
        {query.isError && (
          <Failure
            message={getApiErrorMessage(query.error)}
            retry={() => void query.refetch()}
          />
        )}
        {query.data?.map((s) => (
          <div
            key={s.id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-700 p-3"
          >
            <div className="min-w-0">
              <h3 className="text-sm break-words">{s.name}</h3>
              <p className="mt-1 text-xs text-slate-500">
                {s.enabled ? "已启用" : "已暂停"} · 每{" "}
                {s.interval_seconds / 3600} 小时 · 已触发 {s.completed || 0} /{" "}
                {s.max_runs} 次（不代表成功数） ·{" "}
                {s.request.refresh_data ? "随运行更新" : "固定版本"}
              </p>
            </div>
            <button
              disabled={
                busy || (!s.enabled && (s.completed || 0) >= s.max_runs)
              }
              className={buttonClass}
              onClick={() => void save({ ...s, enabled: !s.enabled })}
            >
              {s.enabled
                ? "暂停"
                : (s.completed || 0) >= s.max_runs
                  ? "已达到次数上限"
                  : "启用"}
            </button>
          </div>
        ))}
        <form
          className="space-y-3 border-t border-slate-700 pt-4"
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            const { client_request_id: _key, ...request } = draft;
            void save({
              name: String(f.get("name")),
              request: {
                ...request,
                refresh_data: canRefresh && f.get("refresh_data") === "on",
              },
              interval_seconds: Number(f.get("hours")) * 3600,
              max_runs: Number(f.get("runs")),
              enabled: true,
            });
          }}
        >
          <Field label="安排名称">
            <input
              className={inputClass}
              required
              maxLength={80}
              name="name"
              placeholder="每周检查动量研究"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="运行间隔（小时）">
              <input
                className={inputClass}
                name="hours"
                type="number"
                min={1}
                max={8760}
                defaultValue={24}
                required
              />
            </Field>
            <Field label="最多运行次数">
              <input
                className={inputClass}
                name="runs"
                type="number"
                min={1}
                max={365}
                defaultValue={10}
                required
              />
            </Field>
          </div>
          <div className="rounded-xl border border-slate-700 p-3">
            <label className="flex items-center gap-2 text-sm text-slate-200">
              <input
                key={draft.dataset_id}
                type="checkbox"
                name="refresh_data"
                disabled={!canRefresh}
                aria-describedby="schedule-refresh-help"
                className="h-4 w-4 accent-cyan-400 disabled:cursor-not-allowed"
              />
              每次运行前更新数据
            </label>
            <p
              id="schedule-refresh-help"
              className="mt-2 text-xs leading-6 text-slate-400"
            >
              {canRefresh
                ? "沿用股票、起始日期与复权口径，更新到运行当天并保存新版本。缓存本身可能仍未更新。未勾选时使用固定版本。"
                : "仅有来源信息的缓存或市场数据支持刷新。CSV 与合成数据需导入新版本后新建安排。"}
            </p>
          </div>
          <button
            className={buttonClass}
            disabled={
              busy ||
              !draft.dataset_id ||
              (draft.mode === "agent" && !draft.connection_id)
            }
          >
            {busy && <Loader2 size={14} className="animate-spin" />}
            按当前配置创建并启用
          </button>
        </form>
      </div>
    </Panel>
  );
}
