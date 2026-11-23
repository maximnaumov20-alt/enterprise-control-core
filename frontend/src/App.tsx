import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, Asset, AssetInput, AssetStatus, CurrentUser, setAccessToken } from "./api";

const emptyForm: AssetInput = {
  name: "", asset_tag: "", category: "Компьютер", manufacturer: "",
  model: "", serial_number: "", assigned_to: "", status: "active"
};

const statusLabels: Record<AssetStatus, string> = {
  active: "В эксплуатации", in_repair: "В ремонте", retired: "Списано"
};

export default function App() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [form, setForm] = useState<AssetInput>(emptyForm);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [apiOnline, setApiOnline] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);

  async function loadAssets(term = search) {
    setLoading(true);
    try {
      const [items, health] = await Promise.all([api.assets(term), api.health()]);
      setAssets(items);
      setApiOnline(health.status === "ok");
      setError("");
    } catch (e) {
      setApiOnline(false);
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
      <div className="nav-item active"><span>▦</span> Оборудование</div>
      <div className="nav-item disabled"><span>♙</span> Сотрудники <small>скоро</small></div>
      <div className="nav-item disabled"><span>▤</span> Программное обеспечение</div>
      <div className="nav-item disabled"><span>⚙</span> Обслуживание</div>
      <div className="sidebar-bottom"><span className={apiOnline ? "dot online" : "dot"} /> API {apiOnline ? "подключён" : "не подключён"}<small>Версия 0.1.0 · ранняя сборка</small></div>
    </aside>

    <main className="main">
      <header className="topbar"><div className="crumb">Рабочее пространство <span>/</span> Оборудование</div><div className="top-user"><div><strong>{currentUser.full_name}</strong><small>{currentUser.organization_name} · {currentUser.role}</small></div><button className="subtle" onClick={logout}>Выйти</button></div></header>
      <section className="page-heading"><div><div className="eyebrow">УПРАВЛЕНИЕ АКТИВАМИ</div><h1>Оборудование</h1><p>Единый реестр IT-активов организации</p></div>{currentUser.role !== "viewer" && <button className="primary" onClick={() => { resetForm(); document.getElementById("asset-form")?.scrollIntoView({ behavior: "smooth" }); }}>＋ Добавить актив</button>}</section>

      <section className="stats">
        <div className="stat-card"><span>Всего в реестре</span><strong>{counts.total}</strong><small>Записей по текущему запросу</small></div>
        <div className="stat-card"><span>В эксплуатации</span><strong>{counts.active}</strong><small>Активное оборудование</small></div>
        <div className="stat-card"><span>В ремонте</span><strong>{counts.repair}</strong><small>Требует обслуживания</small></div>
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
          <div className="table-wrap"><table><thead><tr><th>ОБОРУДОВАНИЕ</th><th>ИНВ. НОМЕР</th><th>КАТЕГОРИЯ</th><th>ОТВЕТСТВЕННЫЙ</th><th>СТАТУС</th><th></th></tr></thead><tbody>
            {assets.map(asset => <tr key={asset.id}><td><strong>{asset.name}</strong><small>{[asset.manufacturer, asset.model].filter(Boolean).join(" ") || "Производитель не указан"}</small></td><td className="mono">{asset.asset_tag}</td><td>{asset.category}</td><td>{asset.assigned_to || "—"}</td><td><span className={`status status-${asset.status}`}>{statusLabels[asset.status]}</span></td>{currentUser.role !== "viewer" && <td className="actions"><button title="Редактировать" onClick={() => beginEdit(asset)}>Изменить</button><button className="danger" title="Удалить" onClick={() => void removeAsset(asset)}>Удалить</button></td>}</tr>)}
          </tbody></table></div>}
      </section>
      <footer>Enterprise Control Center <span>·</span> Community Edition <span>·</span> Незавершённая разработка</footer>
    </main>
  </div>;
}