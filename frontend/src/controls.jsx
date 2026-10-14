import React, { useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Button, DirtyContext, Input, normalizeSearch } from './shared.jsx';

export function DriverPicker({ id = 'driver', entries, current = '', onChange }) {
  const input = useRef(null), host = useRef(null);
  const dirty = useContext(DirtyContext);
  const all = useMemo(() => {
    const items = current && !entries.some(item => item.id === current) ? [{ id: current, label: `Driver hiện tại · ${current}` }, ...entries] : entries;
    return items.map(item => ({ ...item, text: normalizeSearch(`${item.label} ${item.id}`) }));
  }, [entries, current]);
  const initial = all.find(item => item.id === current);
  const [committed, setCommitted] = useState(initial || null);
  const [selected, setSelected] = useState(current), [query, setQuery] = useState(initial?.label || '');
  const [open, setOpen] = useState(false), [active, setActive] = useState(-1);
  const tokens = normalizeSearch(query === committed?.label ? '' : query).trim().split(/\s+/).filter(Boolean);
  const filtered = all.filter(item => tokens.every(token => item.text.includes(token)));
  const matches = filtered.slice(0, 200), chosen = filtered.find(item => item.id === selected);
  if (chosen && !matches.includes(chosen)) matches[matches.length - 1] = chosen;
  useEffect(() => { input.current.setCustomValidity(selected ? '' : 'Hãy chọn đúng driver trong danh sách kết quả.'); }, [selected]);
  useEffect(() => { if (open && active >= 0) host.current.querySelector(`[id="${id}-option-${active}"]`)?.scrollIntoView({ block: 'nearest' }); }, [active, open, id]);
  const close = () => { setOpen(false); setActive(-1); };
  const choose = item => { setCommitted(item); setSelected(item.id); setQuery(item.label); onChange(item.id); dirty(); close(); input.current.focus(); };
  const keyDown = event => {
    if (event.key === 'Escape') {
      event.preventDefault(); event.stopPropagation();
      if (committed) { setSelected(committed.id); setQuery(committed.label); onChange(committed.id); }
      close();
    } else if (event.key === 'Enter') {
      event.preventDefault(); if (open && active >= 0 && matches[active]) choose(matches[active]);
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); setOpen(true);
      if (matches.length) setActive(index => index < 0 ? (event.key === 'ArrowDown' ? 0 : matches.length - 1) : (index + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length);
    } else if (open && ['Home', 'End'].includes(event.key)) {
      event.preventDefault(); if (matches.length) setActive(event.key === 'Home' ? 0 : matches.length - 1);
    }
  };
  return <div id={`${id}-picker`} className="driver-picker" ref={host} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) close(); }}>
    <div className="combo-field">
      <Input ref={input} type="search" className="driver-search" id={`${id}-search`} autoComplete="off" required placeholder="Tìm model hoặc tên driver…"
        role="combobox" aria-autocomplete="list" aria-expanded={open} aria-controls={`${id}-list`} aria-describedby={`${id}-results`}
        aria-activedescendant={open && active >= 0 ? `${id}-option-${active}` : undefined} value={query} onClick={() => { setOpen(true); setActive(-1); }}
        onChange={event => { setQuery(event.target.value); setSelected(''); onChange(''); setOpen(true); setActive(-1); }} onKeyDown={keyDown} />
      <Button className="combo-clear" aria-label="Xóa lựa chọn driver" onClick={() => { setCommitted(null); setSelected(''); setQuery(''); onChange(''); dirty(); setOpen(true); setActive(-1); input.current.focus(); }}>×</Button>
    </div>
    <input type="hidden" id={id} value={selected} readOnly />
    <div className="combo-popup" hidden={!open}>
      <div id={`${id}-results`} className="combo-hint" role="status">{filtered.length ? `${filtered.length.toLocaleString('vi-VN')} kết quả${filtered.length > 200 ? ' · Hiển thị 200, hãy nhập cụ thể hơn' : ''}` : 'Không tìm thấy driver. Thử từ khóa khác hoặc kiểm tra driver đã cài.'}</div>
      <div id={`${id}-list`} className="combo-list" role="listbox" aria-label="Driver phù hợp">
        {matches.map((item, index) => <div key={item.id} id={`${id}-option-${index}`} className="combo-option" role="option" aria-selected={active === index}
          onPointerDown={event => event.preventDefault()} onClick={() => choose(item)}>{item.label}</div>)}
      </div>
    </div>
  </div>;
}

// The selection belongs to the form, not the refreshed printer list: never silently grant/regrant.
export function GrantPicker({ id, printers, value, onChange }) {
  const host = useRef(null), toggle = useRef(null), search = useRef(null);
  const dirty = useContext(DirtyContext);
  const [query, setQuery] = useState(''), [open, setOpen] = useState(false);
  useEffect(() => {
    // Closing on click (not pointerdown) preserves the Save button's geometry until submission.
    const outside = event => { if (!host.current?.contains(event.target)) setOpen(false); };
    document.addEventListener('click', outside); return () => document.removeEventListener('click', outside);
  }, []);
  const filtered = printers.filter(printer => normalizeSearch(printer.name).includes(normalizeSearch(query)));
  const change = (id, checked) => { onChange(checked ? [...new Set([...value, id])] : value.filter(item => item !== id)); dirty(); };
  const keyDown = event => {
    const controls = [search.current, ...host.current.querySelectorAll('.grant-list input')];
    const index = controls.indexOf(document.activeElement);
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setOpen(false); toggle.current.focus(); }
    else if (event.key === 'Enter') { event.preventDefault(); if (event.target.type === 'checkbox') event.target.click(); }
    else if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); controls[(index + (event.key === 'ArrowDown' ? 1 : controls.length - 1)) % controls.length]?.focus(); }
  };
  return <div className="grant-picker" id={id} ref={host} onBlur={event => {
    const next = event.relatedTarget;
    if (!host.current.contains(next) && next?.closest('form') !== host.current.closest('form')) setOpen(false);
  }}>
    <Button ref={toggle} id={`${id}-toggle`} className="grant-toggle secondary" aria-expanded={open} aria-controls={`${id}-popup`}
      onClick={() => { setOpen(!open); if (!open) requestAnimationFrame(() => search.current?.focus()); }}>{value.length ? 'Thay đổi máy được cấp ▾' : 'Chọn máy in ▾'}</Button>
    <div className="grant-chips">{value.map(printerId => {
      const name = printers.find(item => item.id === printerId)?.name || 'Máy không còn trong danh sách';
      return <span className="grant-chip" key={printerId}><span>{name}</span><Button aria-label={`Bỏ cấp: ${name}`} onClick={() => { change(printerId, false); toggle.current.focus(); }}>×</Button></span>;
    })}</div>
    <p className="muted grant-count" role="status">{value.length ? `Đã chọn ${value.length} máy` : 'Chưa cấp máy nào'}</p>
    <div className="grant-popup" id={`${id}-popup`} hidden={!open} onKeyDown={keyDown}>
      <Input ref={search} className="picker-search" type="search" placeholder="Tìm tên máy…" aria-label="Tìm máy được cấp" value={query} onChange={event => setQuery(event.target.value)} />
      <div className="grant-list" role="group" aria-label="Máy được cấp">
        {!filtered.length && <p className="muted">{printers.length ? 'Không có máy phù hợp.' : 'Chưa có máy in đăng ký.'}</p>}
        {filtered.slice(0, 200).map(printer => <label className="check" key={printer.id}><Input type="checkbox" value={printer.id} checked={value.includes(printer.id)} onChange={event => change(printer.id, event.target.checked)} /><span>{printer.name}</span></label>)}
        {filtered.length > 200 && <p className="muted">Hiển thị 200 máy. Hãy nhập tên cụ thể hơn.</p>}
      </div>
    </div>
  </div>;
}
