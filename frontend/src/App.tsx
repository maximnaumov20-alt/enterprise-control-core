import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, Asset, AssetInput, AssetStatus, AssetHistoryEvent, AuditEvent, CurrentUser, Department, Employee, EmployeeInput, MaintenancePriority, MaintenanceRequest, MaintenanceRequestInput, MaintenanceStatus, ManagedUser, SoftwareLicense, SoftwareLicenseInput, SoftwareLicenseStatus, SoftwareLicenseType, UserRole, setAccessToken } from "./api";

const emptyForm: AssetInput = {
  name: "", asset_tag: "", category: "Компьютер", manufacturer: "",
  model: "", serial_number: "", assigned_to: "", status: "active"
};

const statusLabels: Record<AssetStatus, string> = {
  active: "В эксплуатации", in_repair: "В ремонте", retired: "Списано"
};

const emptySoftwareForm: SoftwareLicenseInput = {
  name: "", publisher: "", version: "", license_type: "subscription",
  license_reference: "", seats_total: 1, seats_used: 0,
  purchased_at: null, expires_at: null, status: "active", notes: ""
};

const licenseTypeLabels: Record<SoftwareLicenseType, string> = {
  subscription: "Подписка", perpetual: "Бессрочная", oem: "OEM", site: "Корпоративная"
};

const licenseStatusLabels: Record<SoftwareLicenseStatus, string> = {
  active: "Активна", suspended: "Приостановлена", retired: "Закрыта"
};

const emptyEmployeeForm: EmployeeInput = {
  employee_number: "", full_name: "", department_id: null,
  email: null, job_title: "", phone: ""
};

const emptyMaintenanceForm: MaintenanceRequestInput = {
  asset_id: 0, title: "", description: "", priority: "normal"
};

const maintenanceStatusLabels: Record<MaintenanceStatus, string> = {
  open: "Открыта", in_progress: "В работе", resolved: "Решена", cancelled: "Отменена"
};

const maintenancePriorityLabels: Record<MaintenancePriority, string> = {
  low: "Низкий", normal: "Обычный", high: "Высокий", urgent: "Срочный"
};

export default function App() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [form, setForm] = useState<AssetInput>(emptyForm);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [assetImportFile, setAssetImportFile] = useState<File | null>(null);
  const [assetImporting, setAssetImporting] = useState(false);
  const [assetImportSummary, setAssetImportSummary] = useState<{ created: number; skipped: number; errors: { row: number; error: string }[] } | null>(null);
  const [historyAsset, setHistoryAsset] = useState<Asset | null>(null);
  const [assetHistory, setAssetHistory] = useState<AssetHistoryEvent[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [apiOnline, setApiOnline] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);
  const [section, setSection] = useState<"assets" | "software" | "maintenance" | "employees" | "users" | "audit">("assets");
  const [departments, setDepartments] = useState<Department[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [employeeForm, setEmployeeForm] = useState<EmployeeInput>(emptyEmployeeForm);
  const [editingEmployeeId, setEditingEmployeeId] = useState<number | null>(null);
  const [employeeSaving, setEmployeeSaving] = useState(false);
  const [departmentForm, setDepartmentForm] = useState({ name: "", code: "" });
  const [departmentSaving, setDepartmentSaving] = useState(false);
  const [maintenanceRequests, setMaintenanceRequests] = useState<MaintenanceRequest[]>([]);
  const [maintenanceForm, setMaintenanceForm] = useState<MaintenanceRequestInput>(emptyMaintenanceForm);
  const [maintenanceSaving, setMaintenanceSaving] = useState(false);
  const [softwareLicenses, setSoftwareLicenses] = useState<SoftwareLicense[]>([]);
  const [softwareForm, setSoftwareForm] = useState<SoftwareLicenseInput>(emptySoftwareForm);
  const [editingSoftwareId, setEditingSoftwareId] = useState<number | null>(null);
  const [softwareSaving, setSoftwareSaving] = useState(false);
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [userForm, setUserForm] = useState({ email: "", full_name: "", password: "", role: "viewer" as UserRole });
  const [userSaving, setUserSaving] = useState(false);
  const [showPasswordForm, setShowPasswordForm] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [passwordSaving, setPasswordSaving] = useState(false);

  async function loadAssets(term = search) {
    setLoading(true);
    try {
      const [items, health] = await Promise.all([api.assets(term), api.health()]);
      setAssets(items);
      setApiOnline(health.status === "ok");
      setError("");
    } catch (e) {
      setApiOnline(false);
      if (e instanceof Error && (e.message.includes("token") || e.message.includes("401"))) {
        setToken(null);
        setCurrentUser(null);
      }
      setError(e instanceof Error ? e.message : "Не удалось загрузить данные");
    } finally { setLoading(false); }
  }

  useEffect(() => {
    if (token) void loadAssets("");
    else { setAssets([]); setApiOnline(false); }
  }, [token]);

  async function handleLogin(event: FormEvent) {
    event.preventDefault();
    setLoginLoading(true);
    setError("");
    try {
      const result = await api.login(email.trim().toLowerCase(), password);
      setAccessToken(result.access_token);
      const user = await api.me();
      setCurrentUser(user);
      setToken(result.access_token);
      setPassword("");
    } catch (e) {
      setAccessToken(null);
      setToken(null);
      setCurrentUser(null);
      setError(e instanceof Error ? e.message : "Не удалось войти");
    } finally { setLoginLoading(false); }
  }

  function logout() {
    setAccessToken(null);
    setToken(null);
    setCurrentUser(null);
    resetForm();
  }

  async function submitPasswordChange(event: FormEvent) {
    event.preventDefault();
    setPasswordSaving(true);
    setError("");
    try {
      await api.changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setShowPasswordForm(false);
      setNotice("Пароль успешно изменён.");
    } catch (e) {
      setNotice("");
      setError(e instanceof Error ? e.message : "Не удалось изменить пароль");
    } finally { setPasswordSaving(false); }
  }

  async function openEmployees() {
    setSection("employees");
    setError("");
    try {
      const [employeeItems, departmentItems] = await Promise.all([api.employees(), api.departments()]);
      setEmployees(employeeItems);
      setDepartments(departmentItems);
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось загрузить сотрудников"); }
  }

  function beginEmployeeEdit(item: Employee) {
    setEditingEmployeeId(item.id);
    setEmployeeForm({
      employee_number: item.employee_number,
      full_name: item.full_name,
      department_id: item.department_id,
      email: item.email,
      job_title: item.job_title || "",
      phone: item.phone || ""
    });
  }

  async function submitEmployee(event: FormEvent) {
    event.preventDefault();
    setEmployeeSaving(true);
    setError("");
    try {
      const payload = {
        ...employeeForm,
        employee_number: employeeForm.employee_number.trim(),
        full_name: employeeForm.full_name.trim(),
        email: employeeForm.email?.trim() || null,
        job_title: employeeForm.job_title?.trim() || null,
        phone: employeeForm.phone?.trim() || null
      };
      if (editingEmployeeId === null) await api.createEmployee(payload);
      else await api.updateEmployee(editingEmployeeId, payload);
      setEditingEmployeeId(null);
      setEmployeeForm(emptyEmployeeForm);
      setEmployees(await api.employees());
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось сохранить сотрудника"); }
    finally { setEmployeeSaving(false); }
  }

  async function createDepartment(event: FormEvent) {
    event.preventDefault();
    setDepartmentSaving(true);
    setError("");
    try {
      await api.createDepartment({
        name: departmentForm.name.trim(),
        code: departmentForm.code.trim().toUpperCase()
      });
      setDepartmentForm({ name: "", code: "" });
      setDepartments(await api.departments());
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось создать подразделение"); }
    finally { setDepartmentSaving(false); }
  }

  async function toggleEmployee(employee: Employee) {
    try {
      await api.updateEmployee(employee.id, { is_active: !employee.is_active });
      setEmployees(await api.employees());
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось изменить статус сотрудника"); }
  }

  async function openMaintenance() {
    setSection("maintenance");
    setError("");
    try {
      const [requests, assetItems] = await Promise.all([api.maintenanceRequests(), api.assets("")]);
      setMaintenanceRequests(requests);
      setAssets(assetItems);
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось загрузить заявки"); }
  }

  async function submitMaintenance(event: FormEvent) {
    event.preventDefault();
    setMaintenanceSaving(true);
    setError("");
    try {
      await api.createMaintenanceRequest({
        ...maintenanceForm,
        title: maintenanceForm.title.trim(),
        description: maintenanceForm.description.trim()
      });
      setMaintenanceForm(emptyMaintenanceForm);
      setMaintenanceRequests(await api.maintenanceRequests());
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось создать заявку"); }
    finally { setMaintenanceSaving(false); }
  }

  async function changeMaintenanceStatus(item: MaintenanceRequest, nextStatus: MaintenanceStatus) {
    setError("");
    try {
      await api.updateMaintenanceRequest(item.id, { status: nextStatus });
      setMaintenanceRequests(await api.maintenanceRequests());
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось обновить заявку"); }
  }

  async function openSoftware() {
    setSection("software");
    setError("");
    try { setSoftwareLicenses(await api.softwareLicenses()); }
    catch (e) { setError(e instanceof Error ? e.message : "Не удалось загрузить лицензии"); }
  }

  function beginSoftwareEdit(item: SoftwareLicense) {
    setEditingSoftwareId(item.id);
    setSoftwareForm({
      name: item.name,
      publisher: item.publisher || "",
      version: item.version || "",
      license_type: item.license_type,
      license_reference: item.license_reference || "",
      seats_total: item.seats_total,
      seats_used: item.seats_used,
      purchased_at: item.purchased_at,
      expires_at: item.expires_at,
      status: item.status,
      notes: item.notes || ""
    });
  }

  async function submitSoftware(event: FormEvent) {
    event.preventDefault();
    setSoftwareSaving(true);
    setError("");
    try {
      const payload = {
        ...softwareForm,
        name: softwareForm.name.trim(),
        publisher: softwareForm.publisher?.trim() || null,
        version: softwareForm.version?.trim() || null,
        license_reference: softwareForm.license_reference?.trim() || null,
        notes: softwareForm.notes?.trim() || null
      };
      if (editingSoftwareId === null) await api.createSoftwareLicense(payload);
      else await api.updateSoftwareLicense(editingSoftwareId, payload);
      setEditingSoftwareId(null);
      setSoftwareForm(emptySoftwareForm);
      setSoftwareLicenses(await api.softwareLicenses());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить лицензию");
    } finally { setSoftwareSaving(false); }
  }

  async function removeSoftware(item: SoftwareLicense) {
    if (!window.confirm(`Удалить запись о лицензии «${item.name}»?`)) return;
    try {
      await api.deleteSoftwareLicense(item.id);
      setSoftwareLicenses(await api.softwareLicenses());
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось удалить лицензию"); }
  }

  async function openUsers() {
    setSection("users");
    setError("");
    try { setUsers(await api.users()); }
    catch (e) { setError(e instanceof Error ? e.message : "Не удалось загрузить пользователей"); }
  }

  async function openAudit() {
    setSection("audit");
    setError("");
    try { setEvents(await api.auditEvents()); }
    catch (e) { setError(e instanceof Error ? e.message : "Не удалось загрузить журнал"); }
  }

  async function createUser(event: FormEvent) {
    event.preventDefault();
    setUserSaving(true);
    setError("");
    try {
      await api.createUser(userForm);
      setUserForm({ email: "", full_name: "", password: "", role: "viewer" });
      setUsers(await api.users());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось создать пользователя");
    } finally { setUserSaving(false); }
  }

  async function changeUser(user: ManagedUser, changes: { role?: UserRole; is_active?: boolean }) {
    setError("");
    try {
      await api.updateUser(user.id, changes);
      setUsers(await api.users());
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось обновить пользователя"); }
  }

  const counts = useMemo(() => ({
    total: assets.length,
    active: assets.filter(a => a.status === "active").length,
    repair: assets.filter(a => a.status === "in_repair").length
  }), [assets]);

  function beginEdit(asset: Asset) {
    setEditingId(asset.id);
    setForm({
      name: asset.name, asset_tag: asset.asset_tag, category: asset.category,
      manufacturer: asset.manufacturer || "", model: asset.model || "",
      serial_number: asset.serial_number || "", assigned_to: asset.assigned_to || "",
      status: asset.status
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function resetForm() { setEditingId(null); setForm(emptyForm); }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const payload = { ...form,
        name: form.name.trim(), asset_tag: form.asset_tag.trim(), category: form.category.trim(),
        manufacturer: form.manufacturer?.trim() || null,
        model: form.model?.trim() || null,
        serial_number: form.serial_number?.trim() || null,
        assigned_to: form.assigned_to?.trim() || null
      };
      if (editingId === null) await api.createAsset(payload);
      else await api.updateAsset(editingId, payload);
      resetForm();
      await loadAssets();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить запись");
    } finally { setSaving(false); }
  }

  async function openAssetHistory(asset: Asset) {
    setHistoryAsset(asset);
    setAssetHistory([]);
    setHistoryLoading(true);
    try {
      setAssetHistory(await api.assetHistory(asset.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить историю актива");
      setHistoryAsset(null);
    } finally { setHistoryLoading(false); }
  }

  async function exportAssets() {
    setError("");
    try {
      const blob = await api.exportAssetsCsv();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "ecc-assets.csv";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось экспортировать активы"); }
  }

  async function importAssets() {
    if (!assetImportFile) return;
    if (!window.confirm(`Импортировать активы из файла «${assetImportFile.name}»? Дубликаты будут пропущены.`)) return;
    setAssetImporting(true);
    setError("");
    setAssetImportSummary(null);
    try {
      const result = await api.importAssetsCsv(assetImportFile);
      setAssetImportSummary(result);
      setAssetImportFile(null);
      await loadAssets();
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось импортировать активы"); }
    finally { setAssetImporting(false); }
  }

  async function removeAsset(asset: Asset) {
    if (!window.confirm(`Удалить оборудование «${asset.name}»?`)) return;
    try {
      await api.deleteAsset(asset.id);
      await loadAssets();
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось удалить запись"); }
  }

  if (!token || !currentUser) return <div className="login-shell">
    <form className="login-card" onSubmit={handleLogin}>
      <div className="brand login-brand"><div className="brand-mark">E</div><div><strong>ECC</strong><span>CONTROL CENTER</span></div></div>
      <div className="eyebrow">ЗАЩИЩЁННЫЙ ДОСТУП</div>
      <h1>Вход в систему</h1>
      <p>Войдите под учётной записью вашей организации</p>
      {error && <div className="alert">{error}</div>}
      <label>Электронная почта<input type="email" required autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} /></label>
      <label>Пароль<input type="password" required autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} /></label>
      <button className="primary" type="submit" disabled={loginLoading}>{loginLoading ? "Вход..." : "Войти"}</button>
    </form>
  </div>;

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">E</div><div><strong>ECC</strong><span>CONTROL CENTER</span></div></div>
      <div className="nav-caption">РАБОЧЕЕ ПРОСТРАНСТВО</div>
      <button type="button" className={`nav-item ${section === "assets" ? "active" : ""}`} onClick={() => setSection("assets")}><span>▦</span> Оборудование</button>
      <button type="button" className={`nav-item ${section === "software" ? "active" : ""}`} onClick={() => void openSoftware()}><span>▤</span> Лицензии ПО</button>
      <button type="button" className={`nav-item ${section === "maintenance" ? "active" : ""}`} onClick={() => void openMaintenance()}><span>⚙</span> Обслуживание</button>
      {currentUser.role !== "viewer" && <button type="button" className={`nav-item ${section === "employees" ? "active" : ""}`} onClick={() => void openEmployees()}><span>♙</span> Сотрудники</button>}
      {currentUser.role === "admin" && <>
        <button type="button" className={`nav-item ${section === "users" ? "active" : ""}`} onClick={() => void openUsers()}><span>♙</span> Пользователи</button>
        <button type="button" className={`nav-item ${section === "audit" ? "active" : ""}`} onClick={() => void openAudit()}><span>≡</span> Журнал действий</button>
      </>}
       
      <div className="sidebar-bottom"><span className={apiOnline ? "dot online" : "dot"} /> API {apiOnline ? "подключён" : "не подключён"}<small>Версия 0.2.0 · ранняя сборка</small></div>
    </aside>

    <main className="main">
      {historyAsset && <div className="modal-overlay" role="presentation" onClick={() => setHistoryAsset(null)}>
        <section className="modal-card history-modal" role="dialog" aria-modal="true" aria-labelledby="asset-history-title" onClick={e => e.stopPropagation()}>
          <div className="panel-heading"><div><h2 id="asset-history-title">История оборудования</h2><p>{historyAsset.name} · {historyAsset.asset_tag}</p></div><button type="button" className="subtle" onClick={() => setHistoryAsset(null)}>Закрыть</button></div>
          {historyLoading ? <div className="empty">Загрузка истории...</div> : assetHistory.length === 0 ? <div className="empty">Записей пока нет.</div> : <ol className="history-list">
            {assetHistory.map(event => {
              const changes = (event.details.changes || {}) as Record<string, { old: unknown; new: unknown }>;
              const labels: Record<string, string> = { name: "Название", asset_tag: "Инвентарный номер", category: "Категория", manufacturer: "Производитель", model: "Модель", serial_number: "Серийный номер", assigned_to: "Ответственный", status: "Статус" };
              const actionLabels: Record<string, string> = { "asset.created": "Создание актива", "asset.imported": "Импорт актива", "asset.updated": "Изменение актива", "asset.deleted": "Удаление актива" };
              return <li key={event.id}>
                <div className="history-event-head"><strong>{actionLabels[event.action] || event.action}</strong><time>{new Date(event.created_at).toLocaleString()}</time></div>
                <small>Пользователь ID: {event.actor_user_id ?? "учётная запись удалена"}</small>
                {Object.entries(changes).map(([field, values]) => <div className="history-change" key={field}><span>{labels[field] || field}:</span> {String(values.old ?? "—")} → {String(values.new ?? "—")}</div>)}
                {!Object.keys(changes).length && <div className="history-change">{String(event.details.name || event.details.asset_tag || "")}</div>}
              </li>;
            })}
          </ol>}
        </section>
      </div>}
      {showPasswordForm && <div className="modal-overlay"><form className="modal-card" onSubmit={submitPasswordChange}>
        <div className="panel-heading"><div><h2>Смена пароля</h2><p>Новый пароль должен содержать не менее 12 символов</p></div><button type="button" className="subtle" onClick={() => setShowPasswordForm(false)}>Закрыть</button></div>
        <label>Текущий пароль<input type="password" required autoComplete="current-password" value={currentPassword} onChange={e => setCurrentPassword(e.target.value)} /></label>
        <label>Новый пароль<input type="password" required minLength={12} maxLength={128} autoComplete="new-password" value={newPassword} onChange={e => setNewPassword(e.target.value)} /></label>
        <button className="primary" type="submit" disabled={passwordSaving}>{passwordSaving ? "Сохранение..." : "Изменить пароль"}</button>
      </form></div>}
      {notice && <div className="notice">{notice}</div>}
      <header className="topbar"><div className="crumb">Рабочее пространство <span>/</span> {section === "assets" ? "Оборудование" : section === "software" ? "Лицензии ПО" : section === "maintenance" ? "Обслуживание" : section === "employees" ? "Сотрудники" : section === "users" ? "Пользователи" : "Журнал действий"}</div><div className="top-user"><div><strong>{currentUser.full_name}</strong><small>{currentUser.organization_name} · {{ admin: "Администратор", editor: "Оператор", viewer: "Наблюдатель" }[currentUser.role]}</small></div><button className="subtle" onClick={() => { setError(""); setShowPasswordForm(true); }}>Сменить пароль</button><button className="subtle" onClick={logout}>Выйти</button></div></header>
      {section === "assets" && <>
      <section className="page-heading"><div><div className="eyebrow">УПРАВЛЕНИЕ АКТИВАМИ</div><h1>Оборудование</h1><p>Единый реестр IT-активов организации</p></div>{currentUser.role !== "viewer" && <button className="primary" onClick={() => { resetForm(); document.getElementById("asset-form")?.scrollIntoView({ behavior: "smooth" }); }}>＋ Добавить актив</button>}</section>

      <section className="stats">
        <div className="stat-card"><span>Всего в реестре</span><strong>{counts.total}</strong><small>Записей по текущему запросу</small></div>
        <div className="stat-card"><span>В эксплуатации</span><strong>{counts.active}</strong><small>Активное оборудование</small></div>
        <div className="stat-card"><span>В ремонте</span><strong>{counts.repair}</strong><small>Требует обслуживания</small></div>
      </section>

      <section className="panel transfer-panel">
        <div className="panel-heading"><div><h2>Импорт и экспорт</h2><p>CSV, UTF-8 · до 5 000 строк за импорт · дубликаты пропускаются</p></div>
          <button type="button" className="subtle" onClick={() => void exportAssets()}>Экспорт CSV</button>
        </div>
        {currentUser.role !== "viewer" && <div className="transfer-actions">
          <label>Файл CSV<input type="file" accept=".csv,text/csv" onChange={e => { setAssetImportFile(e.target.files?.[0] || null); setAssetImportSummary(null); }} /></label>
          <button type="button" className="primary" disabled={!assetImportFile || assetImporting} onClick={() => void importAssets()}>{assetImporting ? "Импорт..." : "Импортировать"}</button>
        </div>}
        {assetImportSummary && <div className="import-summary">
          <strong>Создано: {assetImportSummary.created} · Пропущено: {assetImportSummary.skipped}</strong>
          {assetImportSummary.errors.length > 0 && <ul>{assetImportSummary.errors.slice(0, 20).map((item, index) => <li key={index}>Строка {item.row}: {item.error}</li>)}</ul>}
          {assetImportSummary.errors.length > 20 && <small>Показаны первые 20 ошибок из {assetImportSummary.errors.length}.</small>}
        </div>}
      </section>

      {currentUser.role !== "viewer" && <section className="panel" id="asset-form">
        <div className="panel-heading"><div><h2>{editingId === null ? "Добавить оборудование" : "Редактировать оборудование"}</h2><p>Основные сведения об IT-активе</p></div>{editingId !== null && <button className="subtle" onClick={resetForm}>Отмена</button>}</div>
        <form onSubmit={submit}>
          <div className="form-grid">
            <label>Название *<input required maxLength={160} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="Например, Рабочая станция" /></label>
            <label>Инвентарный номер *<input required maxLength={80} value={form.asset_tag} onChange={e => setForm({ ...form, asset_tag: e.target.value })} placeholder="ECC-0001" disabled={editingId !== null} /></label>
            <label>Категория *<select value={form.category} onChange={e => setForm({ ...form, category: e.target.value })}><option>Компьютер</option><option>Ноутбук</option><option>Сервер</option><option>Монитор</option><option>Принтер</option><option>Сетевое оборудование</option><option>Другое</option></select></label>
            <label>Статус<select value={form.status} onChange={e => setForm({ ...form, status: e.target.value as AssetStatus })}><option value="active">В эксплуатации</option><option value="in_repair">В ремонте</option><option value="retired">Списано</option></select></label>
            <label>Производитель<input maxLength={120} value={form.manufacturer || ""} onChange={e => setForm({ ...form, manufacturer: e.target.value })} placeholder="Dell, HP, Lenovo..." /></label>
            <label>Модель<input maxLength={120} value={form.model || ""} onChange={e => setForm({ ...form, model: e.target.value })} placeholder="Модель устройства" /></label>
            <label>Серийный номер<input maxLength={120} value={form.serial_number || ""} onChange={e => setForm({ ...form, serial_number: e.target.value })} /></label>
            <label>Ответственный сотрудник<input maxLength={160} value={form.assigned_to || ""} onChange={e => setForm({ ...form, assigned_to: e.target.value })} placeholder="ФИО" /></label>
          </div>
          <div className="form-actions"><span className="required-note">* Обязательные поля</span><button className="primary" type="submit" disabled={saving}>{saving ? "Сохранение..." : editingId === null ? "Сохранить актив" : "Сохранить изменения"}</button></div>
        </form>
      </section>}

      <section className="panel inventory">
        <div className="panel-heading"><div><h2>Реестр оборудования</h2><p>Список зарегистрированных активов</p></div><input className="search" value={search} onChange={e => { setSearch(e.target.value); void loadAssets(e.target.value); }} placeholder="⌕  Поиск по названию..." /></div>
        {error && <div className="alert">{error}<button onClick={() => void loadAssets()}>Повторить</button></div>}
        {loading ? <div className="empty">Загрузка данных...</div> : assets.length === 0 ? <div className="empty">Активы не найдены. Добавьте первую запись в форму выше.</div> :
          <div className="table-wrap"><table><thead><tr><th>ОБОРУДОВАНИЕ</th><th>ИНВ. НОМЕР</th><th>КАТЕГОРИЯ</th><th>ОТВЕТСТВЕННЫЙ</th><th>СТАТУС</th><th>ДЕЙСТВИЯ</th></tr></thead><tbody>
            {assets.map(asset => <tr key={asset.id}><td><strong>{asset.name}</strong><small>{[asset.manufacturer, asset.model].filter(Boolean).join(" ") || "Производитель не указан"}</small></td><td className="mono">{asset.asset_tag}</td><td>{asset.category}</td><td>{asset.assigned_to || "—"}</td><td><span className={`status status-${asset.status}`}>{statusLabels[asset.status]}</span></td><td className="actions"><button title="История изменений" onClick={() => void openAssetHistory(asset)}>История</button>{currentUser.role !== "viewer" && <><button title="Редактировать" onClick={() => beginEdit(asset)}>Изменить</button><button className="danger" title="Удалить" onClick={() => void removeAsset(asset)}>Удалить</button></>}</td></tr>)}
          </tbody></table></div>}
      </section>
      <footer>Enterprise Control Center <span>·</span> Community Edition <span>·</span> Незавершённая разработка</footer>
      </>}

      {section === "software" && <section className="admin-section">
        <div className="page-heading"><div><div className="eyebrow">УЧЁТ ПРОГРАММНОГО ОБЕСПЕЧЕНИЯ</div><h1>Лицензии ПО</h1><p>Реестр договоров, типов лицензий и количества мест</p></div></div>
        {error && <div className="alert">{error}</div>}
        {currentUser.role !== "viewer" && <section className="panel">
          <div className="panel-heading"><div><h2>{editingSoftwareId === null ? "Добавить лицензию" : "Редактировать лицензию"}</h2><p>Не вводите ключи активации. Храните только внешний номер договора или заказа.</p></div>{editingSoftwareId !== null && <button className="subtle" onClick={() => { setEditingSoftwareId(null); setSoftwareForm(emptySoftwareForm); }}>Отмена</button>}</div>
          <form onSubmit={submitSoftware}>
            <div className="form-grid user-form-grid">
              <label>Название ПО *<input required maxLength={160} value={softwareForm.name} onChange={e => setSoftwareForm({ ...softwareForm, name: e.target.value })} /></label>
              <label>Издатель<input maxLength={120} value={softwareForm.publisher || ""} onChange={e => setSoftwareForm({ ...softwareForm, publisher: e.target.value })} /></label>
              <label>Версия<input maxLength={80} value={softwareForm.version || ""} onChange={e => setSoftwareForm({ ...softwareForm, version: e.target.value })} /></label>
              <label>Тип лицензии<select value={softwareForm.license_type} onChange={e => setSoftwareForm({ ...softwareForm, license_type: e.target.value as SoftwareLicenseType })}><option value="subscription">Подписка</option><option value="perpetual">Бессрочная</option><option value="oem">OEM</option><option value="site">Корпоративная</option></select></label>
              <label>Номер договора / заказа<input maxLength={120} value={softwareForm.license_reference || ""} onChange={e => setSoftwareForm({ ...softwareForm, license_reference: e.target.value })} /></label>
              <label>Всего мест *<input type="number" min={1} max={1000000} required value={softwareForm.seats_total} onChange={e => setSoftwareForm({ ...softwareForm, seats_total: Number(e.target.value) })} /></label>
              <label>Используется мест<input type="number" min={0} max={1000000} required value={softwareForm.seats_used} onChange={e => setSoftwareForm({ ...softwareForm, seats_used: Number(e.target.value) })} /></label>
              <label>Статус<select value={softwareForm.status} onChange={e => setSoftwareForm({ ...softwareForm, status: e.target.value as SoftwareLicenseStatus })}><option value="active">Активна</option><option value="suspended">Приостановлена</option><option value="retired">Закрыта</option></select></label>
              <label>Дата покупки<input type="date" value={softwareForm.purchased_at || ""} onChange={e => setSoftwareForm({ ...softwareForm, purchased_at: e.target.value || null })} /></label>
              <label>Дата окончания<input type="date" value={softwareForm.expires_at || ""} onChange={e => setSoftwareForm({ ...softwareForm, expires_at: e.target.value || null })} /></label>
              <label className="wide-field">Примечания<input maxLength={4000} value={softwareForm.notes || ""} onChange={e => setSoftwareForm({ ...softwareForm, notes: e.target.value })} /></label>
            </div>
            <div className="form-actions"><span className="required-note">Используемые места не могут превышать общее количество</span><button className="primary" type="submit" disabled={softwareSaving}>{softwareSaving ? "Сохранение..." : editingSoftwareId === null ? "Сохранить лицензию" : "Сохранить изменения"}</button></div>
          </form>
        </section>}
        <section className="panel inventory">
          <div className="panel-heading"><div><h2>Реестр лицензий</h2><p>{softwareLicenses.length} записей</p></div><button className="subtle" onClick={() => void openSoftware()}>Обновить</button></div>
          {softwareLicenses.length === 0 ? <div className="empty">Записей пока нет.</div> : <div className="table-wrap"><table><thead><tr><th>ПРОГРАММА</th><th>ТИП</th><th>ДОГОВОР</th><th>МЕСТА</th><th>ОКОНЧАНИЕ</th><th>СТАТУС</th>{currentUser.role !== "viewer" && <th>ДЕЙСТВИЯ</th>}</tr></thead><tbody>
            {softwareLicenses.map(item => <tr key={item.id}><td><strong>{item.name}</strong><small>{[item.publisher, item.version].filter(Boolean).join(" · ")}</small></td><td>{licenseTypeLabels[item.license_type]}</td><td className="mono">{item.license_reference || "—"}</td><td>{item.seats_used} / {item.seats_total}</td><td>{item.expires_at || "Без срока"}</td><td><span className={`status ${item.status === "active" ? "status-active" : item.status === "suspended" ? "status-in_repair" : "status-retired"}`}>{licenseStatusLabels[item.status]}</span></td>{currentUser.role !== "viewer" && <td className="actions"><button onClick={() => beginSoftwareEdit(item)}>Изменить</button><button className="danger" onClick={() => void removeSoftware(item)}>Удалить</button></td>}</tr>)}
          </tbody></table></div>}
        </section>
      </section>}

      {section === "maintenance" && <section className="admin-section">
        <div className="page-heading"><div><div className="eyebrow">ЗАЯВКИ НА РЕМОНТ И ОБСЛУЖИВАНИЕ</div><h1>Обслуживание</h1><p>Регистрация неисправностей и контроль выполнения работ</p></div></div>
        {error && <div className="alert">{error}</div>}
        <section className="panel">
          <div className="panel-heading"><div><h2>Создать заявку</h2><p>Выберите оборудование и опишите проблему</p></div></div>
          {assets.length === 0 ? <div className="empty">Сначала добавьте оборудование в реестр.</div> : <form onSubmit={submitMaintenance}>
            <div className="form-grid user-form-grid">
              <label>Оборудование *<select required value={maintenanceForm.asset_id} onChange={e => setMaintenanceForm({ ...maintenanceForm, asset_id: Number(e.target.value) })}><option value={0} disabled>Выберите актив</option>{assets.map(asset => <option key={asset.id} value={asset.id}>{asset.asset_tag} · {asset.name}</option>)}</select></label>
              <label>Тема заявки *<input required maxLength={160} value={maintenanceForm.title} onChange={e => setMaintenanceForm({ ...maintenanceForm, title: e.target.value })} /></label>
              <label>Приоритет<select value={maintenanceForm.priority} onChange={e => setMaintenanceForm({ ...maintenanceForm, priority: e.target.value as MaintenancePriority })}><option value="low">Низкий</option><option value="normal">Обычный</option><option value="high">Высокий</option><option value="urgent">Срочный</option></select></label>
              <label className="wide-field">Описание проблемы<textarea required maxLength={4000} rows={3} value={maintenanceForm.description} onChange={e => setMaintenanceForm({ ...maintenanceForm, description: e.target.value })} /></label>
            </div>
            <div className="form-actions"><span className="required-note">Заявка будет видна вашей организации согласно вашей роли</span><button className="primary" type="submit" disabled={maintenanceSaving}>{maintenanceSaving ? "Отправка..." : "Отправить заявку"}</button></div>
          </form>}
        </section>
        <section className="panel inventory">
          <div className="panel-heading"><div><h2>Заявки</h2><p>{maintenanceRequests.length} записей</p></div><button className="subtle" onClick={() => void openMaintenance()}>Обновить</button></div>
          {maintenanceRequests.length === 0 ? <div className="empty">Заявок пока нет.</div> : <div className="table-wrap"><table><thead><tr><th>ЗАЯВКА</th><th>АКТИВ ID</th><th>ИНИЦИАТОР</th><th>ПРИОРИТЕТ</th><th>СТАТУС</th><th>ДАТА</th></tr></thead><tbody>
            {maintenanceRequests.map(item => <tr key={item.id}><td><strong>{item.title}</strong><small>{item.description}</small></td><td>{item.asset_id}</td><td>{item.requested_by_user_id}</td><td>{maintenancePriorityLabels[item.priority]}</td><td>{currentUser.role === "viewer" ? <span className="status status-in_repair">{maintenanceStatusLabels[item.status]}</span> : <select value={item.status} onChange={e => void changeMaintenanceStatus(item, e.target.value as MaintenanceStatus)}><option value="open">Открыта</option><option value="in_progress">В работе</option><option value="resolved">Решена</option><option value="cancelled">Отменена</option></select>}</td><td>{new Date(item.created_at).toLocaleDateString()}</td></tr>)}
          </tbody></table></div>}
        </section>
      </section>}

      {section === "employees" && currentUser.role !== "viewer" && <section className="admin-section">
        <div className="page-heading"><div><div className="eyebrow">СТРУКТУРА ОРГАНИЗАЦИИ</div><h1>Сотрудники</h1><p>Справочник сотрудников и подразделений</p></div></div>
        {error && <div className="alert">{error}</div>}
        {currentUser.role === "admin" && <section className="panel">
          <div className="panel-heading"><div><h2>Добавить подразделение</h2><p>Короткий код используется в учётных процессах</p></div></div>
          <form className="department-form" onSubmit={createDepartment}>
            <label>Название<input required maxLength={120} value={departmentForm.name} onChange={e => setDepartmentForm({ ...departmentForm, name: e.target.value })} /></label>
            <label>Код<input required maxLength={40} value={departmentForm.code} onChange={e => setDepartmentForm({ ...departmentForm, code: e.target.value })} /></label>
            <button className="primary" type="submit" disabled={departmentSaving}>{departmentSaving ? "Сохранение..." : "Добавить"}</button>
          </form>
        </section>}
        <section className="panel">
          <div className="panel-heading"><div><h2>{editingEmployeeId === null ? "Добавить сотрудника" : "Редактировать сотрудника"}</h2><p>Контактные и организационные данные</p></div>{editingEmployeeId !== null && <button className="subtle" onClick={() => { setEditingEmployeeId(null); setEmployeeForm(emptyEmployeeForm); }}>Отмена</button>}</div>
          <form onSubmit={submitEmployee}>
            <div className="form-grid user-form-grid">
              <label>Табельный номер *<input required maxLength={40} value={employeeForm.employee_number} onChange={e => setEmployeeForm({ ...employeeForm, employee_number: e.target.value })} /></label>
              <label>ФИО *<input required maxLength={160} value={employeeForm.full_name} onChange={e => setEmployeeForm({ ...employeeForm, full_name: e.target.value })} /></label>
              <label>Подразделение<select value={employeeForm.department_id || 0} onChange={e => setEmployeeForm({ ...employeeForm, department_id: Number(e.target.value) || null })}><option value={0}>Не указано</option>{departments.map(department => <option key={department.id} value={department.id}>{department.name}</option>)}</select></label>
              <label>Должность<input maxLength={120} value={employeeForm.job_title || ""} onChange={e => setEmployeeForm({ ...employeeForm, job_title: e.target.value })} /></label>
              <label>Электронная почта<input type="email" value={employeeForm.email || ""} onChange={e => setEmployeeForm({ ...employeeForm, email: e.target.value })} /></label>
              <label>Телефон<input maxLength={40} value={employeeForm.phone || ""} onChange={e => setEmployeeForm({ ...employeeForm, phone: e.target.value })} /></label>
            </div>
            <div className="form-actions"><span className="required-note">Данные доступны администраторам и операторам организации</span><button className="primary" type="submit" disabled={employeeSaving}>{employeeSaving ? "Сохранение..." : editingEmployeeId === null ? "Добавить сотрудника" : "Сохранить изменения"}</button></div>
          </form>
        </section>
        <section className="panel inventory">
          <div className="panel-heading"><div><h2>Справочник сотрудников</h2><p>{employees.length} записей · {departments.length} подразделений</p></div><button className="subtle" onClick={() => void openEmployees()}>Обновить</button></div>
          {employees.length === 0 ? <div className="empty">Сотрудников пока нет.</div> : <div className="table-wrap"><table><thead><tr><th>СОТРУДНИК</th><th>ТАБЕЛЬНЫЙ №</th><th>ПОДРАЗДЕЛЕНИЕ</th><th>ДОЛЖНОСТЬ</th><th>КОНТАКТЫ</th><th>СТАТУС</th><th>ДЕЙСТВИЯ</th></tr></thead><tbody>
            {employees.map(employee => <tr key={employee.id}><td><strong>{employee.full_name}</strong></td><td className="mono">{employee.employee_number}</td><td>{departments.find(d => d.id === employee.department_id)?.name || "—"}</td><td>{employee.job_title || "—"}</td><td>{employee.email || employee.phone || "—"}</td><td><span className={`status ${employee.is_active ? "status-active" : "status-retired"}`}>{employee.is_active ? "Работает" : "Неактивен"}</span></td><td className="actions"><button onClick={() => beginEmployeeEdit(employee)}>Изменить</button><button className="danger" onClick={() => void toggleEmployee(employee)}>{employee.is_active ? "Отключить" : "Включить"}</button></td></tr>)}
          </tbody></table></div>}
        </section>
      </section>}

      {section === "users" && currentUser.role === "admin" && <section className="admin-section">
        <div className="page-heading"><div><div className="eyebrow">АДМИНИСТРИРОВАНИЕ</div><h1>Пользователи</h1><p>Управление доступом сотрудников к организации</p></div></div>
        {error && <div className="alert">{error}</div>}
        <section className="panel">
          <div className="panel-heading"><div><h2>Создать учётную запись</h2><p>Пароль должен содержать не менее 12 символов</p></div></div>
          <form onSubmit={createUser}>
            <div className="form-grid user-form-grid">
              <label>Электронная почта<input type="email" required value={userForm.email} onChange={e => setUserForm({ ...userForm, email: e.target.value })} /></label>
              <label>Имя сотрудника<input required maxLength={160} value={userForm.full_name} onChange={e => setUserForm({ ...userForm, full_name: e.target.value })} /></label>
              <label>Временный пароль<input type="password" required minLength={12} maxLength={128} autoComplete="new-password" value={userForm.password} onChange={e => setUserForm({ ...userForm, password: e.target.value })} /></label>
              <label>Роль<select value={userForm.role} onChange={e => setUserForm({ ...userForm, role: e.target.value as UserRole })}><option value="viewer">Наблюдатель</option><option value="editor">Оператор</option><option value="admin">Администратор</option></select></label>
            </div>
            <div className="form-actions"><span className="required-note">Доступ будет ограничен текущей организацией</span><button className="primary" type="submit" disabled={userSaving}>{userSaving ? "Создание..." : "Создать пользователя"}</button></div>
          </form>
        </section>
        <section className="panel inventory">
          <div className="panel-heading"><div><h2>Учётные записи организации</h2><p>{users.length} пользователей</p></div><button className="subtle" onClick={() => void openUsers()}>Обновить</button></div>
          <div className="table-wrap"><table><thead><tr><th>ПОЛЬЗОВАТЕЛЬ</th><th>РОЛЬ</th><th>СТАТУС</th><th>ДЕЙСТВИЯ</th></tr></thead><tbody>
            {users.map(user => <tr key={user.id}><td><strong>{user.full_name}</strong><small>{user.email}</small></td><td><select value={user.role} disabled={user.id === currentUser.id} onChange={e => void changeUser(user, { role: e.target.value as UserRole })}><option value="viewer">Наблюдатель</option><option value="editor">Оператор</option><option value="admin">Администратор</option></select></td><td><span className={`status ${user.is_active ? "status-active" : "status-retired"}`}>{user.is_active ? "Активен" : "Отключён"}</span></td><td>{user.id === currentUser.id ? "Текущая учётная запись" : <button className="subtle" onClick={() => void changeUser(user, { is_active: !user.is_active })}>{user.is_active ? "Отключить" : "Включить"}</button>}</td></tr>)}
          </tbody></table></div>
        </section>
      </section>}

      {section === "audit" && currentUser.role === "admin" && <section className="admin-section">
        <div className="page-heading"><div><div className="eyebrow">КОНТРОЛЬ И БЕЗОПАСНОСТЬ</div><h1>Журнал действий</h1><p>Последние события в текущей организации</p></div><button className="subtle" onClick={() => void openAudit()}>Обновить</button></div>
        {error && <div className="alert">{error}</div>}
        <section className="panel inventory">
          <div className="table-wrap"><table><thead><tr><th>ВРЕМЯ</th><th>ПОЛЬЗОВАТЕЛЬ ID</th><th>ДЕЙСТВИЕ</th><th>ОБЪЕКТ</th><th>ДЕТАЛИ</th></tr></thead><tbody>
            {events.map(event => <tr key={event.id}><td>{new Date(event.created_at).toLocaleString()}</td><td>{event.actor_user_id ?? "—"}</td><td><strong>{event.action}</strong></td><td>{event.entity_type} #{event.entity_id}</td><td className="audit-details">{JSON.stringify(event.details)}</td></tr>)}
          </tbody></table></div>
          {events.length === 0 && <div className="empty">Событий пока нет.</div>}
        </section>
      </section>}
    </main>
  </div>;
}