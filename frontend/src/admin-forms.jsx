import React, { useContext, useEffect, useRef, useState } from 'react';
import { Button, CloseContext, FieldLabel, HelpButton, Input, Select, Textarea, fieldHelp } from './shared.jsx';
import { DriverPicker, GrantPicker } from './controls.jsx';
import { PrintOptions, initialOptions, optionsError, optionsPayload, usableSchema } from './print-options.jsx';

export function Formats({ value, onChange, name = 'format' }) {
  return <fieldset><legend>Định dạng nhận<HelpButton title="Định dạng nhận" explanation={fieldHelp.formats} /></legend>{['pdf', 'zpl'].map(format => <label className="check" key={format}><Input type="checkbox" name={name} value={format} checked={value.includes(format)} onChange={event => onChange(event.target.checked ? [...value, format] : value.filter(item => item !== format))} /> {format.toUpperCase()}</label>)}</fieldset>;
}
export function AddPrinter({ discovery, drivers, printers, run, save }) {
  const onClose = useContext(CloseContext);
  const [importing, setImporting] = useState(false), [name, setName] = useState(''), [uri, setUri] = useState(''), [manual, setManual] = useState(''), [driver, setDriver] = useState('');
  const [formats, setFormats] = useState(['pdf']), [importFormats, setImportFormats] = useState(['pdf']), [queue, setQueue] = useState(''), [importName, setImportName] = useState('');
  const registered = new Set(printers.map(printer => printer.queue));
  return <div id="discovery" className="discovery"><div className="button-row add-mode" role="group" aria-label="Cách thêm máy in">
    <Button id="new-printer-mode" aria-pressed={!importing} onClick={() => setImporting(false)}>Cấu hình máy mới</Button><Button id="import-printer-mode" className="secondary" aria-pressed={importing} onClick={() => setImporting(true)}>Dùng cấu hình có sẵn</Button><Button id="close-add" className="quiet" onClick={onClose}>Đóng</Button>
  </div>
    <form id="managed-printer-form" className="subpanel" hidden={importing} onSubmit={event => {
      event.preventDefault(); const payload = { name, device_uri: manual.trim() || uri, driver, formats, default_options: {}, allowed_options: {} };
      run(async () => { if (!driver) throw new Error('Hãy chọn driver trong danh sách.'); if (!payload.device_uri) throw new Error('Hãy chọn thiết bị hoặc nhập địa chỉ mạng.'); await save(payload, false); });
    }}><h3>Cấu hình máy mới</h3>
      <FieldLabel htmlFor="printer-name" explanation={fieldHelp.name}>Tên hiển thị</FieldLabel><Input id="printer-name" required maxLength={120} value={name} onChange={event => setName(event.target.value)} />
      <FieldLabel htmlFor="device-uri" explanation={fieldHelp.uri}>Thiết bị</FieldLabel><Select id="device-uri" value={uri} onChange={event => setUri(event.target.value)}><option value="">Chọn thiết bị…</option>{(discovery?.devices || []).map(item => <option key={item.uri} value={item.uri}>{item.info || item.make_model || item.uri} · {item.uri}</option>)}</Select>
      <FieldLabel htmlFor="driver-search" explanation={fieldHelp.driver}>Driver</FieldLabel><DriverPicker entries={drivers} onChange={setDriver} />
      <Formats value={formats} onChange={setFormats} />
      <details className="advanced"><summary>Nâng cao</summary><FieldLabel htmlFor="manual-uri" explanation={fieldHelp.uri}>Địa chỉ mạng thủ công</FieldLabel><Input id="manual-uri" placeholder="ipp://192.168.1.20:631/ipp/print" value={manual} onChange={event => setManual(event.target.value)} />
        <p className="muted">Có thể đăng ký trước với tùy chọn trống. Sau khi tạo, mở Sửa cấu hình để đọc schema của hàng đợi và chọn giá trị mặc định/quyền client.</p></details>
      <Button type="submit">Tạo máy in</Button>
    </form>
    <form id="import-form" className="subpanel" hidden={!importing} onSubmit={event => { event.preventDefault(); const payload = { queue, name: importName, formats: importFormats }; run(() => save(payload, true)); }}><h3>Dùng cấu hình có sẵn</h3>
      <FieldLabel htmlFor="existing-queue" explanation={fieldHelp.queue}>Hàng đợi hệ thống</FieldLabel><Select id="existing-queue" required value={queue} onChange={event => setQueue(event.target.value)}><option value="">Chọn hàng đợi…</option>{Object.keys(discovery?.queues || {}).filter(item => !registered.has(item)).map(item => <option key={item} value={item}>{item}</option>)}</Select>
      <FieldLabel htmlFor="import-name" explanation={fieldHelp.name}>Tên hiển thị</FieldLabel><Input id="import-name" required maxLength={120} value={importName} onChange={event => setImportName(event.target.value)} />
      <Formats value={importFormats} onChange={setImportFormats} name="import-format" /><p>Máy bắt đầu tạm dừng. Sau khi đăng ký, nút tạm dừng/tiếp tục sẽ điều khiển hàng đợi CUPS này.</p><Button type="submit">Đăng ký máy in</Button>
    </form>
  </div>;
}

export function EditPrinter({ printer, drivers, api, run, discover, save }) {
  const [name, setName] = useState(printer.name), [formats, setFormats] = useState([...printer.formats]);
  const [uri, setUri] = useState(printer.device_uri), [driver, setDriver] = useState(printer.driver);
  const [schema, setSchema] = useState(null), [state, setState] = useState({ text: 'Đang đọc tùy chọn của máy in…', error: false });
  const [options, setOptions] = useState(initialOptions(printer.default_options, printer.allowed_options));
  const touched = useRef(false), requestId = useRef(0);
  const loadSchema = async () => {
    const id = ++requestId.current; setSchema(null); setState({ text: 'Đang đọc capability của hàng đợi…', error: false });
    try {
      const value = await api(`/admin/api/printers/${encodeURIComponent(printer.id)}/capabilities`);
      if (id !== requestId.current) return;
      if (!usableSchema(value)) throw new Error(value.reason || `Schema hiện không dùng được (${value.availability}).`);
      setSchema(value); setOptions(initialOptions(printer.default_options, printer.allowed_options)); touched.current = false;
      setState({ text: value.reason || 'Tùy chọn do CUPS và driver hiện tại báo.', error: false });
    } catch (error) { if (id === requestId.current && !error.stale) setState({ text: `Không tải/cấu hình được schema: ${error.message}`, error: true }); }
  };
  useEffect(() => { loadSchema(); return () => { requestId.current++; }; }, []);
  return <form className="edit-form" onSubmit={event => {
    event.preventDefault();
    const payload = { name, formats };
    run(async () => {
      if (printer.managed && !driver) throw new Error('Hãy chọn driver trong danh sách.');
      const changed = printer.managed && (uri !== printer.device_uri || driver !== printer.driver);
      if (changed) {
        if ((Object.keys(printer.default_options || {}).length || Object.keys(printer.allowed_options || {}).length) && !confirm('Đổi địa chỉ/driver sẽ xóa tùy chọn và quyền client cũ để tránh áp dụng lựa chọn không tương thích. Tiếp tục?')) return;
        payload.default_options = {}; payload.allowed_options = {};
      } else if (touched.current) {
        if (!schema) throw new Error('Hãy tải schema capability trước khi sửa tùy chọn.');
        const config = { defaults: printer.default_options || {}, allowed: printer.allowed_options || {} };
        const invalid = optionsError(schema, options, config); if (invalid) throw new Error(invalid);
        Object.assign(payload, optionsPayload(schema, options, config));
      }
      if (printer.managed && uri !== printer.device_uri) payload.device_uri = uri;
      if (printer.managed && driver !== printer.driver) payload.driver = driver;
      await save(payload);
    });
  }}>
    <FieldLabel htmlFor={`edit-name-${printer.id}`} explanation={fieldHelp.name}>Tên hiển thị</FieldLabel><Input id={`edit-name-${printer.id}`} required maxLength={120} value={name} onChange={event => setName(event.target.value)} />
    <Formats value={formats} onChange={setFormats} />
    {printer.managed && <><FieldLabel htmlFor={`edit-uri-${printer.id}`} explanation={fieldHelp.uri}>Địa chỉ thiết bị</FieldLabel><Input id={`edit-uri-${printer.id}`} required value={uri} onChange={event => setUri(event.target.value)} />
      <FieldLabel htmlFor={`driver-${printer.id}-search`} explanation={fieldHelp.driver}>Driver</FieldLabel><DriverPicker id={`driver-${printer.id}`} entries={drivers} current={printer.driver} onChange={setDriver} />
      <Button className="text-button" onClick={() => run(discover)}>Cập nhật danh sách driver</Button></>}
    <section className="printer-schema"><div className="section-heading"><h3>Tùy chọn driver và quyền client</h3><Button className="secondary" onClick={() => run(async () => { if (touched.current && !confirm('Tải lại sẽ bỏ các tùy chọn chưa lưu. Tiếp tục?')) return; await loadSchema(); })}>Tải lại schema</Button></div>
      <p className={state.error ? 'bad' : 'muted'}>{state.text}</p>
      {schema && <PrintOptions schema={schema} model={options} defaults={printer.default_options || {}} allowed={printer.allowed_options || {}} onChange={value => { touched.current = true; setOptions(value); }} />}
    </section><Button type="submit">Lưu cấu hình</Button>
  </form>;
}
export function ClientForm({ client, printers, run, save }) {
  const [name, setName] = useState(client?.name || ''), [grants, setGrants] = useState(() => [...(client?.printer_ids || [])]);
  const id = client ? `client-grants-${client.id}` : 'client-printers';
  return <form id={client ? undefined : 'client-form'} className="edit-form" onSubmit={event => {
    event.preventDefault(); const payload = { name };
    // Omitting unchanged grants avoids overwriting an operator's concurrent revocation.
    if (!client || JSON.stringify([...grants].sort()) !== JSON.stringify([...client.printer_ids].sort())) payload.printer_ids = [...grants];
    run(() => save(payload));
  }}><div><FieldLabel htmlFor={client ? `client-name-${client.id}` : 'client-name'} explanation={fieldHelp.client}>Tên client</FieldLabel><Input id={client ? `client-name-${client.id}` : 'client-name'} required maxLength={120} value={name} onChange={event => setName(event.target.value)} /></div>
    <div><FieldLabel htmlFor={`${id}-toggle`} explanation={fieldHelp.grants}>Máy được cấp</FieldLabel><GrantPicker id={id} printers={printers} value={grants} onChange={setGrants} /></div><Button type="submit">{client ? 'Lưu client' : 'Tạo client + key'}</Button>
  </form>;
}
export function ApiKey({ value, notice }) {
  const input = useRef(null);
  return <section><p>Sao chép và lưu an toàn. Key chỉ xuất hiện lần này; key cũ (nếu có) đã mất hiệu lực.</p><Textarea ref={input} value={value} readOnly rows={3} aria-label="API key mới" />
    <Button onClick={async () => {
      try { if (!navigator.clipboard) throw new Error('Trình duyệt HTTP không hỗ trợ sao chép tự động. Chọn nội dung rồi sao chép thủ công.'); await navigator.clipboard.writeText(value); notice('Đã sao chép API key.', 'success'); }
      catch (error) { input.current?.focus(); input.current?.select(); notice(error.message, 'info'); }
    }}>Sao chép API key</Button>
  </section>;
}
export function EntityDetails({ kind, record, printers }) {
  const fields = kind === 'printers' ? { Tên: record.name, ID: record.id, 'Hàng đợi CUPS': record.queue, Driver: record.driver, 'Địa chỉ': record.device_uri, 'Định dạng': record.formats?.join(', ') } : { Tên: record.name, ID: record.id, 'Trạng thái': record.revoked ? 'Đã thu hồi' : 'Hoạt động', 'Máy được cấp': record.printer_ids?.map(id => printers.find(item => item.id === id)?.name || id).join(', ') || 'Chưa cấp máy nào' };
  return <section><dl className="detail-list">{Object.entries(fields).map(([key, value]) => <React.Fragment key={key}><dt>{key}</dt><dd>{value || '—'}</dd></React.Fragment>)}</dl></section>;
}
export function JobDetails({ job, run, control, resolve }) {
  const [outcome, setOutcome] = useState('completed'), [reason, setReason] = useState('');
  return <aside id="job-details" className="subpanel" aria-live="polite"><h3>Chi tiết {job.job_id}</h3><p>{job.status}: {job.reason || 'Không có lỗi được ghi nhận'}</p>
    <h4>Cấu hình khi nhận</h4><pre>{JSON.stringify(job.printer_snapshot, null, 2)}</pre>{job.cups_job_id && <p>CUPS job ID: {job.cups_job_id}</p>}<h4>Sự kiện</h4><pre>{JSON.stringify(job.events, null, 2)}</pre>
    {job.status === 'unknown' && <form className="edit-form" onSubmit={event => { event.preventDefault(); const payload = { outcome, reason }; run(() => resolve(payload)); }}>
      <FieldLabel htmlFor="resolve-outcome" explanation="Chọn kết quả đã xác minh của lệnh chưa rõ kết quả. Thao tác chỉ ghi nhận, không gửi lại để in.">Kết quả xác minh</FieldLabel><Select id="resolve-outcome" value={outcome} onChange={event => setOutcome(event.target.value)}>{['completed', 'failed', 'canceled'].map(value => <option key={value} value={value}>{value}</option>)}</Select>
      <FieldLabel htmlFor="resolve-reason" explanation="Ghi bằng chứng kiểm tra thực tế hoặc phản hồi hệ thống in (8–1000 ký tự). Không suy đoán thất bại để gửi lại vì có thể gây in trùng.">Bằng chứng</FieldLabel><Textarea id="resolve-reason" rows={3} required minLength={8} maxLength={1000} placeholder="Ghi bằng chứng / lý do xử lý (tối thiểu 8 ký tự)" value={reason} onChange={event => setReason(event.target.value)} />
      <Button type="submit">Ghi nhận xác minh (không gửi lại)</Button>
    </form>}
    <div className="button-row">{['queued', 'held', 'submitted', 'unknown'].includes(job.status) && <Button className="danger" onClick={() => run(async () => { if (!confirm('Hủy lệnh này? Phần đã in không thể thu hồi.')) return; await control('cancel'); })}>Hủy lệnh</Button>}
      {['queued', 'held', 'submitted'].includes(job.status) && <Button className="secondary" onClick={() => run(() => control('resume'))}>Tiếp tục riêng lệnh này</Button>}</div>
  </aside>;
}
