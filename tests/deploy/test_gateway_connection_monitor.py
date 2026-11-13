"""Socket audit behavior without connecting to Docker or IBKR."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "ibkr-docker" / "monitor_gateway_connections.py"
spec = importlib.util.spec_from_file_location("gateway_monitor", SCRIPT)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def row(local, peer, state="01"):
    return f"0: {local} {peer} {state} 0:0 0:0 0 1000 0 12345"


def test_expected_bot_relay_and_unexpected_peer_are_distinguished():
    tcp = "\n".join([
        "sl local_address rem_address st",
        row("020012AC:0FA4", "030012AC:BEEF"),
        row("0100007F:0FA2", "0100007F:ABCD"),
        row("020012AC:0FA4", "040012AC:BEEF"),
        row("00000000:0FA4", "00000000:0000", state="0A"),
        row("020012AC:01BB", "040012AC:BEEF"),
    ])
    sockets = monitor.classify_sockets(tcp, {"172.18.0.3"})
    assert len(sockets) == 3
    assert {item["classification"] for item in sockets} == {
        "expected_bot", "local_probe_or_relay", "unexpected_peer",
    }


def test_ipv6_and_mapped_ipv4_peers_are_decoded():
    assert monitor.endpoint("00000000000000000000000001000000:0FA2") == ("::1", 4002)
    assert monitor.endpoint("0000000000000000FFFF0000030012AC:0FA4") == ("172.18.0.3", 4004)
    with pytest.raises(ValueError):
        monitor.classify_sockets("not a valid socket row", set())


def test_snapshot_uses_only_metadata_and_flags_extra_connections_and_jvms(monkeypatch):
    commands = []

    def run(*args):
        commands.append(args)
        if args[:2] == ("docker", "inspect"):
            address = "172.18.0.3" if args[-1] == monitor.BOT else "172.18.0.2"
            return json.dumps({"strategy-lab_default": {"IPAddress": address}})
        if args[:2] == ("docker", "exec"):
            return "\n".join([
                row("020012AC:0FA4", "030012AC:BEEF"),
                row("020012AC:0FA4", "030012AC:BEF0"),
                row("020012AC:0FA4", "040012AC:BEEF"),
            ])
        if args[:2] == ("docker", "top"):
            return "PID PPID COMMAND\n10 1 java\n"
        assert args == ("ps", "-eo", "pid,ppid,comm")
        return "PID PPID COMMAND\n10 1 java\n20 1 java\n"

    monkeypatch.setattr(monitor, "run", run)
    monkeypatch.setattr(monitor, "host_counters", lambda: {"MemAvailable_KiB": 100000})
    sample = monitor.snapshot()
    assert sample["bot_socket_count"] == 2
    assert len(sample["warnings"]) == 3
    assert sample["host_counters"]["MemAvailable_KiB"] == 100000
    assert all("args" not in command and "connect" not in command for command in commands)


def test_failed_inspection_is_not_reported_as_no_unexpected_connections(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", [str(SCRIPT)])

    def fail():
        raise subprocess.TimeoutExpired("docker", 15)

    monkeypatch.setattr(monitor, "snapshot", fail)
    assert monitor.main() == 1
    output = capsys.readouterr()
    assert not output.out
    assert "Gateway audit failed (TimeoutExpired)" in output.err
