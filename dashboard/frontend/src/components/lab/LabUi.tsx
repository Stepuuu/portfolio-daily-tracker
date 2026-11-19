import {
  cloneElement,
  isValidElement,
  useId,
  type ReactElement,
  type ReactNode,
} from "react";
import { AlertCircle, Check, Loader2, RefreshCw } from "lucide-react";
import { buttonClass as kitButton, cx } from "@/components/ui";

// The research pages' controls, drawn like the kit (src/components/ui): a native field with the kit's
// outline and focus, and the kit's secondary and primary button looks.
export const inputClass =
  "w-full min-w-0 rounded-md border border-line-input bg-surface px-3 py-1.5 text-body text-fg outline-none transition-colors duration-[120ms] [color-scheme:var(--scheme)] placeholder:text-fg-3 hover:border-fg-3 focus:border-accent focus:ring-1 focus:ring-accent disabled:cursor-not-allowed disabled:border-line disabled:bg-surface-2 disabled:text-fg-3";
export const buttonClass = kitButton({ variant: "secondary" });
export const primaryClass = kitButton({ variant: "primary" });
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
      className={cx("min-w-0 rounded-lg border border-line bg-surface p-4 text-fg sm:p-5", className)}
    >
      {title && (
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-title text-fg">{title}</h2>
            {detail && (
              <p className="mt-1 max-w-2xl text-table text-fg-3">
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
      <label htmlFor={id} className="block text-table font-medium text-fg-2">
        {label}
      </label>
      {control}
      {help && (
        <span
          id={`${id}-help`}
          className="block text-table text-fg-3"
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
      className="flex flex-wrap items-start gap-3 rounded-md bg-warn/10 px-3 py-2 text-body text-fg"
    >
      <AlertCircle size={16} className="mt-[3px] shrink-0 text-warn" aria-hidden />
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
      className="flex items-center gap-2 p-4 text-body text-fg-3"
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
  // A run's state is not a gain or a loss: neutral (the done mark tells completed apart), accent while it
  // runs, amber when it failed. Red and green mean up and down in this app.
  const tone =
    value === "completed"
      ? "border-line-input/60 text-fg bg-fg/[.06]"
      : value === "failed"
        ? "border-warn/40 text-warn bg-warn/10"
        : value === "running"
          ? "border-accent/40 text-accent bg-accent/10"
          : "border-line text-fg-3 bg-surface-2";
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-caption ${tone}`}
    >
      {value === "running" && <Loader2 className="animate-spin" size={12} />}
      {value === "completed" && <Check aria-hidden size={12} />}
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
