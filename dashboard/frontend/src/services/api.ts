import axios from "axios";
import type { AxiosError } from "axios";

const API_BASE = "/api";
const REQUEST_TIMEOUT_MS = 10000;

const api = axios.create({
  baseURL: API_BASE,
  timeout: REQUEST_TIMEOUT_MS,
  headers: {
    "Content-Type": "application/json",
  },
});

// 请求拦截器
api.interceptors.request.use(
  (config) => {
    // 可以添加认证 token 等
    return config;
  },
  (error) => {
    return Promise.reject(error);
  },
);

// 响应拦截器
api.interceptors.response.use(
  (response) => {
    return response;
  },
  (error) => {
    const detail =
      error.response?.data?.detail ||
      error.response?.data?.message ||
      error.message;
    console.error("API Error:", detail);
    return Promise.reject(error);
  },
);

export function getApiErrorMessage(error: unknown): string {
  const axiosError = error as AxiosError<{
    detail?: unknown;
    message?: unknown;
    error?: unknown;
  }>;
  const status = axiosError.response?.status;
  const raw =
    axiosError.response?.data?.detail ||
    axiosError.response?.data?.message ||
    axiosError.response?.data?.error;
  let detail = typeof raw === "string" ? raw : "";
  if (Array.isArray(raw)) {
    detail = raw
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          const field = Array.isArray(item.loc)
            ? item.loc.filter((v: unknown) => v !== "body").join(".")
            : "";
          return `${field ? field + ": " : ""}${String(item.msg)}`;
        }
        return "请检查输入内容";
      })
      .join("；");
  }
  if (status === 401) return detail || "鉴权失败，请检查 API Key";
  if (status === 403) return detail || "没有权限访问当前 API";
  if (status === 429) return detail || "请求过于频繁，已触发限流";
  if (status === 504) return detail || "上游服务超时，请稍后重试";
  if (status && status >= 500) return detail || `上游服务错误 (${status})`;
  if (axiosError.code === "ECONNABORTED") return "请求超时，请稍后重试";
  return detail || axiosError.message || "请求失败，请稍后重试";
}

export default api;
