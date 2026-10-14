import React, { createContext, useCallback, useContext, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';

export const BusyContext = createContext(false);
export const DirtyContext = createContext(() => {});
export const CloseContext = createContext(() => {});
// Explicit disabled attributes preserve both native validation and test/accessibility semantics.
export function Input(props) { const busy = useContext(BusyContext); return <input {...props} disabled={busy || props.disabled} />; }
export function Select(props) { const busy = useContext(BusyContext); return <select {...props} disabled={busy || props.disabled} />; }
export function Textarea(props) { const busy = useContext(BusyContext); return <textarea {...props} disabled={busy || props.disabled} />; }
export function Button({ alwaysEnabled, ...props }) { const busy = useContext(BusyContext); return <button type="button" {...props} disabled={(!alwaysEnabled && busy) || props.disabled} />; }

export function useToasts() {
  const [items, setItems] = useState([]);
  const nextId = useRef(0);
  const show = useCallback((message, kind = 'info') => setItems(previous => {
    message = String(message);
    if (previous.some(item => item.message === message && item.kind === kind)) return previous;
    return [{ id: ++nextId.current, message, kind }];
  }), []);
  const dismiss = useCallback(id => setItems(previous => previous.filter(item => item.id !== id)), []);
  const clear = useCallback(() => setItems([]), []);
  return { items, show, dismiss, clear };
}

export function Notice({ id, items, dismiss }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    const host = ref.current;
    // Native top-layer popovers keep notices visible above a modal without weakening CSP.
    if (host.matches(':popover-open')) host.hidePopover();
    if (items.length) host.showPopover();
  }, [items]);
  return <section ref={ref} id={id} className="toast-region" popover="manual" aria-label="Thông báo">
    {items.map(item => <Toast key={item.id} item={item} dismiss={dismiss} />)}
  </section>;
}
function Toast({ item, dismiss }) {
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (item.kind === 'error' || paused) return;
    const timer = setTimeout(() => dismiss(item.id), 5000);
    return () => clearTimeout(timer);
  }, [item, paused, dismiss]);
  return <div className={`toast ${item.kind}`} role={item.kind === 'error' ? 'alert' : 'status'}
    onPointerEnter={() => setPaused(true)} onPointerLeave={() => setPaused(false)}
    onFocus={() => setPaused(true)} onBlur={() => setPaused(false)}>
    <span>{item.message}</span><Button alwaysEnabled className="toast-close" aria-label="Đóng thông báo" onClick={() => dismiss(item.id)}>×</Button>
  </div>;
}
export function HelpButton({ title, explanation }) {
  const id = useId();
  const [expanded, setExpanded] = useState(false);
  return <><Button alwaysEnabled className="help-button" aria-label={`Giải thích: ${title}`} popoverTarget={id} aria-controls={id} aria-expanded={expanded}>?</Button>
    <div id={id} className="help-popover" popover="auto" onToggle={event => setExpanded(event.newState === 'open')}>
      <strong>{title}</strong><p>{explanation}</p><Button alwaysEnabled className="secondary" popoverTarget={id} popoverTargetAction="hide">Đóng</Button>
    </div></>;
}
export function FieldLabel({ htmlFor, title, explanation, children }) {
  return <div className="field-label"><label htmlFor={htmlFor}>{children}</label>{explanation && <HelpButton title={title || String(children)} explanation={explanation} />}</div>;
}
export function Modal({ title, trigger, busy, onClose, children, readOnly = false, toasts }) {
  const ref = useRef(null), dirty = useRef(false);
  useLayoutEffect(() => {
    const dialog = ref.current;
    dialog.showModal(); dialog.querySelector('#modal-title').focus();
    return () => { if (dialog.open) dialog.close(); if (trigger?.isConnected) trigger.focus(); };
  }, [trigger]);
  const close = () => {
    if (busy || (dirty.current && !confirm('Bạn có thay đổi chưa lưu. Bỏ thay đổi và đóng?'))) return;
    onClose();
  };
  const markDirty = event => {
    if (event.target.closest('form') && !event.target.classList.contains('picker-search')) dirty.current = true;
  };
  return <dialog ref={ref} className="entity-modal" id="entity-modal" aria-labelledby="modal-title"
    onCancel={event => { event.preventDefault(); close(); }} onClick={event => { if (event.target === event.currentTarget) close(); }}
    onInputCapture={markDirty} onChangeCapture={markDirty}>
    <header className="modal-header"><h2 id="modal-title" tabIndex={-1}>{title}</h2><Button className="quiet" aria-label="Đóng modal" onClick={close}>×</Button></header>
    <div className="modal-content"><CloseContext.Provider value={close}><DirtyContext.Provider value={() => { dirty.current = true; }}>{children}</DirtyContext.Provider></CloseContext.Provider></div>
    <div className="modal-footer"><Button className="secondary" onClick={close}>{readOnly ? 'Đóng' : 'Hủy'}</Button></div>
    {toasts}
  </dialog>;
}
export function normalizeSearch(value) {
  return String(value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[đĐ]/g, match => match === 'đ' ? 'd' : 'D').toLowerCase();
}
export const fieldHelp = {
  name: 'Tên để nhận biết trong ứng dụng, ví dụ Canon văn phòng. Đổi tên không đổi địa chỉ máy in, mã định danh hay lệnh đã nhận.',
  formats: 'Chỉ bật định dạng máy và driver thực sự hỗ trợ. PDF là tài liệu; ZPL là ngôn ngữ in nhãn gửi nguyên bản. Ứng dụng không chuyển PDF thành ZPL.',
  driver: 'Driver dịch tài liệu thành dữ liệu mà máy in hiểu. Gõ model hoặc mã PPD rồi chọn một kết quả. Danh sách chỉ gồm driver đã cài trên thiết bị; tìm kiếm không cài driver mới. Không chọn model gần giống nếu chưa xác nhận tương thích.',
  uri: 'Địa chỉ mà CUPS dùng để kết nối máy in. Chọn thiết bị đã tìm thấy hoặc nhập địa chỉ LAN trong mục nâng cao. Đổi địa chỉ có thể chuyển lệnh tới máy khác; cấu hình của lệnh đã nhận không tự thay đổi.',
  grants: 'Client chỉ được gửi lệnh tới máy được chọn và xem lệnh của chính nó. Có thể chưa cấp máy nào. Mở danh sách, tìm tên và tích từng máy. Các thẻ phía ngoài thể hiện máy đã chọn; bấm × để bỏ cấp. Thay đổi quyền không hủy lệnh đã nhận.',
  client: 'Tên ứng dụng sử dụng API, ví dụ Odoo kho. Client không phải tài khoản quản trị. Mỗi client có API key riêng; đổi tên không đổi key hay quyền máy in.',
  queue: 'Hàng đợi đã có trong CUPS, tức hệ thống in trên thiết bị. Đăng ký chỉ tạo liên kết trong ứng dụng, không cài driver hay sửa hàng đợi. Máy bắt đầu ở trạng thái tạm dừng trong ứng dụng.',
  settingsUpload: 'Kích thước tối đa của một tệp PDF/ZPL, tính bằng byte. Ví dụ 10485760 tương đương 10 MiB. Tệp lớn hơn bị từ chối trước khi nhận lệnh.',
  settingsJobs: 'Tổng số lệnh đang chờ, đang giữ, đã giao hoặc chưa rõ kết quả được phép tồn tại. Đạt giới hạn thì từ chối lệnh mới; không xóa lệnh đã nhận.',
  settingsDisk: 'Dung lượng đĩa phải để trống sau khi nhận tệp, tính bằng byte. Ví dụ 104857600 là 100 MiB. Không đặt quá thấp vì thiết bị cần chỗ cho hệ điều hành và CUPS.',
  settingsRetention: 'Số ngày giữ chi tiết lệnh đã kết thúc, mặc định 30. Dấu chống trùng vẫn được giữ; không xóa lệnh đang chờ hoặc chưa rõ kết quả. Tệp đã kết thúc được dọn riêng. Thiết lập này không thay đổi lưu trữ của CUPS.',
};
export function optionLabel(item) {
  const labels = { PageSize: 'Khổ giấy', Duplex: 'In hai mặt', MediaType: 'Loại giấy', InputSlot: 'Khay giấy', Resolution: 'Độ phân giải', 'print-scaling': 'Căn nội dung PDF', BindEdge: 'Cạnh đóng gáy', CNDraftMode: 'Tiết kiệm mực', Collate: 'Sắp bộ bản in' };
  return labels[item.name] || item.label || item.name;
}
export function choiceLabel(item, value) {
  const labels = { BindEdge: { Left: 'Trái', Top: 'Trên' }, Duplex: { None: 'Một mặt', DuplexNoTumble: 'Hai mặt · lật cạnh dài', DuplexTumble: 'Hai mặt · lật cạnh ngắn' }, 'print-scaling': { auto: 'Tự động theo CUPS', 'auto-fit': 'Tự động thu vừa trang', fit: 'Vừa vùng in · giữ toàn bộ nội dung', fill: 'Lấp đầy vùng in · có thể cắt nội dung', none: 'Giữ kích thước gốc' } };
  return labels[item.name]?.[value] || item.choices?.find(choice => choice.value === value)?.label || value;
}
export function validateOptionConstraints(schema, values) {
  for (const constraint of schema?.constraints || []) {
    if (values[constraint.option1] !== constraint.choice1 || values[constraint.option2] !== constraint.choice2) continue;
    const describe = (name, value) => { const item = schema.options?.find(option => option.name === name); return `${optionLabel(item || { name })}: ${item ? choiceLabel(item, value) : value}`; };
    return `${describe(constraint.option1, constraint.choice1)} không tương thích với ${describe(constraint.option2, constraint.choice2)}.`;
  }
  return '';
}
export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return 'không rõ';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']; let value = bytes, index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index++; }
  return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
}
