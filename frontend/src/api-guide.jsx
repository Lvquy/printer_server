import React, { useId, useRef, useState } from 'react';
import { Button, Input, Select, normalizeSearch } from './shared.jsx';
import contract from './client-api.json';

const quote = value => `'${value.replace(/'/g, `'\\''`)}'`;
const requestIdExample = 'export REQUEST_ID="client-$(python3 -c \'import secrets; print(secrets.token_hex(24))\')"';
const tasks = [
  { id: 'printers', title: 'Chọn máy in', hint: 'Lấy PRINTER_ID', summary: 'Danh sách máy được cấp, kèm capabilities.supported và defaults.', result: 'Dùng id làm PRINTER_ID. Gửi giá trị trong supported bằng field cùng tên; bỏ qua thì dùng mặc định. Không cần gọi API tùy chọn riêng.' },
  { id: 'submit', title: 'Gửi file', hint: 'PDF hoặc ZPL', summary: 'Gửi file và lưu một lệnh in mới.', result: 'Lưu job_id để theo dõi ở bước tiếp theo.' },
  { id: 'job', title: 'Theo dõi lệnh', hint: 'Trạng thái và sự kiện', summary: 'Xem trạng thái hiện tại và các sự kiện của một lệnh.', result: 'Kiểm tra mỗi 3–5 giây khi lệnh còn hoạt động; ngừng khi đã kết thúc.' },
  { id: 'jobs', title: 'Xem lịch sử', hint: 'Lệnh của client', summary: 'Danh sách lệnh của bạn, mới nhất trước.', result: 'Chỉ có lệnh của API key hiện tại; response không chứa file đã gửi.' },
  { id: 'cancel', title: 'Hủy lệnh', hint: 'Kiểm tra đúng JOB_ID', summary: 'Yêu cầu hủy một lệnh của bạn; không xóa lịch sử.', result: 'HTTP 200 không luôn có status = canceled. Kiểm tra status, reason và events.' },
];
export function curlExample(endpoint, format = 'pdf') {
  const route = endpoint.path.replace('{printer_id}', '${PRINTER_ID}').replace('{job_id}', '${JOB_ID}') + (endpoint.id === 'jobs' ? '?limit=50' : '');
  const lines = ['curl --include --silent --show-error --max-time 30', `  -X ${endpoint.method} "$BASE_URL${route}"`, '  -H "Authorization: Bearer ${API_KEY}"'];
  if (endpoint.id === 'submit') lines.push(
    '  -H "Idempotency-Key: ${REQUEST_ID:?Tao REQUEST_ID mot lan cho yeu cau moi}"',
    '  -F "printer_id=${PRINTER_ID}"', `  -F 'format=${format}'`, "  -F 'title=Invoice example'", "  -F 'copies=1'",
    `  -F 'file=@./${format === 'pdf' ? 'invoice.pdf;type=application/pdf' : 'label.zpl;type=text/plain'}'`,
  );
  return lines.join(' \\\n');
}
const json = value => JSON.stringify(value, null, 2);
function schemaDocument(name) {
  const schema = contract.$defs[name], dependencies = {};
  // Copied schemas include every referenced definition and work offline.
  const visit = node => {
    if (!node || typeof node !== 'object') return;
    if (node.$ref) { const key = node.$ref.split('/').at(-1); if (!dependencies[key]) { dependencies[key] = contract.$defs[key]; visit(dependencies[key]); } }
    Object.values(node).forEach(value => { if (typeof value === 'object') visit(value); });
  };
  visit(schema);
  return { $schema: contract.$schema, ...schema, ...(Object.keys(dependencies).length ? { $defs: dependencies } : {}) };
}
function CodeBlock({ title, text, notice, disabled = false, fullHeight = false }) {
  const ref = useRef(null);
  return <div className="api-code"><div className="section-heading"><strong>{title}</strong><Button className="secondary" disabled={disabled} aria-label={`Sao chép ${title}`} onClick={async () => {
    try { if (!navigator.clipboard) throw new Error('unavailable'); await navigator.clipboard.writeText(text); notice('Đã sao chép.', 'success'); }
    catch { if (!ref.current) return; const range = document.createRange(); range.selectNodeContents(ref.current); const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range); ref.current.focus(); notice('Đã chọn code. Nhấn ⌘C hoặc Ctrl+C để sao chép.', 'info'); }
  }}>Sao chép</Button></div><pre ref={ref} className={fullHeight ? 'api-code-full' : undefined} tabIndex={0} aria-label={title}><code>{text}</code></pre></div>;
}
function Fields({ endpoint }) {
  const fields = endpoint.bodySchema ? contract.$defs[endpoint.bodySchema].properties : {};
  const hints = { file: 'File PDF hoặc ZPL UTF-8; giới hạn theo thiết bị.', printer_id: 'PRINTER_ID từ bước Chọn máy in.', format: 'pdf hoặc zpl; máy phải hỗ trợ định dạng này.', title: 'Tiêu đề 1–128 ký tự sau khi bỏ khoảng trắng đầu/cuối.', copies: '1–100. Mặc định: 1.', paper_size: 'Khổ giấy, ví dụ A4. Bỏ qua để dùng mặc định.', sides: 'one-sided, two-sided-long-edge hoặc two-sided-short-edge nếu máy hỗ trợ.', color_mode: 'monochrome hoặc color nếu máy hỗ trợ.', scaling: 'Tỷ lệ PDF: lấy từ capabilities.supported.scaling.' };
  const rows = [
    ...endpoint.parameters.map(field => ({ ...field, hint: field.description })),
    ...Object.entries(fields).map(([name, field]) => ({ name, in: 'form', required: contract.$defs[endpoint.bodySchema].required.includes(name), hint: hints[name] || field.description })),
  ];
  if (!rows.length) return <p className="api-empty">Không có body hay tham số bổ sung.</p>;
  return <div className="api-field-list" aria-label="Tham số request">{rows.map(field => <div className="api-field" key={`${field.in}-${field.name}`}>
    <div><code>{field.name}</code><span className="api-field-location">{field.in}</span><span className={field.required ? 'api-required' : 'muted'}>{field.required ? 'Bắt buộc' : 'Tùy chọn'}</span></div>
    <p>{field.hint}{field.default !== undefined && <> Mặc định: {field.default}.</>}</p>
  </div>)}</div>;
}
function JobStates() {
  return <details className="api-disclosure"><summary>Ý nghĩa trạng thái lệnh</summary><dl className="api-state-list">
    <dt>queued / held</dt><dd>Đang chờ / đang giữ; chưa giao xuống CUPS.</dd>
    <dt>submitting / submitted</dt><dd>Đang giao / đã giao cho CUPS.</dd>
    <dt>completed</dt><dd>CUPS báo hoàn thành; không bảo đảm giấy đã ra.</dd>
    <dt>failed / canceled</dt><dd>Thất bại / đã hủy. Không thu hồi được giấy đã in.</dd>
    <dt>unknown</dt><dd>Nhờ người vận hành xác minh; không tự in lại.</dd>
  </dl></details>;
}
function Endpoint({ endpoint, task, notice, invalid, prefix, onSetup }) {
  const [format, setFormat] = useState('pdf'), [responseIndex, setResponseIndex] = useState(0);
  const [fieldsOpen, setFieldsOpen] = useState(() => !endpoint.bodySchema || !matchMedia('(max-width: 700px)').matches);
  const response = endpoint.responses[responseIndex];
  const fieldCount = endpoint.parameters.length + Object.keys(endpoint.bodySchema ? contract.$defs[endpoint.bodySchema].properties : {}).length;
  return <article className="api-endpoint" aria-labelledby={`${prefix}-endpoint-title`}>
    <header className="api-endpoint-header"><p className="eyebrow">{task.hint}</p><h3 id={`${prefix}-endpoint-title`}>{task.title}</h3><p>{task.summary}</p>
      <div className="api-route"><span className={`api-method api-method-${endpoint.method.toLowerCase()}`}>{endpoint.method}</span><code>{endpoint.path}</code></div>
    </header>
    {endpoint.id === 'submit' && <div className="api-warning"><strong>Lệnh này có thể in thật.</strong> Tạo REQUEST_ID một lần. Nếu mất phản hồi, chỉ gửi lại cùng ID, file, filename và mọi trường; không dùng <code>--retry</code>.</div>}
    {endpoint.id === 'cancel' && <div className="api-warning"><strong>Có thể hủy lệnh thật.</strong> Kiểm tra JOB_ID; không tự gửi lại sau timeout. Giấy đã in không thu hồi được.</div>}
    <section className="api-request"><div className="section-heading"><h4>Request</h4><span className="api-caption">{endpoint.bodySchema ? 'multipart/form-data' : 'Không có body'}</span></div>
      {endpoint.bodySchema && <label className="api-format">File ví dụ<Select aria-label="Định dạng ví dụ" value={format} onChange={event => setFormat(event.target.value)}><option value="pdf">PDF · invoice.pdf</option><option value="zpl">ZPL · label.zpl</option></Select></label>}
      <div className="api-request-grid"><div>{endpoint.bodySchema && <Button className="api-fields-toggle secondary" aria-expanded={fieldsOpen} aria-controls={`${prefix}-fields`} onClick={() => setFieldsOpen(!fieldsOpen)}>Tham số request · {fieldCount} <span aria-hidden="true">{fieldsOpen ? '−' : '+'}</span></Button>}<div id={`${prefix}-fields`} hidden={!fieldsOpen}><Fields endpoint={endpoint} />{endpoint.bodySchema && <p className="api-caption">Không gửi JSON body. Để cURL tự đặt multipart boundary. ZPL gửi nguyên bản, không áp tùy chọn driver PDF.</p>}
        {endpoint.id === 'cancel' && <p className="api-caption">Không cần Content-Type hoặc JSON body.</p>}
      </div></div><div>
        {endpoint.bodySchema && <><CodeBlock title="Tạo REQUEST_ID một lần" text={requestIdExample} notice={notice} /><p className="api-caption">Ví dụ dùng Python 3; ứng dụng có thể tạo UUID. Không tạo ID mới khi gửi lại.</p></>}
        <CodeBlock title={`cURL ${endpoint.method} ${endpoint.path}`} text={curlExample(endpoint, format)} notice={notice} disabled={invalid} fullHeight />
        <p className="api-caption">Chạy <Button className="text-button" onClick={onSetup}>Thiết lập cURL</Button> trước. Thay các ID và file ví dụ trên máy của bạn.</p>
      </div></div>
    </section>
    <section className="api-response"><div className="section-heading"><h4>Response</h4><span className="api-caption">application/json</span></div>
      {endpoint.responses.length > 1 && <div className="api-response-switch" role="group" aria-label="Các response"><Button className="secondary" aria-pressed={responseIndex === 0} onClick={() => setResponseIndex(0)}>202 · Lệnh mới</Button><Button className="secondary" aria-pressed={responseIndex === 1} onClick={() => setResponseIndex(1)}>200 · Gửi lại</Button></div>}
      <p className="api-result"><span className="api-http">HTTP {response.status}</span> {response.description}</p>
      <CodeBlock title={`Ví dụ response ${response.status}`} text={json(response.example)} notice={notice} />
      <p className="api-result-hint">{task.result}</p>
      {endpoint.id === 'submit' && <p className="api-caption">deduplicated = true: không tạo lệnh trùng. history_expired = true: lịch sử đã dọn, không tự tạo lệnh mới để in lại.</p>}
      {['job', 'jobs', 'cancel'].includes(endpoint.id) && <><p className="api-warning">completed chỉ là CUPS báo hoàn thành. unknown cần người vận hành kiểm tra; không tự in lại.</p><JobStates /></>}
    </section>
    <details className="api-disclosure"><summary>Mã lỗi · {endpoint.errors.map(error => error.status).join(', ')}</summary><table><thead><tr><th>HTTP</th><th>Cách hiểu</th></tr></thead><tbody>{endpoint.errors.map(error => <tr key={error.status}><td><code>{error.status}</code></td><td>{error.description}</td></tr>)}</tbody></table>
      <p className="api-caption">422 có thể trả detail là danh sách lỗi trường/query.</p><CodeBlock title="Ví dụ response lỗi" text={json({ detail: 'Invalid API key' })} notice={notice} /><CodeBlock title="JSON schema lỗi" text={json(schemaDocument('Error'))} notice={notice} />
    </details>
    <details className="api-disclosure"><summary>Schema & chi tiết kỹ thuật</summary><p>{endpoint.description}</p>
      {!!endpoint.parameters.length && <table><thead><tr><th>Tham số</th><th>Giới hạn</th></tr></thead><tbody>{endpoint.parameters.map(field => <tr key={field.name}><td><code>{field.name}</code></td><td>{field.pattern || field.description}</td></tr>)}</tbody></table>}
      {endpoint.bodySchema && <CodeBlock title="JSON schema request" text={json(schemaDocument(endpoint.bodySchema))} notice={notice} />}
      {endpoint.responses.map(item => <CodeBlock key={item.status} title={`Schema response ${item.status}`} text={json(schemaDocument(item.schema))} notice={notice} />)}
    </details>
  </article>;
}
export function ApiGuide({ notice }) {
  const setupRef = useRef(null), contentRef = useRef(null), prefix = useId(), [base, setBase] = useState(location.origin), [query, setQuery] = useState(''), [selected, setSelected] = useState('printers');
  let origin = '', invalid = false;
  try { const url = new URL(base); if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== '/') throw new Error(); origin = url.origin; }
  catch { invalid = true; }
  const matches = tasks.filter(task => { const endpoint = contract.endpoints.find(item => item.id === task.id); return normalizeSearch(`${endpoint.method} ${endpoint.path} ${endpoint.title} ${task.title}`).includes(normalizeSearch(query.trim())); });
  const currentTask = matches.find(task => task.id === selected) || matches[0];
  const setup = invalid ? '# Nhập Base URL hợp lệ trước khi sao chép.' : `export BASE_URL=${quote(origin)}\nexport API_KEY='<API_KEY>'\nexport PRINTER_ID='ID_FROM_PRINTER_LIST'\nexport JOB_ID='ID_FROM_JOB_RESPONSE'`;
  return <div className="api-reference"><header className="api-guide-heading"><div><p className="eyebrow">TÍCH HỢP ỨNG DỤNG</p><h2>API Guide</h2><p>Chọn tác vụ, copy request, kiểm tra response.</p></div><span className="pill">Client API v1 · Chỉ đọc</span></header>
    <section className="api-connection" aria-label="Kết nối API"><div className="api-connection-row"><label className="setting-field" htmlFor={`${prefix}-base`}>Base URL<Input id={`${prefix}-base`} type="url" value={base} onChange={event => setBase(event.target.value)} aria-invalid={invalid} aria-describedby={invalid ? `${prefix}-base-error` : undefined} /></label><div className="api-auth"><span>Header cho mọi request</span><code>Authorization: Bearer &lt;API_KEY&gt;</code></div></div>
      {invalid && <p id={`${prefix}-base-error`} className="bad" role="alert">Nhập origin HTTP/HTTPS, không có credentials, path, query hoặc fragment.</p>}
      <details ref={setupRef} className="api-setup"><summary>Thiết lập cURL · bắt đầu tại đây</summary><p className="api-caption">Thay placeholder bằng key do quản trị cấp; không nhập key thật vào trang này.</p><CodeBlock title="Biến môi trường (thay placeholder trên máy client)" text={setup} notice={notice} disabled={invalid} /><p className="api-caption">API key không phải mật khẩu quản trị. Gọi từ backend; trình duyệt khác origin không được cấp CORS. HTTP không mã hóa key: chỉ dùng LAN tin cậy/TLS. Giữ key ngoài Git, log và chat.</p></details>
    </section>
    <div className="api-layout"><aside className="api-sidebar"><label className="setting-field" htmlFor={`${prefix}-search`}>Tìm API<Input id={`${prefix}-search`} type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Gửi file, hủy, GET…" /></label>
      <nav aria-label="Các tác vụ API">{matches.map(task => { const endpoint = contract.endpoints.find(item => item.id === task.id); return <Button key={task.id} className="api-task" aria-current={currentTask?.id === task.id ? 'step' : undefined} aria-controls={`${prefix}-content`} onClick={() => { setSelected(task.id); if (matchMedia('(max-width: 700px)').matches) { contentRef.current.focus({ preventScroll: true }); contentRef.current.scrollIntoView({ block: 'start' }); } }}><span className="api-task-number" aria-hidden="true">{tasks.indexOf(task) + 1}</span><span className="api-task-text"><strong>{task.title}</strong><small>{task.hint}</small></span><span className={`api-method api-method-${endpoint.method.toLowerCase()}`}>{endpoint.method}</span></Button>; })}</nav>
      <p className="api-caption">{matches.length} / {tasks.length} API · Ví dụ dùng dữ liệu giả.</p>
    </aside><div ref={contentRef} id={`${prefix}-content`} className="api-content" role="region" tabIndex={-1} aria-label="Chi tiết API đã chọn">{currentTask ? <Endpoint key={currentTask.id} endpoint={contract.endpoints.find(item => item.id === currentTask.id)} task={currentTask} notice={notice} invalid={invalid} prefix={prefix} onSetup={() => { const element = setupRef.current; element.open = true; element.querySelector('summary').focus(); element.scrollIntoView({ block: 'nearest' }); }} /> : <p className="api-no-results" role="status">Không có API phù hợp. Thử tên tác vụ hoặc đường dẫn.</p>}</div></div>
    <p className="api-readonly-note">Trang hướng dẫn không gọi API, gửi file hay hủy lệnh. Chỉ chạy ví dụ trên máy của bạn khi đã kiểm tra đúng key, máy in và file.</p>
  </div>;
}
