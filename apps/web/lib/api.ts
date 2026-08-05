const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  token?: string | null
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    // Пропускает межстраничную заглушку-предупреждение ngrok (см. CIN-52,
    // локальный OAuth-тест через туннель) -- без этого заголовка ngrok
    // отдаёт HTML вместо JSON первому запросу с нового браузера.
    "ngrok-skip-browser-warning": "true",
    ...((options.headers as Record<string, string>) ?? {}),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(`${API_URL}${path}`, { ...options, headers });

  if (!response.ok) {
    let detail: string = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // response body wasn't JSON -- fall back to statusText
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function upload<T>(path: string, file: File, token?: string | null): Promise<T> {
  const formData = new FormData();
  formData.append("file", file);
  const headers: Record<string, string> = {
    "ngrok-skip-browser-warning": "true",
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  // No "Content-Type" here on purpose -- the browser sets
  // multipart/form-data with the right boundary itself; setting it
  // manually (as `request()` above does for JSON) breaks the upload.
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    body: formData,
    headers,
  });

  if (!response.ok) {
    let detail: string = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // response body wasn't JSON -- fall back to statusText
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export const api = {
  get: <T,>(path: string, token?: string | null) => request<T>(path, { method: "GET" }, token),
  post: <T,>(path: string, body?: unknown, token?: string | null) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }, token),
  patch: <T,>(path: string, body?: unknown, token?: string | null) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }, token),
  delete: <T,>(path: string, token?: string | null) =>
    request<T>(path, { method: "DELETE" }, token),
  upload,
};
