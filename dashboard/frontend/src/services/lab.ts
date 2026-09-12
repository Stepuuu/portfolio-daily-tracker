import api from "./api";

export type LabMode = "manual" | "agent";
export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };
export interface LabParams {
  [key: string]: JsonValue | undefined;
  horizon?: number;
  lookback?: number;
  train_ratio?: number;
  validation_ratio?: number;
  fee_bps?: number;
  slippage_bps?: number;
  seed?: number;
  market?: "a_share" | "hk" | "us";
  adjustment?: "qfq" | "hfq" | "none";
}
export interface LabRequest {
  mode: LabMode;
  objective: string;
  dataset_id: string;
  template_id: string;
  params: LabParams;
  connection_id?: string;
  max_experiments: number;
  timeout_seconds: number;
  client_request_id?: string;
  refresh_data?: boolean;
}
export interface LabTemplate {
  id: string;
  name: string;
  description: string;
  target?: string;
  lesson?: string;
  defaults?: Partial<LabParams>;
  parameters?: Record<string, ParameterSchema>;
}
export interface ParameterSchema {
  type?: string;
  title?: string;
  description?: string;
  default?: JsonValue;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  enum?: JsonValue[];
  required?: boolean;
}
export interface LabDataset {
  id: string;
  name: string;
  symbols: string[];
  rows: number;
  start: string;
  end: string;
  source: string;
  synthetic: boolean;
  hash?: string;
  created_at?: string;
  adjustment?: "qfq" | "hfq" | "none";
  selection?: {
    name: string;
    symbols: string[];
    start: string;
    end: string;
    adjustment: "qfq" | "hfq" | "none";
    source: "cache" | "market";
  };
}
export interface LabConnection {
  id: string;
  name: string;
  provider: string;
  model: string;
  base_url?: string | null;
  api_key_env?: string | null;
  timeout: number;
}
export interface LabProvider {
  id: string;
  label: string;
  available: boolean;
  reason?: string;
  auth_mode?: string;
}
export interface LabCapabilities {
  providers: LabProvider[];
  limits?: Record<string, number>;
  demo?: boolean;
}
export interface LabEvent {
  seq: number;
  kind: string;
  message: string;
  created_at: string;
}
export interface LabRun {
  id: string;
  status: string;
  stage: string;
  request: LabRequest;
  created_at: string;
  updated_at: string;
  error?: string;
  events?: LabEvent[];
  result?: Record<string, unknown> | null;
  progress?: {
    experiments_completed: number;
    model_calls: number;
    max_model_calls: number;
    plan?: Record<string, unknown>;
    frozen: boolean;
  };
}
export interface LabSchedule {
  id?: string;
  name: string;
  request: LabRequest;
  interval_seconds: number;
  max_runs: number;
  enabled: boolean;
  completed?: number;
  next_due?: number;
}
export interface ConnectionCheck {
  available?: boolean;
  authenticated?: boolean | null;
  ok?: boolean;
  reason?: string;
  message?: string;
  configured?: boolean;
}

export function cleanRequest(request: LabRequest): LabRequest {
  const params = Object.fromEntries(
    Object.entries(request.params).filter(([, value]) => value !== undefined),
  );
  strictJson(params);
  return {
    mode: request.mode,
    objective: request.objective,
    dataset_id: request.dataset_id,
    template_id: request.template_id,
    connection_id: request.mode === "agent" ? request.connection_id : undefined,
    max_experiments: request.max_experiments,
    timeout_seconds: request.timeout_seconds,
    client_request_id: request.client_request_id,
    refresh_data: request.refresh_data ?? false,
    params,
  };
}

export function strictJson(value: unknown): void {
  if (value === null || typeof value === "string" || typeof value === "boolean")
    return;
  if (typeof value === "number" && Number.isFinite(value)) return;
  if (Array.isArray(value)) {
    value.forEach(strictJson);
    return;
  }
  if (typeof value === "object" && value) {
    Object.values(value).forEach(strictJson);
    return;
  }
  throw new Error("实验参数必须是有效 JSON，数值需要为有限数。");
}
export const isBuiltinTemplate = (id: string) =>
  ["momentum", "mean_reversion", "volatility"].includes(id);
export function templateParams(
  template: LabTemplate,
  previous: LabParams,
): LabParams {
  const defaults = {
    ...Object.fromEntries(
      Object.entries(template.parameters || {})
        .filter(([, schema]) => schema.default !== undefined)
        .map(([key, schema]) => [key, schema.default]),
    ),
    ...template.defaults,
  };
  if (!isBuiltinTemplate(template.id)) return defaults;
  return {
    ...defaultParams,
    ...defaults,
    market: previous.market || defaultParams.market,
    adjustment: previous.adjustment || defaultParams.adjustment,
  };
}

function list<T>(data: unknown, key: string): T[] {
  if (Array.isArray(data)) return data as T[];
  if (data && typeof data === "object") {
    const value = data as Record<string, unknown>;
    const items = value.items ?? value[key];
    if (Array.isArray(items)) return items as T[];
  }
  throw new Error("服务返回了无法识别的列表，请刷新后重试");
}
function item<T>(data: unknown): T {
  if (data && typeof data === "object" && "item" in data)
    return (data as { item: T }).item;
  return data as T;
}

export const labService = {
  capabilities: async () =>
    item<LabCapabilities>((await api.get("/lab/capabilities")).data),
  templates: async () =>
    list<LabTemplate>((await api.get("/lab/templates")).data, "templates"),
  datasets: async () =>
    list<LabDataset>((await api.get("/lab/datasets")).data, "datasets"),
  runs: async () => list<LabRun>((await api.get("/lab/runs")).data, "runs"),
  run: async (id: string) =>
    item<LabRun>((await api.get(`/lab/runs/${encodeURIComponent(id)}`)).data),
  submit: async (request: LabRequest) =>
    item<LabRun>((await api.post("/lab/runs", cleanRequest(request))).data),
  cancel: async (id: string) =>
    item<LabRun>(
      (await api.post(`/lab/runs/${encodeURIComponent(id)}/cancel`)).data,
    ),
  retry: async (id: string) =>
    item<LabRun>(
      (await api.post(`/lab/runs/${encodeURIComponent(id)}/retry`)).data,
    ),
  connections: async () =>
    list<LabConnection>(
      (await api.get("/lab/connections")).data,
      "connections",
    ),
  saveConnection: async (profile: LabConnection) =>
    item<LabConnection>((await api.put("/lab/connections", profile)).data),
  checkConnection: async (id: string) =>
    item<ConnectionCheck>(
      (
        await api.post(
          `/lab/connections/${encodeURIComponent(id)}/check`,
          {},
          { timeout: 15000 },
        )
      ).data,
    ),
  importDataset: async (file: File, name?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (name) form.append("name", name);
    return item<LabDataset>(
      (
        await api.post("/lab/datasets/import", form, {
          headers: { "Content-Type": "multipart/form-data" },
          timeout: 60000,
        })
      ).data,
    );
  },
  marketDataset: async (request: {
    name: string;
    symbols: string[];
    start: string;
    end: string;
    adjustment: string;
    source: "cache" | "market";
  }) =>
    item<LabDataset>(
      (await api.post("/lab/datasets/market", request, { timeout: 150000 }))
        .data,
    ),
  schedules: async () =>
    list<LabSchedule>((await api.get("/lab/schedules")).data, "schedules"),
  saveSchedule: async (schedule: LabSchedule) =>
    item<LabSchedule>(
      (
        await api.post("/lab/schedules", {
          id: schedule.id,
          name: schedule.name,
          request: cleanRequest(schedule.request),
          interval_seconds: schedule.interval_seconds,
          max_runs: schedule.max_runs,
          enabled: schedule.enabled,
        })
      ).data,
    ),
};

export const defaultParams: LabParams = {
  horizon: 5,
  lookback: 20,
  train_ratio: 0.6,
  validation_ratio: 0.2,
  fee_bps: 10,
  slippage_bps: 5,
  seed: 42,
  market: "us",
  adjustment: "qfq",
};
export const isActiveRun = (run?: LabRun) =>
  !!run && ["queued", "running", "cancelling"].includes(run.status);
