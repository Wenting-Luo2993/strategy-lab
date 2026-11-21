"""Socket audit behavior without connecting to Docker or IBKR."""

import importlib.util
import json
import logging
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "ibkr-docker" / "monitor_gateway_connections.py"
spec = importlib.util.spec_from_file_location("gateway_monitor", SCRIPT)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def row(local, peer, state="01"):
    return f"0: {local} {peer} {state} 0:0 0:0 0 1000 0 12345"


def unit_metadata():
    return "\n\n".join(
        f"Id={unit}\nLoadState=loaded\nActiveState=inactive\nUnitFileState=disabled"
        for unit in monitor.LEGACY_UNITS
    )


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
            if args[2] == monitor.BOT:
                return "PID PPID COMMAND\n30 1 python\n"
            return "PID PPID COMMAND\n10 1 java\n"
        if args[:2] == ("systemctl", "show"):
            return unit_metadata().replace(
                "Id=trading-bot-phase2-start.timer\nLoadState=loaded\nActiveState=inactive",
                "Id=trading-bot-phase2-start.timer\nLoadState=loaded\nActiveState=active",
            )
        assert args == ("ps", "-eo", "pid,ppid,comm")
        return (
            f"PID PPID COMMAND\n10 1 java\n20 1 java\n30 1 python\n40 1 python3\n"
            f"{monitor.os.getpid()} 1 python3\n"
        )

    monkeypatch.setattr(monitor, "run", run)
    monkeypatch.setattr(monitor, "host_counters", lambda: {"MemAvailable_KiB": 100000})
    sample = monitor.snapshot()
    assert sample["bot_socket_count"] == 2
    assert len(sample["warnings"]) == 5
    assert {row["pid"] for row in sample["unmanaged_candidate_processes"]} == {"20", "40"}
    assert any("Legacy activation path" in warning for warning in sample["warnings"])
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


def counters(cpu, swap_in=0, swap_out=0, boot="boot-a"):
    return {
        "boot_id": boot, "cpu_jiffies_since_boot": cpu,
        "pswpin_pages_since_boot": swap_in, "pswpout_pages_since_boot": swap_out,
    }


def test_interval_distinguishes_host_pressure_and_invalid_counter_baselines():
    before = counters([100] * 10, 100, 100)
    after = counters([110, 100, 110, 120, 130, 100, 100, 130, 500, 500], 140, 120)
    delta = monitor.counter_interval(before, after, 2)
    assert delta == {
        "available": True, "seconds": 2,
        "cpu_iowait_percent": 30, "cpu_steal_percent": 30,
        "swap_in_pages_per_second": 20, "swap_out_pages_per_second": 10,
    }
    assert not monitor.counter_interval(after, before, 2)["available"]
    assert not monitor.counter_interval(before, {**after, "boot_id": "boot-b"}, 2)["available"]


def test_incomplete_legacy_inventory_is_not_treated_as_disabled(monkeypatch):
    monkeypatch.setattr(monitor, "run", lambda *args: "")
    with pytest.raises(ValueError, match="Incomplete legacy unit"):
        monitor.legacy_units()


def test_recording_watch_preserves_failed_sample_then_recovers(monkeypatch, tmp_path, capsys):
    path = tmp_path / "logs" / "audit.jsonl"
    monkeypatch.setattr(monitor, "AUDIT_LOG", path)
    monkeypatch.setattr("sys.argv", [str(SCRIPT), "--watch", "--record"])
    calls = 0

    def snapshot():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired("docker", 15)
        return {"timestamp": str(calls), "host_counters": counters([calls * 100] * 10)}

    def sleep(_seconds):
        if calls == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(monitor, "snapshot", snapshot)
    monkeypatch.setattr(monitor.time, "sleep", sleep)
    times = iter([1.0, 61.0, 121.0])
    monkeypatch.setattr(monitor.time, "monotonic", lambda: next(times))
    assert monitor.main() == 0
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records[0]["audit_error"] == "TimeoutExpired"
    assert "host_interval" not in records[1]
    assert records[2]["host_interval"]["seconds"] == 60
    assert "TimeoutExpired" in capsys.readouterr().err


def test_audit_recording_rotates_and_does_not_swallow_write_failures(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    handler = monitor.AuditLogHandler(path, maxBytes=150, backupCount=3)
    try:
        for index in range(10):
            payload = json.dumps({"index": index, "padding": "x" * 90})
            handler.emit(logging.LogRecord("audit", logging.INFO, "", 0, payload, (), None))
        files = list(tmp_path.iterdir())
        assert len(files) == 4
        assert all(file.stat().st_size <= 150 for file in files)
        assert json.loads(path.read_text())["index"] == 9

        def fail(_record):
            raise OSError("disk full")

        monkeypatch.setattr(handler, "shouldRollover", fail)
        with pytest.raises(OSError, match="disk full"):
            handler.emit(logging.LogRecord("audit", logging.INFO, "", 0, "test", (), None))
    finally:
        handler.close()
