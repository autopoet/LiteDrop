import type {
  AdminFile,
  AdminOverview,
  HealthStatus,
  PageResult,
  ShareFile,
  UploadResult,
  UploadStatus,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

interface SuccessEnvelope<T> {
  success: true;
  data: T;
  request_id?: string;
}

interface ErrorEnvelope {
  success: false;
  error: {
    code?: string;
    message?: string;
  };
  detail?: string;
  request_id?: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly code = "REQUEST_FAILED",
    public readonly status = 0,
  ) {
    super(message);
  }
}

function isEnvelope<T>(value: unknown): value is SuccessEnvelope<T> {
  return Boolean(
    value &&
      typeof value === "object" &&
      "success" in value &&
      "data" in value,
  );
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  token?: string,
): Promise<T> {
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  } catch {
    throw new ApiError("无法连接服务器，请检查网络后重试。", "NETWORK_ERROR");
  }

  const payload = response.status === 204 ? undefined : await response.json().catch(() => undefined);

  if (!response.ok) {
    const error = payload as ErrorEnvelope | undefined;
    throw new ApiError(
      error?.error?.message ?? error?.detail ?? "请求失败，请稍后重试。",
      error?.error?.code ?? "REQUEST_FAILED",
      response.status,
    );
  }

  return (isEnvelope<T>(payload) ? payload.data : payload) as T;
}

export interface InitUploadInput {
  file_name: string;
  total_size: number;
  expire_hours: number;
  download_limit: number;
}

export interface InitUploadResult {
  upload_id: string;
  chunk_size: number;
  total_chunks: number;
  expires_at: string;
}

export interface AdminFileQuery {
  query?: string;
  status?: string;
  page?: number;
  pageSize?: number;
}

export const api = {
  health(signal?: AbortSignal) {
    return request<HealthStatus>("/health", { signal });
  },

  initUpload(input: InitUploadInput, accessCode: string, signal?: AbortSignal) {
    return request<InitUploadResult>("/api/uploads", {
      method: "POST",
      headers: accessCode ? { "X-Upload-Code": accessCode } : undefined,
      body: JSON.stringify(input),
      signal,
    });
  },

  getUpload(uploadId: string, signal?: AbortSignal) {
    return request<UploadStatus>(`/api/uploads/${uploadId}`, { signal });
  },

  uploadPart(
    uploadId: string,
    partNumber: number,
    chunk: Blob,
    sha256: string,
    signal: AbortSignal,
  ) {
    const body = new FormData();
    body.append("chunk", chunk, `${partNumber}.part`);
    body.append("chunk_sha256", sha256);
    return request<{ part_number: number; idempotent: boolean }>(
      `/api/uploads/${uploadId}/chunks/${partNumber}`,
      { method: "PUT", body, signal },
    );
  },

  completeUpload(uploadId: string, signal?: AbortSignal) {
    return request<UploadResult>(`/api/uploads/${uploadId}/complete`, {
      method: "POST",
      signal,
    });
  },

  cancelUpload(uploadId: string) {
    return request<void>(`/api/uploads/${uploadId}`, { method: "DELETE" });
  },

  getShare(code: string) {
    return request<ShareFile>(`/api/shares/${code}`);
  },

  createDownloadTicket(code: string) {
    return request<{ download_url: string; expires_at: string }>(
      `/api/shares/${code}/download-ticket`,
      { method: "POST" },
    );
  },

  adminLogin(username: string, password: string) {
    return request<{ access_token: string; token_type?: string }>(
      "/api/admin/login",
      {
        method: "POST",
        body: JSON.stringify({ username, password }),
      },
    );
  },

  adminOverview(token: string) {
    return request<AdminOverview>("/api/admin/overview", {}, token);
  },

  adminFiles(token: string, options: AdminFileQuery = {}) {
    const {
      query = "",
      status = "all",
      page = 1,
      pageSize = 20,
    } = options;
    const params = new URLSearchParams();
    if (query) params.set("q", query);
    if (status !== "all") params.set("status", status);
    params.set("page", String(page));
    params.set("page_size", String(pageSize));
    return request<PageResult<AdminFile>>(
      `/api/admin/files?${params}`,
      {},
      token,
    );
  },

  deleteAdminFile(token: string, fileId: string) {
    return request<void>(`/api/admin/files/${fileId}`, { method: "DELETE" }, token);
  },

  cleanup(token: string) {
    return request<{
      uploads_deleted: number;
      files_deleted: number;
      tmp_files_deleted: number;
    }>(
      "/api/admin/cleanup",
      { method: "POST" },
      token,
    );
  },
};

export function downloadUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  return `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}
