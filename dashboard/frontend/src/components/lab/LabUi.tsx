import {
  cloneElement,
  isValidElement,
  useId,
  type ReactElement,
  type ReactNode,
} from "react";
import { AlertCircle, Loader2, RefreshCw } from "lucide-react";

export const inputClass =
  "w-full min-w-0 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm text-slate-100 outline-none focus:border-cyan-500 focus:ring-2 focus:ring-cyan-500/20 disabled:opacity-50";
export const buttonClass =
  "inline-flex items-center justify-center gap-2 rounded-xl border border-slate-700 px-3 py-2 text-sm text-slate-200 transition-colors hover:border-slate-500 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40";
export const primaryClass =
  "inline-flex items-center justify-center gap-2 rounded-xl bg-cyan-300 px-4 py-2.5 text-sm font-semibold text-slate-950 transition-colors hover:bg-cyan-200 disabled:cursor-not-allowed disabled:opacity-40";
export function Panel({
  title,
  detail,
  children,
  action,
  className = "",
}: {
  title?: string;
  detail?: string;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`min-w-0 rounded-2xl border border-slate-700/70 bg-slate-900/80 p-4 sm:p-5 ${className}`}
    >
      {title && (
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-slate-100">{title}</h2>
            {detail && (
              <p className="mt-1 max-w-2xl text-xs leading-6 text-slate-400">
                {detail}
              </p>
            )}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: ReactNode;
}) {
  const id = useId();
  const control = isValidElement(children)
    ? cloneElement(
        children as ReactElement<{ id?: string; "aria-describedby"?: string }>,
        { id, "aria-describedby": help ? `${id}-help` : undefined },
      )
    : children;
  return (
    <div className="block min-w-0 space-y-2">
      <label htmlFor={id} className="block text-xs font-medium text-slate-300">
        {label}
      </label>
      {control}
      {help && (
        <span
          id={`${id}-help`}
          className="block text-xs leading-5 text-slate-500"
        >
          {help}
        </span>
      )}
    </div>
  );
}
export function Failure({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm leading-6 text-amber-200"
    >
      <AlertCircle size={18} className="mt-1 shrink-0" />
      <span className="min-w-0 flex-1 break-words">{message}</span>
      {retry && (
        <button type="button" onClick={retry} className={buttonClass}>
          <RefreshCw size={14} />
          重试
        </button>
      )}
    </div>
  );
}
export function Loading({ text = "正在读取…" }: { text?: string }) {
  return (
    <p
      role="status"
      className="flex items-center gap-2 p-4 text-sm text-slate-400"
    >
      <Loader2 className="animate-spin" size={16} />
      {text}
    </p>
  );
}
export function Status({ value }: { value: string }) {
  const labels: Record<string, string> = {
    queued: "排队中",
    running: "研究中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    cancelling: "取消中",
  };
  const tone =
    value === "completed"
      ? "border-emerald-500/30 text-emerald-300 bg-emerald-500/10"
      : value === "failed"
        ? "border-amber-500/30 text-amber-300 bg-amber-500/10"
        : value === "running"
          ? "border-cyan-500/30 text-cyan-300 bg-cyan-500/10"
          : "border-slate-700 text-slate-400 bg-slate-800";
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${tone}`}
    >
      {value === "running" && <Loader2 className="animate-spin" size={12} />}
      {labels[value] || value}
    </span>
  );
}
export const dateTime = (value: string) =>
  new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
