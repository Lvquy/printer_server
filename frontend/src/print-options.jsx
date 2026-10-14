import React, { useEffect, useRef } from 'react';
import { HelpButton, Input, Select, choiceLabel, optionLabel, validateOptionConstraints } from './shared.jsx';

export const usableSchema = schema => ['available', 'partial'].includes(schema?.availability);
const members = schema => (schema?.options || []).filter(item => item.name !== 'PageRegion' && Array.isArray(item.choices) && item.choices.length);
export function initialOptions(defaults = {}, allowed = {}, client = false) {
  return { values: client ? {} : { ...defaults }, rights: Object.fromEntries(Object.entries(allowed).map(([name, choices]) => [name, [...choices]])), dropLegacy: false };
}
function legacyOptions(schema, defaults, allowed) {
  const options = members(schema);
  const known = (key, value) => options.some(item => item.name === key && item.choices.some(choice => choice.value === value));
  return {
    unknownDefaults: Object.fromEntries(Object.entries(defaults).filter(([key, value]) => !known(key, value))),
    unknownAllowed: Object.fromEntries(Object.entries(allowed).map(([key, values]) => [key, values.filter(value => !known(key, value))]).filter(([, values]) => values.length)),
  };
}
export function optionsPayload(schema, model, { defaults = {}, allowed = {}, client = false, format = 'pdf' } = {}) {
  const options = members(schema);
  const values = Object.fromEntries(options.filter(item => model.values[item.name] && item.choices.some(choice => choice.value === model.values[item.name])).map(item => [item.name, model.values[item.name]]));
  if (client) return { options: format === 'pdf' ? values : {} };
  const legacy = legacyOptions(schema, defaults, allowed);
  const nextDefaults = { ...(model.dropLegacy ? {} : legacy.unknownDefaults), ...values };
  const nextAllowed = model.dropLegacy ? {} : { ...legacy.unknownAllowed };
  for (const item of options) {
    const choices = (model.rights[item.name] || []).filter(value => item.choices.some(choice => choice.value === value));
    const merged = [...new Set([...(nextAllowed[item.name] || []), ...choices])];
    if (merged.length) nextAllowed[item.name] = merged;
  }
  return { default_options: nextDefaults, allowed_options: nextAllowed };
}
export function optionsError(schema, model, config = {}) {
  if (config.client && config.format === 'zpl') return '';
  if (!usableSchema(schema)) return schema?.reason || 'Chưa đọc được khả năng của máy in.';
  const payload = optionsPayload(schema, model, config);
  const driver = Object.fromEntries(members(schema).filter(item => item.default).map(item => [item.name, item.default]));
  const effective = config.client ? { ...driver, ...schema.default_options, ...config.defaults, ...payload.options } : { ...driver, ...payload.default_options };
  return validateOptionConstraints(schema, effective);
}
function AdvancedOptions({ error, children }) {
  const ref = useRef(null);
  // Keep the user's disclosure state; only a conflict forces it open.
  useEffect(() => { if (error) ref.current.open = true; }, [error]);
  return <details ref={ref} className="advanced option-advanced"><summary>Tùy chọn driver nâng cao</summary>{children}</details>;
}
export function PrintOptions({ schema, model, onChange, defaults = {}, allowed = {}, client = false, format = 'pdf' }) {
  if (!usableSchema(schema)) return <div className="option-editor"><p className="bad">{schema?.reason || 'Chưa đọc được khả năng của máy in.'}</p></div>;
  const options = client && format === 'zpl' ? [] : members(schema);
  const legacy = legacyOptions(schema, defaults, allowed);
  const error = optionsError(schema, model, { defaults, allowed, client, format });
  const selectValue = (name, value) => {
    const rights = { ...model.rights };
    // A fixed default cannot be accidentally excluded from the allowed values.
    if (!client && value) rights[name] = [...new Set([...(rights[name] || []), value])];
    onChange({ ...model, values: { ...model.values, [name]: value }, rights });
  };
  let message = client ? (format === 'zpl' ? 'ZPL gửi nguyên bản. Không áp dụng tùy chọn PDF.' : options.length ? 'Chỉ có thể đổi những giá trị quản trị đã cấp.' : 'Dùng cấu hình mặc định; quản trị chưa cấp tùy chọn để thay đổi.') : 'Chọn mặc định driver. Client được chọn mọi tùy chọn phổ thông đã ánh xạ chắc chắn; danh sách quyền cũ bên dưới không còn giới hạn API.';
  if (schema.availability === 'partial') message += ' CUPS chỉ báo một phần khả năng; không xác nhận các ràng buộc riêng của driver.';
  return <div className="option-editor"><p className="muted">{message}</p>
    {!client && (Object.keys(legacy.unknownDefaults).length > 0 || Object.keys(legacy.unknownAllowed).length > 0) && <>
      <p className="bad">Có cấu hình cũ không được schema hiện tại xác nhận. Mặc định giữ nguyên; chỉ xóa khi bạn chủ động chọn dưới đây.</p>
      <label className="check"><Input type="checkbox" checked={model.dropLegacy} onChange={event => onChange({ ...model, dropLegacy: event.target.checked })} />Xóa các giá trị cũ không còn trong schema khi lưu</label>
    </>}
    {['common', 'advanced'].map(group => {
      const items = options.filter(item => (item.group === 'advanced' ? 'advanced' : 'common') === group);
      if (!items.length) return null;
      const fields = items.map(item => {
        const title = optionLabel(item), current = defaults[item.name] ?? item.default ?? '';
        return <fieldset className="option-field" key={item.name}><legend>{title}<HelpButton title={title} explanation={item.name === 'print-scaling' ? 'Vừa vùng in giữ toàn bộ nội dung; lấp đầy có thể cắt mép. Không lựa chọn nào tạo khả năng in tràn lề. Máy laser thường vẫn có vùng không in được.' : `Các giá trị do CUPS/driver báo cho ${item.name}. Client chỉ được đổi trong danh sách quản trị cấp. Giá trị mặc định được chụp cùng lệnh khi nhận, không tự đổi cho lệnh cũ.`} /></legend>
          {client && item.choices.length === 1 && item.choices[0].value === current ? <p>{choiceLabel(item, current)} · do quản trị cố định</p> : <Select data-option-name={item.name} aria-label={`${title}: ${client ? 'lựa chọn' : 'giá trị mặc định'}`} value={model.values[item.name] || ''} onChange={event => selectValue(item.name, event.target.value)}>
            <option value="">{client ? `Dùng mặc định${current ? ` (${choiceLabel(item, current)})` : ''}` : `Dùng mặc định driver${item.default ? ` (${choiceLabel(item, item.default)})` : ''}`}</option>
            {item.choices.map(choice => <option key={choice.value} value={choice.value}>{choiceLabel(item, choice.value)}</option>)}
          </Select>}
          {!client && <><details className="option-rights"><summary>Cấu hình quyền cũ (không áp dụng API mới)</summary><div className="option-choices">
            {item.choices.map(choice => <label className="check option-choice" key={choice.value}><Input type="checkbox" value={choice.value} checked={(model.rights[item.name] || []).includes(choice.value)} onChange={event => {
              let rights = event.target.checked ? [...new Set([...(model.rights[item.name] || []), choice.value])] : (model.rights[item.name] || []).filter(value => value !== choice.value);
              if (model.values[item.name]) rights = [...new Set([...rights, model.values[item.name]])];
              onChange({ ...model, rights: { ...model.rights, [item.name]: rights } });
            }} />{choiceLabel(item, choice.value)}</label>)}
          </div></details><small>Chỉ giữ để đọc cấu hình cũ. API mới không dùng danh sách này để giới hạn tùy chọn phổ thông.</small></>}
        </fieldset>;
      });
      return group === 'advanced' ? <AdvancedOptions key={group} error={error}>{fields}</AdvancedOptions> : <section key={group} className="option-common"><h3>Tùy chọn thường dùng</h3>{fields}</section>;
    })}
    <details className="advanced"><summary>Thông tin kỹ thuật</summary><pre>{JSON.stringify({ source: schema.source, schema_fingerprint: schema.schema_fingerprint, mapping_fingerprint: schema.mapping_fingerprint, ...(!client ? legacy : {}) }, null, 2)}</pre></details>
    <p className="bad" role="alert" hidden={!error}>{error}</p>
  </div>;
}
