"""Installer checks never execute privileged installation commands."""
import subprocess
from pathlib import Path

from print_appliance import db
from print_appliance.auth import password_hash

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "install.sh"


def test_shell_syntax_and_help():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    result = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "fresh Debian 12" in result.stdout


def test_installer_bootstrap_is_fresh_only(tmp_path):
    script = SCRIPT.read_text()
    bootstrap = script.split("<<'BOOTSTRAP'\n", 1)[1].split("\nBOOTSTRAP", 1)[0]
    import sys
    env = {"PYTHONPATH": str(ROOT / "src")}
    args = [sys.executable, "-", str(tmp_path)]
    first = subprocess.run(args, input=bootstrap, text=True, capture_output=True, env=env)
    assert first.returncode == 0, first.stderr

    from print_appliance.config import Settings
    settings = Settings(data_dir=tmp_path)
    connection = db.connect(settings.database_path)
    row = connection.execute("SELECT salt,password_hash FROM admin").fetchone()
    connection.close()
    assert row["password_hash"] == password_hash("admin12345678", bytes.fromhex(row["salt"]))
    second = subprocess.run(args, input=bootstrap, text=True, capture_output=True, env=env)
    assert second.returncode != 0
    assert "refusing to reset" in second.stderr


def test_installation_guards_and_service_contract():
    script = SCRIPT.read_text()
    assert script.index('Already exists:') < script.index('apt-get update')
    assert script.index('Checkout has local changes') < script.index('apt-get update')
    assert '--system-site-packages' in script
    assert '--active --frozen --no-dev --no-editable' in script
    assert 'systemctl enable print-appliance' in script
    assert '\nsystemctl start print-appliance' not in script
    assert 'rm -rf' not in script
    assert 'Change it immediately' in script
