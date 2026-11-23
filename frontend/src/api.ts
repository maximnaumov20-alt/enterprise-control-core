export type AssetStatus = "active" | "in_repair" | "retired";

export interface Asset {
  id: number;
  name: string;
  asset_tag: string;
  category: string;
  manufacturer: string | null;
  model: string | null;
  serial_number: string | null;
  assigned_to: string | null;
  status: AssetStatus;
  created_at: string;
  updated_at: string;
}

export type AssetInput = Omit<Asset, "id" | "created_at" | "updated_at">;

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers }
  });
  if (!response.ok) {
    let message = `Ошибка API: ${response.status}`;
    try {
      const body = await response.json();
      message = typeof body.detail === "string" ? body.detail : message;
    } catch { /* response did not contain JSON */ }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string }>("/health"),
  assets: (search = "") => request<Asset[]>(`/api/v1/assets?limit=200${search ? `&search=${encodeURIComponent(search)}` : ""}`),
  createAsset: (asset: AssetInput) => request<Asset>("/api/v1/assets", { method: "POST", body: JSON.stringify(asset) }),
  updateAsset: (id: number, asset: Partial<AssetInput>) => request<Asset>(`/api/v1/assets/${id}`, { method: "PATCH", body: JSON.stringify(asset) }),
  deleteAsset: (id: number) => request<void>(`/api/v1/assets/${id}`, { method: "DELETE" })
};