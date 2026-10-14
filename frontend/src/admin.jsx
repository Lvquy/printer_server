import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BusyContext, Button, FieldLabel, Input, Modal, Notice, fieldHelp, formatBytes, useToasts } from './shared.jsx';
import { AddPrinter, ApiKey, ClientForm, EditPrinter, EntityDetails, JobDetails } from './admin-forms.jsx';
import { ChangePassword } from './change-password.jsx';
import { ApiGuide } from './api-guide.jsx';

const tabs = [['overview', 'Tổng quan'], ['printers', 'Máy in'], ['clients', 'Clients'], ['jobs', 'Lệnh in'], ['settings', 'Cấu hình'], ['api-guide', 'API Guide']];
const hashTab = () => { const value = location.hash.startsWith('#tab=') ? location.hash.slice(5) : 'overview'; return tabs.some(([name]) => name === value) ? value : 'overview'; };
const settingFields = [
  ['max_upload_bytes', 'Tệp tối đa (byte)', 65536, 536870912, fieldHelp.settingsUpload],
  ['max_pending_jobs', 'Số lệnh đang xử lý tối đa', 1, 10000, fieldHelp.settingsJobs],
  ['min_free_bytes', 'Đĩa trống dự phòng (byte)', 0, undefined, fieldHelp.settingsDisk],
  ['history_retention_days', 'Giữ lịch sử (ngày)', 1, 3650, fieldHelp.settingsRetention],
];
function AdminApp() {
  const toasts = useToasts();
  const session = useRef({ csrf: '', epoch: 0, pending: false, alive: true });
  const settingsDirty = useRef(false), modalSequence = useRef(0);
  const [busy, setBusy] = useState(true), [authenticated, setAuthenticated] = useState(false), [password, setPassword] = useState('');
  const [tab, setTab] = useState(hashTab), [printers, setPrinters] = useState(null), [clients, setClients] = useState(null), [jobs, setJobs] = useState(null);
  const [status, setStatus] = useState(null), [statusError, setStatusError] = useState(''), [jobsError, setJobsError] = useState('');
  const [settings, setSettings] = useState({}), [discovery, setDiscovery] = useState(null), [discoveryState, setDiscoveryState] = useState(''), [modal, setModal] = useState(null);
  const drivers = React.useMemo(() => (discovery?.drivers || []).map(item => ({ id: item.id, label: `${item.make_model} · ${item.id}` })), [discovery]);
  const activateTab = (name, hash = true) => { setTab(name); if (hash) history.replaceState(null, '', `#tab=${name}`); };
  const reset = () => {
    session.current.epoch++; session.current.csrf = ''; setAuthenticated(false); setModal(null); toasts.clear();
    setPrinters(null); setClients(null); setJobs(null); setStatus(null); setStatusError(''); setJobsError(''); setSettings({}); setDiscovery(null); setDiscoveryState(''); setPassword(''); settingsDirty.current = false;
  };
  const api = async (path, options = {}) => {
    const s = session.current, epoch = s.epoch, headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    if (options.method && options.method !== 'GET' && s.csrf) headers.set('X-CSRF-Token', s.csrf);
    const response = await fetch(path, { credentials: 'same-origin', ...options, headers });
    const data = await response.json().catch(() => ({}));
    if (epoch !== s.epoch || !s.alive) { const error = new Error('Phiên làm việc đã thay đổi. Hãy đăng nhập lại.'); error.stale = true; throw error; }
    if (response.status === 401) reset();
    if (!response.ok) { const error = new Error(data.detail || `Request failed (${response.status})`); error.status = response.status; throw error; }
    return data;
  };
  // Ref guard is synchronous: two submits in one render cannot race. Mutations never retry.
  const run = async fn => {
    const s = session.current; if (s.pending) return;
    s.pending = true; setBusy(true);
    try { await fn(); } catch (error) { if (!error.stale && s.alive) toasts.show(error.message, 'error'); }
    finally { s.pending = false; if (s.alive) setBusy(false); }
  };
  const loadPrinters = async () => {
    try { const value = await api('/admin/api/printers'); setPrinters(value); return value; }
    catch (error) { if (!error.stale) toasts.show(`Lỗi tải máy in: ${error.message}`, 'error'); }
  };
  const loadClients = async () => { try { setClients(await api('/admin/api/clients')); } catch (error) { if (!error.stale) toasts.show(`Lỗi tải clients: ${error.message}`, 'error'); } };
  const loadJobs = async () => { try { setJobs(await api('/admin/api/jobs?limit=100')); setJobsError(''); } catch (error) { if (!error.stale) setJobsError(`Lỗi tải lệnh in: ${error.message}`); } };
  const loadStatus = async () => { try { setStatus(await api('/admin/api/status')); setStatusError(''); } catch (error) { if (!error.stale) setStatusError(`Lỗi tải trạng thái: ${error.message}`); } };
  const loadSettings = async () => { try { const value = await api('/admin/api/settings'); if (!settingsDirty.current) setSettings(value); } catch (error) { if (!error.stale) toasts.show(`Không tải được giới hạn: ${error.message}`, 'error'); } };
  const refreshAll = async () => {
    const epoch = session.current.epoch;
    // Grant controls need the printer list first; settings drafts stay independent of refreshes.
    await loadPrinters(); if (epoch !== session.current.epoch) return;
    await Promise.all([loadStatus(), loadClients(), loadJobs(), loadSettings()]);
  };
  const discover = async () => {
    setDiscoveryState('Đang tìm thiết bị và driver…');
    try { const value = await api('/admin/api/discovery'); setDiscovery(value); setDiscoveryState(`${value.devices.length} thiết bị · ${value.drivers.length.toLocaleString('vi-VN')} driver đã cài`); }
    catch (error) { if (!error.stale) setDiscoveryState(`Không tải được từ CUPS: ${error.message}`); throw error; }
  };
  const open = (kind, title, record = null, trigger = document.activeElement) => setModal({ kind, title, record, trigger, key: ++modalSequence.current });
  const close = () => setModal(null);
  const showEntity = async (kind, record, trigger) => {
    const value = await api(`/admin/api/${kind}/${encodeURIComponent(record.id)}`);
    open(`details-${kind}`, kind === 'printers' ? 'Chi tiết máy in' : 'Chi tiết client', value, trigger);
  };
  const showJob = async (job, trigger = modal?.trigger || document.activeElement) => {
    const value = await api(`/admin/api/jobs/${encodeURIComponent(job.job_id)}`); open('job', 'Chi tiết lệnh in', value, trigger);
  };
  const clientAction = async (client, action) => {
    const result = await api(`/admin/api/clients/${encodeURIComponent(client.id)}/${action}`, { method: 'POST', body: '{}' });
    if (result.api_key) open('key', 'API key mới', result.api_key);
    toasts.show(action === 'rotate-key' ? 'Đã tạo API key mới' : 'Đã thu hồi key', 'success'); await loadClients();
  };
  useEffect(() => {
    const onHash = () => activateTab(hashTab(), false); addEventListener('hashchange', onHash);
    run(async () => {
      try { const value = await api('/admin/api/session'); session.current.csrf = value.csrf_token || ''; setAuthenticated(value.authenticated); if (value.authenticated) await refreshAll(); }
      catch (error) { if (!error.stale) toasts.show(`Không thể kiểm tra phiên: ${error.message}`, 'error'); }
    });
    return () => { session.current.alive = false; session.current.epoch++; removeEventListener('hashchange', onHash); };
  }, []);
  useEffect(() => {
    if (authenticated) document.getElementById(`tab-${tab}`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [tab, authenticated]);
  const notices = <Notice id="notice" {...toasts} />;
  let modalContent = null;
  if (modal?.kind === 'add-printer') modalContent = <AddPrinter discovery={discovery} drivers={drivers} printers={printers || []} run={run} save={async (payload, importing) => {
    await api(importing ? '/admin/api/printers/import' : '/admin/api/printers', { method: 'POST', body: JSON.stringify(payload) }); close();
    toasts.show(importing ? 'Đã nhập queue nguyên trạng; CUPS chưa bị sửa.' : 'Đã tạo queue được quản lý; không xóa hay chiếm queue cũ.', 'success'); await loadPrinters(); if (!importing) await loadStatus();
  }} />;
  if (modal?.kind === 'edit-printer') modalContent = <EditPrinter printer={modal.record} drivers={drivers} api={api} run={run} discover={discover} save={async payload => {
    await api(`/admin/api/printers/${encodeURIComponent(modal.record.id)}`, { method: 'PUT', body: JSON.stringify(payload) }); close(); toasts.show('Đã lưu cấu hình máy in.', 'success'); await loadPrinters();
  }} />;
  if (modal?.kind === 'add-client' || modal?.kind === 'edit-client') modalContent = <ClientForm client={modal.record} printers={printers || []} run={run} save={async payload => {
    const editing = modal.kind === 'edit-client';
    const result = await api(editing ? `/admin/api/clients/${encodeURIComponent(modal.record.id)}` : '/admin/api/clients', { method: editing ? 'PUT' : 'POST', body: JSON.stringify(payload) });
    if (editing) close(); else open('key', 'API key mới', result.api_key, modal.trigger);
    toasts.show(editing ? 'Đã lưu client.' : 'Đã tạo client.', 'success'); await loadClients();
  }} />;
  if (modal?.kind === 'key') modalContent = <ApiKey value={modal.record} notice={toasts.show} />;
  if (modal?.kind.startsWith('details-')) modalContent = <EntityDetails kind={modal.kind.slice(8)} record={modal.record} printers={printers || []} />;
  if (modal?.kind === 'job') modalContent = <JobDetails job={modal.record} run={run} control={async action => {
    await api(`/admin/api/jobs/${encodeURIComponent(modal.record.job_id)}/${action}`, { method: 'POST', body: '{}' });
    toasts.show(action === 'cancel' ? 'Yêu cầu hủy đã được xử lý theo bằng chứng CUPS.' : 'Chỉ lệnh được chọn đã được tiếp tục.', 'success'); await refreshAll(); await showJob(modal.record);
  }} resolve={async payload => {
    await api(`/admin/api/jobs/${encodeURIComponent(modal.record.job_id)}/resolve`, { method: 'POST', body: JSON.stringify(payload) });
    toasts.show('Đã ghi nhận xử lý unknown; không gửi lại job.', 'success'); await refreshAll(); await showJob(modal.record);
  }} />;
  return <BusyContext.Provider value={busy}>
    <header className="topbar"><div><p className="eyebrow">THIẾT BỊ QUẢN LÝ IN</p><h1>Print appliance</h1></div><Button id="logout" className="quiet" hidden={!authenticated} onClick={() => run(async () => {
      await api('/admin/api/logout', { method: 'POST', body: '{}' }); reset(); activateTab('overview');
    })}>Đăng xuất</Button></header>
    <main><p id="busy-status" className="busy-status" role="status" hidden={!busy}>Đang xử lý…</p>{!modal && notices}
      <section id="login-panel" className="panel narrow" hidden={authenticated}><h2>Đăng nhập quản trị</h2><p><a href="/client">Gửi thử bằng API key của client</a></p>
        <form id="login-form" onSubmit={event => { event.preventDefault(); const value = password; run(async () => {
          toasts.clear(); const result = await api('/admin/api/login', { method: 'POST', body: JSON.stringify({ password: value }) }); session.current.csrf = result.csrf_token; setPassword(''); setAuthenticated(true); toasts.show('Đăng nhập thành công.', 'success'); await refreshAll();
        }); }}><FieldLabel htmlFor="password" explanation="Mật khẩu quản trị web được thiết lập trên thiết bị. Đây không phải mật khẩu SSH pi hay API key của client. Chỉ đăng nhập trên mạng nội bộ đáng tin cậy.">Mật khẩu quản trị</FieldLabel><Input id="password" name="password" type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)} /><Button type="submit">Đăng nhập</Button></form>
      </section>
      <div id="dashboard" hidden={!authenticated}>
        <nav className="tab-list" role="tablist" aria-label="Các chức năng quản trị">{tabs.map(([name, label], index) => <Button alwaysEnabled key={name} id={`tab-${name}`} role="tab" data-tab={name} aria-controls={`view-${name}`} aria-selected={tab === name} tabIndex={tab === name ? 0 : -1} onClick={() => activateTab(name)} onKeyDown={event => {
          const keys = { ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1 };
          if (!(event.key in keys)) return; event.preventDefault(); const next = tabs[keys[event.key]][0]; activateTab(next); document.getElementById(`tab-${next}`).focus();
        }}>{label}</Button>)}</nav>
        <section id="view-overview" className="tab-panel" role="tabpanel" aria-labelledby="tab-overview" tabIndex={0} hidden={tab !== 'overview'}><div className="overview"><article className="panel"><h2>Hệ thống</h2><div id="system-status" className="loading">{statusError || (status ? <><p className={status.cups.available ? 'good' : 'bad'}>CUPS: {status.cups.available ? 'kết nối' : 'không sẵn sàng'}</p><p>Đĩa còn {formatBytes(status.disk.free_bytes)} / {formatBytes(status.disk.total_bytes)}</p><p>{status.cups.message || ''}</p></> : 'Đang tải trạng thái…')}</div></article><article className="panel"><h2>Hàng đợi</h2><div id="job-counts" className="loading">{status ? `Chờ ${status.jobs.queued} · Giữ ${status.jobs.held} · CUPS ${status.jobs.submitted} · Chưa rõ ${status.jobs.unknown}` : 'Đang tải…'}</div></article></div></section>
        <section id="view-printers" className="panel tab-panel" role="tabpanel" aria-labelledby="tab-printers" tabIndex={0} hidden={tab !== 'printers'}><div className="section-heading"><div><p className="eyebrow">CUPS</p><h2>Máy in</h2></div><Button id="discover" className="secondary" onClick={event => {
          if (session.current.pending) return; open('add-printer', 'Thêm máy in', null, event.currentTarget); run(discover);
        }}>Thêm máy in</Button></div><p id="discovery-state" className="muted">{discoveryState}</p>
          <div id="printers" className="loading">{printers === null ? 'Đang tải danh sách máy in…' : !printers.length ? <p>Chưa có máy in. Chọn “Thêm máy in” để bắt đầu.</p> : printers.map(printer => <article key={printer.id} data-id={printer.id} className="item-card"><div className="item-heading"><h3>{printer.name}</h3><span className={printer.paused ? 'pill bad' : 'pill good'}>{printer.paused ? 'Đang tạm dừng' : 'Cho phép xử lý'}</span></div><p className="muted">{printer.formats.join(' / ').toUpperCase()} · {printer.managed ? 'Ứng dụng quản lý' : 'Đăng ký từ CUPS'}</p>{printer.pause_reason && <p className="bad">Lý do tạm dừng: {printer.pause_reason}</p>}
            <div className="button-row"><Button className="secondary" onClick={event => { const trigger = event.currentTarget; run(() => showEntity('printers', printer, trigger)); }}>Xem</Button><Button className="secondary" onClick={event => { if (!session.current.pending) open('edit-printer', `Sửa máy in · ${printer.name}`, printer, event.currentTarget); }}>Sửa cấu hình</Button>
              <Button onClick={() => run(async () => { const action = printer.paused ? 'resume' : 'pause'; await api(`/admin/api/printers/${encodeURIComponent(printer.id)}/${action}`, { method: 'POST', body: '{}' }); toasts.show(action === 'pause' ? 'Đã yêu cầu tạm dừng CUPS.' : 'Đã tiếp tục CUPS; hàng đợi có thể tiến FIFO.', 'success'); await Promise.all([loadPrinters(), loadStatus()]); })}>{printer.paused ? 'Tiếp tục máy in' : 'Tạm dừng máy in'}</Button>
              <Button className="danger" onClick={() => run(async () => { if (!confirm(`Gỡ “${printer.name}” khỏi ứng dụng?\nCấu hình CUPS và lịch sử vẫn được giữ. Không thể gỡ nếu còn lệnh chưa kết thúc.`)) return; await api(`/admin/api/printers/${encodeURIComponent(printer.id)}`, { method: 'DELETE' }); toasts.show('Đã gỡ đăng ký. Hàng đợi CUPS không thay đổi.', 'success'); await loadPrinters(); await loadClients(); })}>Gỡ đăng ký</Button></div>
          </article>)}</div>
        </section>
        <section id="view-clients" className="panel tab-panel" role="tabpanel" aria-labelledby="tab-clients" tabIndex={0} hidden={tab !== 'clients'}><div className="section-heading"><div><p className="eyebrow">QUYỀN API</p><h2>Clients</h2></div><Button id="add-client" onClick={event => { if (!session.current.pending) open('add-client', 'Tạo client', null, event.currentTarget); }}>Thêm client</Button></div>
          <div id="clients" className="loading">{clients === null ? 'Đang tải clients…' : !clients.length ? <p>Chưa có client API.</p> : clients.map(client => <article key={client.id} data-id={client.id} className="item-card"><div className="item-heading"><h3>{client.name}</h3><span className={client.revoked ? 'pill bad' : 'pill good'}>{client.revoked ? 'Đã thu hồi' : 'Hoạt động'}</span></div><p className="muted">{client.printer_ids.map(id => printers?.find(printer => printer.id === id)?.name || 'Máy không còn trong danh sách').join(' · ') || 'Chưa cấp máy nào'}</p>
            <div className="button-row"><Button className="secondary" onClick={event => { const trigger = event.currentTarget; run(() => showEntity('clients', client, trigger)); }}>Xem</Button><Button className="secondary" onClick={event => { if (!session.current.pending) open('edit-client', `Sửa client · ${client.name}`, client, event.currentTarget); }}>Sửa client</Button>
              <Button className="secondary" onClick={() => run(async () => { if (confirm(`Đổi API key của “${client.name}”? Key cũ ngừng hoạt động ngay.`)) await clientAction(client, 'rotate-key'); })}>Đổi API key</Button><Button className="danger" disabled={client.revoked} onClick={() => run(async () => { if (confirm(`Thu hồi key của “${client.name}”? Không hủy lệnh đã nhận.`)) await clientAction(client, 'revoke'); })}>Thu hồi key</Button>
              <Button className="danger" onClick={() => run(async () => { if (!confirm(`Xóa “${client.name}”? Key mất hiệu lực; lịch sử vẫn giữ. Không xóa được nếu còn lệnh chưa kết thúc.`)) return; await api(`/admin/api/clients/${encodeURIComponent(client.id)}`, { method: 'DELETE' }); toasts.show('Đã xóa client.', 'success'); await loadClients(); })}>Xóa client</Button></div>
          </article>)}</div>
        </section>
        <section id="view-jobs" className="panel tab-panel" role="tabpanel" aria-labelledby="tab-jobs" tabIndex={0} hidden={tab !== 'jobs'}><div className="section-heading"><div><p className="eyebrow">THEO DÕI</p><h2>Lệnh in</h2></div><Button id="refresh" className="secondary" onClick={() => run(refreshAll)}>Làm mới</Button></div><p className="muted">Hoàn thành là trạng thái hệ thống in báo, không phải xác nhận giấy đã ra. Lệnh chưa rõ kết quả không tự gửi lại.</p>
          <div id="jobs" className="loading">{jobsError || (jobs === null ? 'Đang tải lệnh in…' : <>{!jobs.length && <p>Chưa có lệnh in.</p>}<table><thead><tr>{['Lệnh', 'Client / máy', 'Trạng thái', 'Nhận lúc', 'Chi tiết'].map(value => <th key={value}>{value}</th>)}</tr></thead><tbody>{jobs.map(job => <tr key={job.job_id}><td>{job.title} · {job.format.toUpperCase()} × {job.copies}</td><td>{job.client_name} / {job.printer_name}</td><td className={`status-${job.status}`}>{job.status}</td><td>{job.accepted_at}</td><td><Button className="text-button" onClick={event => { const trigger = event.currentTarget; run(() => showJob(job, trigger)); }}>Xem</Button></td></tr>)}</tbody></table></>)}</div>
        </section>
        <section id="view-settings" className="panel tab-panel" role="tabpanel" aria-labelledby="tab-settings" tabIndex={0} hidden={tab !== 'settings'}><h2>Giới hạn và lưu trữ</h2><form id="settings-form" className="settings-grid" onSubmit={event => {
          event.preventDefault(); const payload = Object.fromEntries(settingFields.map(([key]) => [key, Number(settings[key])])); run(async () => { await api('/admin/api/settings', { method: 'PUT', body: JSON.stringify(payload) }); settingsDirty.current = false; toasts.show('Đã lưu giới hạn.', 'success'); await loadSettings(); });
        }}>{settingFields.map(([key, label, min, max, help]) => <div className="setting-field" key={key}><FieldLabel htmlFor={`setting-${key}`} explanation={help}>{label}</FieldLabel><Input id={`setting-${key}`} name={key} type="number" min={min} max={max} required value={settings[key] ?? ''} onChange={event => { settingsDirty.current = true; setSettings({ ...settings, [key]: event.target.value }); }} /></div>)}<Button type="submit">Lưu giới hạn</Button></form>
          {authenticated && tab === 'settings' && <ChangePassword run={run} save={async payload => {
            await api('/admin/api/password', { method: 'POST', body: JSON.stringify(payload) });
            reset(); activateTab('overview'); toasts.show('Đã đổi mật khẩu. Tất cả phiên quản trị đã đăng xuất. Hãy đăng nhập bằng mật khẩu mới.', 'success');
            requestAnimationFrame(() => document.getElementById('password')?.focus());
          }} />}
        </section>
        <section id="view-api-guide" className="panel tab-panel api-guide" role="tabpanel" aria-labelledby="tab-api-guide" tabIndex={0} hidden={tab !== 'api-guide'}>
          {authenticated && tab === 'api-guide' && <ApiGuide notice={toasts.show} />}
        </section>
      </div>
    </main><footer><p>Chỉ kết nối CUPS cục bộ. Không đưa dịch vụ ra Internet; dùng TLS khi truy cập qua LAN.</p></footer>
    {modal && <Modal key={modal.key} title={modal.title} trigger={modal.trigger} busy={busy} onClose={close} readOnly={modal.kind === 'key' || modal.kind === 'job' || modal.kind.startsWith('details-')} toasts={notices}>{modalContent}</Modal>}
  </BusyContext.Provider>;
}
createRoot(document.getElementById('root')).render(<AdminApp />);
