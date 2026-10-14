import React, { useState } from 'react';
import { Button, FieldLabel, Input } from './shared.jsx';

const empty = { current_password: '', new_password: '', confirm_password: '' };
const fields = [
  ['current_password', 'Mật khẩu hiện tại', 'current-password'],
  ['new_password', 'Mật khẩu mới', 'new-password'],
  ['confirm_password', 'Nhập lại mật khẩu mới', 'new-password'],
];
const messages = {
  'Current password is incorrect': 'Mật khẩu hiện tại không đúng. Vui lòng nhập lại.',
  'Password change temporarily locked; try again later': 'Đổi mật khẩu tạm khóa sau nhiều lần nhập sai. Hãy thử lại sau 5 phút.',
  'New password must contain 12 to 1024 characters': 'Mật khẩu mới phải có từ 12 đến 1024 ký tự.',
  'New password confirmation does not match': 'Hai lần nhập mật khẩu mới không khớp.',
  'New password must differ from the current password': 'Mật khẩu mới phải khác mật khẩu hiện tại.',
};
export function ChangePassword({ run, save }) {
  const [values, setValues] = useState(empty), [error, setError] = useState('');
  return <section className="change-password" aria-labelledby="change-password-title"><h3 id="change-password-title">Đổi mật khẩu quản trị</h3>
    <p className="muted">Mật khẩu mới cần ít nhất 12 ký tự. Sau khi đổi, tất cả phiên quản trị sẽ đăng xuất; API key của client và lệnh in không thay đổi.</p>
    <p className="muted">Chỉ thực hiện trên mạng nội bộ đáng tin cậy. HTTP không mã hóa mật khẩu; nên dùng TLS.</p>
    <form id="change-password-form" className="settings-grid" onSubmit={event => {
      event.preventDefault();
      if (values.new_password !== values.confirm_password) { setError(messages['New password confirmation does not match']); return; }
      if (values.new_password === values.current_password) { setError(messages['New password must differ from the current password']); return; }
      // Snapshot before global busy disables inputs; never persist or auto-retry secrets.
      const payload = { ...values };
      run(async () => {
        setValues(empty); setError('');
        try { await save(payload); }
        catch (failure) {
          if (failure.stale) return;
          if (failure.status === 401) throw failure;
          setError(messages[failure.message] || 'Không xác nhận được việc đổi mật khẩu. Không tự gửi lại. Hãy đăng xuất và thử đăng nhập bằng mật khẩu mới; nếu chưa đổi, dùng mật khẩu cũ.');
        }
      });
    }} onReset={event => { event.preventDefault(); setValues(empty); setError(''); }}>
      {fields.map(([name, label, autocomplete]) => <div className="setting-field" key={name}>
        <FieldLabel htmlFor={`change-${name}`}>{label}</FieldLabel>
        <Input id={`change-${name}`} name={name} type="password" autoComplete={autocomplete} required minLength={name === 'current_password' ? 1 : 12} maxLength={1024}
          value={values[name]} aria-describedby={error ? 'change-password-error' : undefined} onChange={event => { setValues({ ...values, [name]: event.target.value }); setError(''); }} />
      </div>)}
      <Button type="submit">Đổi mật khẩu</Button><Button type="reset" className="secondary">Xóa nội dung</Button>
    </form>
    {error && <p id="change-password-error" className="bad" role="alert">{error}</p>}
  </section>;
}
