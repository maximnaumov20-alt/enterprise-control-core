export type AssetStatus = "active" | "in_repair" | "retired";
export type UserRole = "admin" | "editor" | "viewer";
export type SoftwareLicenseType = "subscription" | "perpetual" | "oem" | "site";
export type SoftwareLicenseStatus = "active" | "suspended" | "retired";
export type MaintenancePriority = "low" | "normal" | "high" | "urgent";
export type MaintenanceStatus = "open" | "in_progress" | "resolved" | "cancelled";

export interface CurrentUser {
  id: number;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  organization_id: number;
  organization_name: string;
  created_at: string;
}

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
  version: number;
}

export type AssetInput = Omit<Asset, "id" | "created_at" | "updated_at" | "version">;

export interface AssetHistoryEvent {
  id: number;
  actor_user_id: number | null;
  action: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface Department {
  id: number;
  version: number;
  name: string;
  code: string;
  created_at: string;
}

export interface Employee {
  id: number;
  version: number;
  employee_number: string;
  full_name: string;
  department_id: number | null;
  email: string | null;
  job_title: string | null;
  phone: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export type EmployeeInput = {
  employee_number: string;
  full_name: string;
  department_id: number | null;
  email: string | null;
  job_title: string | null;
  phone: string | null;
};

export interface MaintenanceRequest {
  id: number;
  asset_id: number;
  requested_by_user_id: number;
  assigned_to_user_id: number | null;
  title: string;
  description: string;
  priority: MaintenancePriority;
  status: MaintenanceStatus;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  version: number;
}

export interface MaintenanceRequestInput {
  asset_id: number;
  title: string;
  description: string;
  priority: MaintenancePriority;
}

export interface SoftwareLicense {
  id: number;
  name: string;
  publisher: string | null;
  version: string | null;
  license_type: SoftwareLicenseType;
  license_reference: string | null;
  seats_total: number;
  seats_used: number;
  purchased_at: string | null;
  expires_at: string | null;
  status: SoftwareLicenseStatus;
  notes: string | null;
  created_at: string;
  updated_at: string;
  record_version: number;
}

export type SoftwareLicenseInput = Omit<SoftwareLicense, "id" | "created_at" | "updated_at" | "record_version">;

export interface ManagedUser {
  id: number;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  organization_id: number;
  created_at: string;
  version: number;
}

export interface AuditEvent {
  id: number;
  actor_user_id: number | null;
  action: string;
  entity_type: string;
  entity_id: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface LoginResponse {
  access_token: string | null;
  token_type?: string;
  expires_in: number;
  mfa_required?: boolean;
  challenge_token?: string | null;
}

export interface MFAStatus {
  status: "disabled" | "pending" | "enabled";
  enabled: boolean;
  recovery_codes_remaining: number;
}

export interface MFAEnrollment {
  status: "pending";
  secret: string;
  provisioning_uri: string;
  expires_at: string;
}

export interface MFARecoveryCodesResponse {
  status: "enabled" | "regenerated";
  recovery_codes: string[];
  sessions_revoked: boolean;
  revoked_sessions: number;
}

export interface AuthSession {
  id: string;
  created_at: string;
  expires_at: string;
  last_seen_at: string;
  revoked_at: string | null;
}

const API_BASE = import.meta.env.VITE_API_URL || "";
let accessToken: string | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof URLSearchParams
        ? { "Content-Type": "application/x-www-form-urlencoded" }
        : { "Content-Type": "application/json" }),
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...init?.headers
    }
  });
  if (!response.ok) {
    let message = `Ошибка API: ${response.status}`;
    try {
      const body = await response.json();
      message = typeof body.detail === "string" ? body.detail : message;
    } catch { /* response did not contain JSON */ }
    if (response.status === 401) setAccessToken(null);
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function downloadAssetCsv(): Promise<Blob> {
  const response = await fetch(`${API_BASE}/api/v1/assets/export`, {
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {}
  });
  if (!response.ok) throw new Error(`Ошибка API: ${response.status}`);
  return response.blob();
}

async function uploadAssetCsv(file: File): Promise<{ created: number; skipped: number; errors: { row: number; error: string }[] }> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(`${API_BASE}/api/v1/assets/import`, {
    method: "POST",
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    body
  });
  if (!response.ok) {
    let message = `Ошибка API: ${response.status}`;
    try {
      const result = await response.json();
      if (typeof result.detail === "string") message = result.detail;
    } catch { /* response did not contain JSON */ }
    if (response.status === 401) setAccessToken(null);
    throw new Error(message);
  }
  return response.json();
}

export const api = {
  health: () => request<{ status: string }>("/health"),
  login: (email: string, password: string) =>
    request<LoginResponse>("/api/v1/auth/token", {
      method: "POST",
      body: new URLSearchParams({ username: email, password })
    }),
  mfaStatus: () => request<MFAStatus>("/api/v1/auth/mfa/status"),
  beginMfaEnrollment: () =>
    request<MFAEnrollment>("/api/v1/auth/mfa/enrollment", { method: "POST" }),
  confirmMfaEnrollment: (code: string) =>
    request<MFARecoveryCodesResponse>("/api/v1/auth/mfa/enrollment/confirm", {
      method: "POST", body: JSON.stringify({ code })
    }),
  verifyMfaChallenge: (challenge_token: string, code: string) =>
    request<TokenResponse>("/api/v1/auth/mfa/challenge/verify", {
      method: "POST", body: JSON.stringify({ challenge_token, code })
    }),
  regenerateMfaRecoveryCodes: (current_password: string, code: string) =>
    request<MFARecoveryCodesResponse>("/api/v1/auth/mfa/recovery-codes/regenerate", {
      method: "POST", body: JSON.stringify({ current_password, code })
    }),
  me: () => request<CurrentUser>("/api/v1/auth/me"),
  logout: () => request<{ status: string; revoked_sessions: string }>("/api/v1/auth/logout", { method: "POST" }),
  logoutAll: () => request<{ status: string; revoked_sessions: string }>("/api/v1/auth/logout-all", { method: "POST" }),
  authSessions: () => request<AuthSession[]>("/api/v1/auth/sessions"),
  revokeAuthSession: (id: string) => request<{ status: string }>(`/api/v1/auth/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  changePassword: (current_password: string, new_password: string) =>
    request<{ status: string }>("/api/v1/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password })
    }),
  users: () => request<ManagedUser[]>("/api/v1/users"),
  createUser: (user: { email: string; full_name: string; password: string; role: UserRole }) =>
    request<ManagedUser>("/api/v1/users", { method: "POST", body: JSON.stringify(user) }),
  updateUser: (id: number, changes: { expected_version: number; full_name?: string; role?: UserRole; is_active?: boolean }) =>
    request<ManagedUser>(`/api/v1/users/${id}`, { method: "PATCH", body: JSON.stringify(changes) }),
  auditEvents: (limit = 100) => request<AuditEvent[]>(`/api/v1/audit-events?limit=${limit}`),
  departments: () => request<Department[]>("/api/v1/departments"),
  createDepartment: (item: { name: string; code: string }) =>
    request<Department>("/api/v1/departments", { method: "POST", body: JSON.stringify(item) }),
  updateDepartment: (id: number, item: { expected_version: number; name?: string; code?: string }) =>
    request<Department>(`/api/v1/departments/${id}`, { method: "PATCH", body: JSON.stringify(item) }),
  employees: (search = "") => request<Employee[]>(`/api/v1/employees${search ? `?search=${encodeURIComponent(search)}` : ""}`),
  createEmployee: (item: EmployeeInput) =>
    request<Employee>("/api/v1/employees", { method: "POST", body: JSON.stringify(item) }),
  updateEmployee: (id: number, item: Partial<EmployeeInput> & { expected_version: number; is_active?: boolean }) =>
    request<Employee>(`/api/v1/employees/${id}`, { method: "PATCH", body: JSON.stringify(item) }),
  deleteEmployee: (id: number) => request<void>(`/api/v1/employees/${id}`, { method: "DELETE" }),
  maintenanceRequests: () => request<MaintenanceRequest[]>("/api/v1/maintenance-requests?limit=200"),
  createMaintenanceRequest: (item: MaintenanceRequestInput) =>
    request<MaintenanceRequest>("/api/v1/maintenance-requests", { method: "POST", body: JSON.stringify(item) }),
  updateMaintenanceRequest: (id: number, changes: { expected_version: number; status?: MaintenanceStatus; assigned_to_user_id?: number | null }) =>
    request<MaintenanceRequest>(`/api/v1/maintenance-requests/${id}`, { method: "PATCH", body: JSON.stringify(changes) }),
  softwareLicenses: () => request<SoftwareLicense[]>("/api/v1/software-licenses?limit=200"),
  createSoftwareLicense: (license: SoftwareLicenseInput) =>
    request<SoftwareLicense>("/api/v1/software-licenses", { method: "POST", body: JSON.stringify(license) }),
  updateSoftwareLicense: (id: number, license: Partial<SoftwareLicenseInput> & { expected_version: number }) =>
    request<SoftwareLicense>(`/api/v1/software-licenses/${id}`, { method: "PATCH", body: JSON.stringify(license) }),
  deleteSoftwareLicense: (id: number) =>
    request<void>(`/api/v1/software-licenses/${id}`, { method: "DELETE" }),
  assets: (search = "") => request<Asset[]>(`/api/v1/assets?limit=200${search ? `&search=${encodeURIComponent(search)}` : ""}`),
  assetHistory: (id: number) => request<AssetHistoryEvent[]>(`/api/v1/assets/${id}/history?limit=100`),
  bulkUpdateAssets: (payload: { asset_ids: number[]; versions: Record<number, number>; status?: AssetStatus; assigned_to?: string | null }) =>
    request<{ selected: number; updated: number; assets: Asset[] }>("/api/v1/assets/bulk", {
      method: "PATCH", body: JSON.stringify(payload)
    }),
  exportAssetsCsv: () => downloadAssetCsv(),
  importAssetsCsv: (file: File) => uploadAssetCsv(file),
  createAsset: (asset: AssetInput) => request<Asset>("/api/v1/assets", { method: "POST", body: JSON.stringify(asset) }),
  updateAsset: (id: number, asset: Partial<AssetInput> & { expected_version: number }) => request<Asset>(`/api/v1/assets/${id}`, { method: "PATCH", body: JSON.stringify(asset) }),
  deleteAsset: (id: number) => request<void>(`/api/v1/assets/${id}`, { method: "DELETE" })
};
