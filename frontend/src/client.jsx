import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BusyContext, Button, Input, Notice, Select, useToasts } from './shared.jsx';
import { PrintOptions, initialOptions, optionsError, optionsPayload } from './client-print-options.jsx';
import { ApiGuide } from './api-guide.jsx';

const KEY_STORAGE = 'print-appliance.client.api-key';
const labels = { queued: 'Đang chờ', held: 'Đang giữ', submitting: 'Đang giao', submitted: 'Đã giao CUPS', completed: 'Hoàn thành (CUPS)', failed: 'Thất bại', canceled: 'Đã hủy', unknown: 'Chưa rõ kết quả' };
const active = new Set(['queued', 'held', 'submitting', 'submitted']);
function forgetKey() { try { sessionStorage.removeItem(KEY_STORAGE); } catch {} }
function rememberedKey() {
  try { const key = sessionStorage.getItem(KEY_STORAGE); if (key && key.length <= 256 && !/[\s\x00-\x1f\x7f]/.test(key)) return key; forgetKey(); } catch {}
  return '';
}
function rememberKey(key) { try { sessionStorage.setItem(KEY_STORAGE, key); return true; } catch { return false; } }
function requestId() {
  // getRandomValues, unlike randomUUID, also works on the approved LAN HTTP listener.
  return 'web-' + [...crypto.getRandomValues(new Uint8Array(24))].map(value => value.toString(16).padStart(2, '0')).join('');
}

function ClientApp() {
  const toasts = useToasts(), fileInput = useRef(null), keyInput = useRef(null);
  const session = useRef({ key: '', epoch: 0, generation: 0, schemaEpoch: 0, pending: null, busy: false, reading: null, timer: null, controllers: new Set(), alive: true });
  const [view, setView] = useState(() => location.hash === '#api-guide' ? 'api-guide' : 'print');
  const [busy, setBusy] = useState(false), [connected, setConnected] = useState(false), [candidate, setCandidate] = useState('');
  const changeView = value => { setView(value); history.replaceState(null, '', value === 'api-guide' ? '#api-guide' : '#print'); };
  useEffect(() => {
    const syncView = () => setView(location.hash === '#api-guide' ? 'api-guide' : 'print');
    addEventListener('hashchange', syncView);
    return () => removeEventListener('hashchange', syncView);
  }, []);
  const [printers, setPrinters] = useState([]), [printerId, setPrinterId] = useState(''), [format, setFormat] = useState('pdf');
  const [title, setTitle] = useState('In thử'), [copies, setCopies] = useState('1'), [pending, setPending] = useState(null);
  const [schema, setSchema] = useState(null), [options, setOptions] = useState(initialOptions({}, {}, true));
  const [optionState, setOptionState] = useState({ text: '', error: false }), [jobs, setJobs] = useState([]), [historyState, setHistoryState] = useState('');
  const selection = useRef({ printers: [], printerId: '', format: 'pdf', schema: null });
  const stopPolling = () => { clearTimeout(session.current.timer); session.current.timer = null; };
  const reset = (clearSaved = true) => {
    if (clearSaved) forgetKey();
    const s = session.current; s.epoch++; s.schemaEpoch++; s.key = ''; s.pending = null; s.reading = null; stopPolling();
    selection.current = { printers: [], printerId: '', format: 'pdf', schema: null };
    setConnected(false); setCandidate(''); setPrinters([]); setPrinterId(''); setFormat('pdf'); setTitle('In thử'); setCopies('1'); setPending(null); setSchema(null); setOptions(initialOptions({}, {}, true)); setOptionState({ text: '', error: false }); setJobs([]); setHistoryState(''); toasts.clear();
    if (fileInput.current) fileInput.current.value = '';
  };
  const action = async fn => {
    const s = session.current;
    if (s.busy) return;
    const generation = ++s.generation;
    s.busy = true; setBusy(true); stopPolling();
    try { await fn(); } catch (error) { if (generation === s.generation && s.alive) toasts.show(error.message, 'error'); }
    finally { if (generation === s.generation && s.alive) { s.busy = false; setBusy(false); } }
  };
  const request = async (path, config = {}, key = session.current.key) => {
    const s = session.current, epoch = s.epoch, controller = new AbortController();
    s.controllers.add(controller); const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(path, { ...config, headers: { Authorization: `Bearer ${key}`, ...config.headers }, credentials: 'omit', signal: controller.signal });
      const data = await response.json().catch(() => null);
      if (epoch !== s.epoch || !s.alive) throw new Error('Kết nối đã thay đổi.');
      if (!response.ok) {
        const message = response.status === 401 ? 'API key không hợp lệ hoặc đã bị thu hồi.' : typeof data?.detail === 'string' ? data.detail : data?.detail?.message ? `${data.detail.field}: ${data.detail.message}` : `API trả lỗi ${response.status}`;
        if (response.status === 401 && key === s.key) { reset(); toasts.show(message, 'error'); }
        const error = new Error(message); error.status = response.status; throw error;
      }
      if (data === null) throw new Error('Server trả dữ liệu không hợp lệ.');
      return data;
    } catch (error) { if (error.name === 'AbortError') throw new Error('Hết thời gian chờ phản hồi.'); throw error; }
    finally { clearTimeout(timeout); s.controllers.delete(controller); }
  };
  const loadSchema = async (id, nextFormat = selection.current.format, key = session.current.key) => {
    const s = session.current, loadId = ++s.schemaEpoch, epoch = s.epoch;
    selection.current.printerId = id; selection.current.format = nextFormat; selection.current.schema = null;
    setSchema(null); setOptions(initialOptions({}, {}, true)); setOptionState({ text: id ? 'Đang đọc tùy chọn từ danh sách máy…' : '', error: false });
    if (!id) return;
    try {
      const value = selection.current.printers.find(item => item.id === id)?.capabilities;
      if (epoch !== s.epoch || loadId !== s.schemaEpoch || selection.current.printerId !== id) return;
      if (!value) throw new Error('Thiếu thông tin tùy chọn trong danh sách máy.');
      selection.current.schema = value; setSchema(value);
      setOptionState({ text: selection.current.format === 'zpl' ? 'ZPL gửi nguyên bản; tùy chọn driver PDF không áp dụng.' : 'Chọn tùy chọn phổ thông hoặc dùng mặc định của máy.', error: false });
    } catch (error) { if (epoch === s.epoch && loadId === s.schemaEpoch) { selection.current.schema = null; setSchema(null); setOptionState({ text: `Không thể tải tùy chọn máy in: ${error.message}`, error: true }); } }
  };
  const loadPrinters = async (key = session.current.key) => {
    const items = await request('/api/v1/printers', {}, key);
    if (!Array.isArray(items)) throw new Error('Danh sách máy in không hợp lệ.');
    const old = selection.current, printer = items.find(item => item.id === old.printerId) || items[0];
    const nextFormat = printer?.formats.includes(old.format) ? old.format : printer?.formats[0] || 'pdf';
    selection.current.printers = items; setPrinters(items); setPrinterId(printer?.id || ''); setFormat(nextFormat);
    await loadSchema(printer?.id || '', nextFormat, key);
  };
  const loadJobs = async () => {
    const s = session.current;
    if (s.reading || !s.key) return;
    const token = {}, epoch = s.epoch; s.reading = token;
    try {
      const items = await request('/api/v1/jobs?limit=50');
      if (!Array.isArray(items)) throw new Error('Danh sách lệnh không hợp lệ.');
      setJobs(items); setHistoryState(items.length ? `${items.length} lệnh gần nhất của client` : 'Chưa có lệnh in.');
      if (items.some(job => active.has(job.status))) { stopPolling(); s.timer = setTimeout(() => { if (!s.busy && epoch === s.epoch) loadJobs(); }, 4000); }
    } catch (error) { if (epoch === s.epoch) setHistoryState(`Không tải được lịch sử: ${error.message} Nhấn Làm mới để kiểm tra.`); }
    finally { if (s.reading === token) s.reading = null; }
  };
  const connect = async (key, restoring = false) => {
    if (!key) throw new Error('Nhập API key.');
    if (!restoring) forgetKey();
    const s = session.current; s.key = key; const epoch = ++s.epoch;
    try { await loadPrinters(key); }
    catch (error) {
      if (s.epoch === epoch) { reset(!restoring || error.status === 401); if (restoring && error.status !== 401) setCandidate(key); }
      throw error;
    }
    if (epoch !== s.epoch || s.key !== key) return;
    const saved = rememberKey(key); setCandidate(''); setConnected(true);
    toasts.show(saved ? 'Đã kết nối. Tải lại trang vẫn giữ client; không tự gửi lệnh in.' : 'Đã kết nối, nhưng trình duyệt chặn lưu theo tab; tải lại cần nhập key.', saved ? 'success' : 'error');
    await loadJobs();
  };
  const send = async () => {
    const s = session.current, captured = s.pending, epoch = s.epoch;
    if (!captured) return;
    const body = new FormData();
    for (const key of ['printer_id', 'format', 'title', 'copies']) body.append(key, captured[key]);
    if (Object.keys(captured.options).length) body.append('options', JSON.stringify(captured.options));
    body.append('file', captured.file, captured.file.name);
    try {
      const result = await request('/api/v1/jobs', { method: 'POST', headers: { 'Idempotency-Key': captured.id }, body });
      if (typeof result.job_id !== 'string') throw new Error('Phản hồi thiếu mã lệnh; cần xác minh lại.');
      s.pending = null; setPending(null); if (fileInput.current) fileInput.current.value = '';
      toasts.show(`${result.deduplicated ? 'Server đã nhận trước đó; không tạo lệnh trùng.' : 'Đã lưu lệnh.'} Mã lệnh: ${result.job_id}. Trạng thái: ${labels[result.status] || result.status}.`, 'success');
      await loadJobs();
    } catch (error) {
      if (epoch !== s.epoch) return;
      // A lost/5xx response may hide durable acceptance. Only the explicit retry can resend.
      if (error.status >= 400 && error.status < 500 && error.status !== 408) { s.pending = null; setPending(null); }
      toasts.show(`${error.message}${s.pending ? ' Chưa xác nhận kết quả. Chỉ gửi lại cùng yêu cầu bên dưới.' : ''}`, 'error');
    }
  };
  const submit = event => {
    event.preventDefault();
    action(async () => {
      if (session.current.pending) return;
      const file = fileInput.current.files[0]; if (!file) throw new Error('Hãy chọn file PDF hoặc ZPL.');
      const schema = selection.current.schema || {};
      const config = { defaults: schema.default_options || {}, client: true, format: selection.current.format };
      const invalid = optionsError(schema, options, config); if (invalid) throw new Error(invalid);
      // Snapshot before disabling controls; retries never reread editable fields or files.
      const captured = Object.freeze({ id: requestId(), file, printer_id: selection.current.printerId, format: selection.current.format, title: title.trim(), copies, options: Object.freeze(optionsPayload(schema, options, config).options) });
      session.current.pending = captured; setPending(captured); await send();
    });
  };
  const changeFormat = value => {
    setFormat(value); selection.current.format = value; setOptions(initialOptions({}, {}, true));
    if (selection.current.schema) setOptionState({ text: value === 'zpl' ? 'ZPL gửi nguyên bản; tùy chọn driver PDF không áp dụng.' : 'Chọn “Dùng mặc định” hoặc một giá trị máy hỗ trợ.', error: false });
  };
  useEffect(() => {
    const s = session.current;
    const restore = () => { const key = rememberedKey(); if (key) action(() => connect(key, true)); };
    const beforeUnload = event => { if (s.pending) { event.preventDefault(); event.returnValue = ''; } };
    const pageHide = () => { stopPolling(); s.key = ''; s.epoch++; s.schemaEpoch++; s.generation++; for (const controller of s.controllers) controller.abort(); };
    const pageShow = event => { if (event.persisted) { s.busy = false; setBusy(false); reset(false); restore(); } };
    addEventListener('beforeunload', beforeUnload); addEventListener('pagehide', pageHide); addEventListener('pageshow', pageShow); restore();
    return () => { s.alive = false; pageHide(); removeEventListener('beforeunload', beforeUnload); removeEventListener('pagehide', pageHide); removeEventListener('pageshow', pageShow); };
  }, []);
  const selectedPrinter = printers.find(item => item.id === printerId);
  return <BusyContext.Provider value={busy}>
    <header className="topbar"><div><p className="eyebrow">CLIENT API</p><h1>{view === 'api-guide' ? 'Tích hợp API' : 'Gửi lệnh in'}</h1></div><a className="client-link" href="/">Quản trị</a></header>
    <main className={`client-main${view === 'api-guide' ? ' client-guide-main' : ''}`}><Notice id="client-notice" {...toasts} />
      <nav className="tab-list" role="tablist" aria-label="Các chức năng client">{[['print', 'In tài liệu'], ['api-guide', 'API Guide']].map(([id, label], index) => <Button alwaysEnabled key={id} id={`client-tab-${id}`} role="tab" aria-selected={view === id} aria-controls={`client-panel-${id}`} tabIndex={view === id ? 0 : -1} onClick={() => changeView(id)} onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault(); const target = event.key === 'Home' ? 'print' : event.key === 'End' ? 'api-guide' : index === 0 ? 'api-guide' : 'print';
        changeView(target); document.getElementById(`client-tab-${target}`).focus();
      }}>{label}</Button>)}</nav>
      {view === 'api-guide' && pending && <p className="api-warning" role="status">Lệnh {pending.id} chưa xác nhận kết quả. File và ID vẫn được giữ trong tab này. <Button alwaysEnabled className="text-button" onClick={() => changeView('print')}>Quay lại kiểm tra lệnh in</Button></p>}
      <div id="client-panel-print" className="tab-panel" role="tabpanel" aria-labelledby="client-tab-print" hidden={view !== 'print'}>
      <section id="key-panel" className="panel narrow" hidden={connected}><h2>Kết nối client</h2>
        <form id="key-form" autoComplete="off" onSubmit={event => { event.preventDefault(); action(() => connect(candidate.trim())); }}>
          <label htmlFor="api-key">API key</label><Input ref={keyInput} id="api-key" type="password" required autoComplete="off" maxLength={256} spellCheck={false} value={candidate} onChange={event => setCandidate(event.target.value)} />
          <p className="muted">Dùng key của máy in trong mục Máy in trên trang quản trị, không phải mật khẩu quản trị. Key lưu theo tab bằng sessionStorage; tải lại tự kết nối. Ngắt kết nối sẽ xóa key. Không dùng trên trình duyệt dùng chung.</p>
          <Button type="submit">{busy ? 'Đang kết nối…' : 'Kết nối'}</Button>
        </form>
      </section>
      <div id="client-workspace" hidden={!connected}>
        <div className="section-heading"><p className="good">Đã kết nối bằng API key</p><Button id="disconnect" className="secondary" onClick={() => {
          if (session.current.busy) return;
          if (pending && !confirm(`Lệnh ${pending.id} có thể đã được nhận. Ngắt kết nối sẽ mất file/mã đang giữ; không gửi lại bằng mã mới khi chưa kiểm tra lịch sử. Vẫn ngắt?`)) return;
          reset(); keyInput.current.focus();
        }}>Ngắt kết nối</Button></div>
        <section className="panel"><h2>Gửi file</h2><p className="bad">Gửi lệnh sẽ in thật nếu máy được phép xử lý. Kiểm tra máy và số bản trước khi gửi.</p>
          <p className="muted">Tải lại chỉ giữ kết nối client, không khôi phục file hay yêu cầu đang gửi. Nếu mất phản hồi, kiểm tra lịch sử trước khi tạo lệnh mới.</p>
          <p id="no-printers" className="muted" hidden={printers.length > 0}>Key này không có máy in khả dụng. Nhờ quản trị kiểm tra mục Máy in.</p>
          <BusyContext.Provider value={busy || !!pending || !printers.length}>
            <form id="print-form" onSubmit={submit}>
              <label htmlFor="client-printer">Máy in</label><Select id="client-printer" required value={printerId} onChange={event => {
                const id = event.target.value, printer = printers.find(item => item.id === id), nextFormat = printer.formats.includes(format) ? format : printer.formats[0];
                setPrinterId(id); setFormat(nextFormat); loadSchema(id, nextFormat);
              }}>{printers.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</Select>
              <p id="printer-state" className="muted" role="status">{selectedPrinter ? selectedPrinter.status === 'paused' ? 'Máy đang tạm dừng. Lệnh sẽ được giữ đến khi quản trị cho tiếp tục.' : 'Được phép xử lý; trạng thái này không xác nhận máy đã in.' : ''}</p>
              <label htmlFor="client-file">File</label><Input ref={fileInput} id="client-file" type="file" accept=".pdf,.zpl,application/pdf" required onChange={event => {
                const file = event.target.files[0]; if (!file) return; const value = file.name.toLowerCase().endsWith('.zpl') ? 'zpl' : 'pdf';
                if (selectedPrinter?.formats.includes(value) && value !== format) changeFormat(value);
              }} />
              <div className="settings-grid"><div><label htmlFor="client-format">Định dạng</label><Select id="client-format" required value={format} onChange={event => changeFormat(event.target.value)}>{(selectedPrinter?.formats || ['pdf', 'zpl']).map(value => <option key={value} value={value}>{value.toUpperCase()}</option>)}</Select></div>
                <div><label htmlFor="client-copies">Số bản</label><Input id="client-copies" type="number" min="1" max="100" required value={copies} onChange={event => setCopies(event.target.value)} /></div></div>
              <label htmlFor="client-title">Tiêu đề</label><Input id="client-title" maxLength={200} required value={title} onChange={event => setTitle(event.target.value)} />
              <section id="client-options-panel" aria-live="polite"><p id="options-state" className={optionState.error ? 'bad' : 'muted'}>{optionState.text || 'Chọn máy in để xem tùy chọn.'}</p><div id="client-options">{schema && <PrintOptions schema={schema} model={options} onChange={setOptions} defaults={schema.default_options || {}} client format={format} />}</div></section>
              <Button id="send-print" type="submit">{busy && pending ? 'Đang gửi…' : 'Gửi lệnh in'}</Button>
            </form>
          </BusyContext.Provider>
          <div id="uncertain-request" hidden={!pending}><p className="bad">Chưa xác nhận server đã nhận lệnh hay chưa. Không tạo lệnh mới để gửi lại.</p><p>Mã yêu cầu: <code id="request-id">{pending?.id || ''}</code></p>
            <Button id="retry-print" onClick={() => action(send)}>{busy ? 'Đang gửi…' : 'Gửi lại cùng yêu cầu'}</Button><p className="muted">Giữ nguyên file, máy in và mã để server chống trùng. Không đóng tab khi còn chưa xác nhận.</p></div>
        </section>
        <section className="panel"><div className="section-heading"><h2>Lệnh của client</h2><Button id="refresh-client" className="secondary" onClick={() => action(async () => { if (!session.current.pending) await loadPrinters(); await loadJobs(); })}>Làm mới</Button></div>
          <p id="history-state" className="muted" role="status">{historyState}</p><div id="client-jobs">{jobs.map(job => <article className="item-card" key={job.job_id}><div className="item-heading"><h3>{job.title}</h3><span className={`status-${job.status}`}>{labels[job.status] || job.status}</span></div><p className="muted">{job.format?.toUpperCase()} · {job.copies} bản · {job.accepted_at}</p><p>Mã lệnh: {job.job_id}</p>{job.reason && <p className={job.status === 'unknown' ? 'bad' : 'muted'}>{job.reason}</p>}</article>)}</div>
          <p className="muted">Tự cập nhật mỗi 4 giây khi có lệnh đang xử lý. “Hoàn thành” là CUPS báo hoàn thành, không bảo đảm giấy đã ra. “Chưa rõ kết quả” cần quản trị kiểm tra; không tự gửi lại.</p>
        </section>
      </div>
      </div>
      <section id="client-panel-api-guide" className="tab-panel api-guide" role="tabpanel" aria-labelledby="client-tab-api-guide" hidden={view !== 'api-guide'}>
        <BusyContext.Provider value={false}>{view === 'api-guide' && <ApiGuide notice={toasts.show} />}</BusyContext.Provider>
      </section>
    </main><footer>Chỉ dùng trên LAN đáng tin cậy. HTTP không mã hóa API key. Trang này không cài driver và không cấp quyền quản trị.</footer>
  </BusyContext.Provider>;
}
createRoot(document.getElementById('root')).render(<ClientApp />);
