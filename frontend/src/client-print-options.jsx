import React from 'react';
import { Select } from './shared.jsx';

const labels = { paper_size: 'Khổ giấy', sides: 'Một / hai mặt', color_mode: 'Màu', scaling: 'Tỷ lệ PDF' };
const values = { 'one-sided': 'Một mặt', 'two-sided-long-edge': 'Hai mặt cạnh dài', 'two-sided-short-edge': 'Hai mặt cạnh ngắn', monochrome: 'Trắng đen', color: 'Màu', auto: 'Tự động', 'auto-fit': 'Tự động vừa trang', fit: 'Vừa vùng in', fill: 'Lấp đầy (có thể cắt mép)', none: 'Giữ nguyên tỷ lệ' };
export const initialOptions = () => ({ values: {} });
export const usableSchema = schema => ['available', 'partial'].includes(schema?.availability);
export const optionsPayload = (schema, model, config) => ({ options: config.format === 'zpl' ? {} : { ...model.values } });
export const optionsError = () => '';
export function PrintOptions({ schema, model, onChange, format }) {
  if (format === 'zpl') return <p>ZPL gửi nguyên bản, không áp dụng tùy chọn PDF.</p>;
  if (!usableSchema(schema)) return <p className="bad">{schema?.reason || 'Chưa đọc được tùy chọn máy. Server sẽ kiểm tra khi nhận lệnh.'}</p>;
  return <div className="option-editor"><p className="muted">Bỏ trống để dùng mặc định. Server kiểm tra tổ hợp lựa chọn khi nhận lệnh.</p>
    {Object.entries(labels).map(([name, label]) => (schema.supported?.[name] || []).length > 0 && <label key={name}>{label}<Select data-option-name={name} value={model.values[name] || ''} onChange={event => {
      const next = { ...model.values }; if (event.target.value) next[name] = event.target.value; else delete next[name]; onChange({ values: next });
    }}><option value="">Dùng mặc định{schema.defaults?.[name] ? ` (${values[schema.defaults[name]] || schema.defaults[name]})` : ''}</option>
      {schema.supported[name].map(value => <option key={value} value={value}>{values[value] || value}</option>)}
    </Select></label>)}
  </div>;
}
