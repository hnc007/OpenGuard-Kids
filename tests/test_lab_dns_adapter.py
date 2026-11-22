"""Run the real backup/restore workflow on disk with only a fake Windows network boundary."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell required for the DNS recovery script")
GUID = "12345678-aaaa-bbbb-cccc-123456789012"
STATIC_SERVERS = ["192.0.2.20", "192.0.2.30", "192.0.2.40"]
IPV6 = ["2001:db8::12", "2001:db8::13"]


class Lab:
    def __init__(self, tmp):
        self.tmp = tmp
        self.state_file = tmp / "adapter.json"
        self.backup_dir = tmp / "repo" / ".local" / "lab-dns"
        self.backup = self.backup_dir / f"{GUID}.json"
        scripts = tmp / "repo" / "scripts" / "lab"
        scripts.mkdir(parents=True)
        shutil.copyfile(ROOT / "scripts" / "lab" / "dns-adapter.ps1", scripts / "dns-adapter.ps1")
        shutil.copyfile(FIXTURES / "lab_dns_windows_fake.ps1", scripts / "dns-adapter-windows.ps1")
        self.runner = tmp / "runner.ps1"
        shutil.copyfile(FIXTURES / "run_lab_dns_mock.ps1", self.runner)
        self.write(dict(index=17, guid=GUID, name="Ethernet Lab (3)", admin=True, udp=True, tcp=True,
                        ambiguous=False, lease=["192.0.2.250"], ipv6=IPV6, writes=[], failures=[],
                        ipv4={"source": "static", "register": "both", "servers": STATIC_SERVERS}))

    @property
    def state(self):
        return json.loads(self.state_file.read_text(encoding="utf-8-sig"))

    def write(self, state):
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

    def run(self, action, *, phrase=None, failure=None):
        # RemoteSigned applies only to this process and still honours machine/user Group Policy.
        result = subprocess.run([POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive",
                                 "-ExecutionPolicy", "RemoteSigned", "-File", str(self.runner),
                                 "-Action", action, "-Confirmation", phrase or f"{action} 17"],
                                cwd=self.tmp, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=25, check=False)
        if failure:
            assert result.returncode != 0 and failure in result.stderr, result.stdout + result.stderr
        else:
            assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout


@pytest.fixture
def lab():
    # A local volume also works on Windows 5.1 hosts whose TEMP is redirected to
    # a network/redirection zone. Each run gets a new private mock repository.
    local = ROOT / ".local"
    local.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dns-test-", dir=local) as directory:
        yield Lab(Path(directory))


def test_preview_and_roundtrip_static_preserve_order_flags_ipv6_and_backup(lab):
    preview = json.loads(lab.run("Preview"))
    assert preview["IPv4Source"] == "static" and preview["IPv4Servers"] == ",".join(STATIC_SERVERS)
    assert preview["IPv6Servers"] == ",".join(IPV6)
    assert not lab.backup_dir.exists() and lab.state["writes"] == []

    lab.run("Apply")
    assert lab.state["ipv4"] == {"source": "static", "register": "both", "servers": ["127.0.0.1"]}
    saved = lab.backup.read_bytes()
    assert json.loads(saved)["ipv4"]["servers"] == STATIC_SERVERS
    lab.run("Apply", failure="Restore the existing backup first")
    assert lab.backup.read_bytes() == saved and len(lab.state["writes"]) == 1

    lab.run("Restore")
    assert lab.state["ipv4"]["servers"] == STATIC_SERVERS and lab.state["ipv4"]["register"] == "both"
    assert lab.state["ipv6"] == IPV6
    assert len(lab.state["writes"]) == 4
    archives = list(lab.backup_dir.glob("*.restored.json"))
    assert not lab.backup.exists() and len(archives) == 1 and archives[0].read_bytes() == saved


def test_dhcp_restore_requests_automatic_dns_even_after_lease_changes(lab):
    st = lab.state
    st["ipv4"] = {"source": "dhcp", "register": "none", "servers": ["192.0.2.10"]}
    lab.write(st)
    lab.run("Apply")
    assert json.loads(lab.backup.read_bytes())["ipv4"]["servers"] == ["192.0.2.10"]
    lab.run("Restore")
    st = lab.state
    assert st["ipv4"] == {"source": "dhcp", "register": "none", "servers": st["lease"]}
    assert st["writes"][-1].endswith("source=dhcp register=none validate=no")
    assert st["ipv6"] == IPV6


@pytest.mark.parametrize(("changes", "phrase", "message"), [
    ({"admin": False}, None, "Administrator"),
    ({}, "apply 17", "Cancelled"),
    ({"tcp": False}, None, "UDP and TCP"),
    ({"udp": False}, None, "UDP and TCP"),
    ({"ambiguous": True}, None, "Cannot reliably distinguish"),
])
def test_refuses_to_write_without_each_precondition(lab, changes, phrase, message):
    lab.write(lab.state | changes)
    lab.run("Apply", phrase=phrase, failure=message)
    assert lab.state["writes"] == [] and not lab.backup_dir.exists()


def test_apply_failure_after_partial_mutation_rolls_back_and_keeps_original(lab):
    original = lab.state["ipv4"]
    lab.write(lab.state | {"failures": ["after:set:static:127.0.0.1"]})
    lab.run("Apply", failure="IPv4 was rolled back")
    assert lab.state["ipv4"] == original and len(lab.state["writes"]) == 4
    assert json.loads(lab.backup.read_bytes())["ipv4"] == original
    assert not list(lab.backup_dir.glob("*.restored.json"))


def test_failed_automatic_rollback_and_failed_restore_remain_retryable(lab):
    lab.write(lab.state | {"failures": ["after:set:static:127.0.0.1", "before:add:192.0.2.30"]})
    lab.run("Apply", failure="Automatic rollback also failed")
    saved = lab.backup.read_bytes()
    assert json.loads(saved)["ipv4"]["servers"] == STATIC_SERVERS
    assert lab.state["ipv4"]["servers"] == STATIC_SERVERS[:1]

    lab.write(lab.state | {"failures": ["before:add:192.0.2.40"]})
    lab.run("Restore", failure="Injected failure")
    assert lab.state["ipv4"]["servers"] == STATIC_SERVERS[:2]
    assert lab.backup.read_bytes() == saved and not list(lab.backup_dir.glob("*.restored.json"))
    lab.run("Restore")
    assert lab.state["ipv4"]["servers"] == STATIC_SERVERS and lab.state["ipv6"] == IPV6
    assert not lab.backup.exists()


@pytest.mark.parametrize(("replace", "message"), [
    ({"computer": "OTHER-VM"}, "another adapter or computer"),
    ({"guid": "11111111-aaaa-bbbb-cccc-123456789012"}, "another adapter or computer"),
    ({"ipv4": {"source": "static", "register": "primary", "servers": ["2001:db8::2"]}}, "Invalid IPv4 address"),
])
def test_rejects_mismatched_or_invalid_backup_before_writing(lab, replace, message):
    lab.run("Apply")
    wrong = json.loads(lab.backup.read_bytes()) | replace
    lab.backup.write_text(json.dumps(wrong), encoding="utf-8")
    writes = list(lab.state["writes"])
    lab.run("Restore", failure=message)
    assert lab.state["writes"] == writes and lab.backup.exists()
