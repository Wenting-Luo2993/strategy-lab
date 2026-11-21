"""Passive Gateway audit with opt-in recording; no broker login or session changes."""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

GATEWAY = "strategy-lab-ib-gateway"
BOT = "strategy-lab-trading-bot"
LEGACY_UNITS = (
    "trading-bot-phase2.service", "trading-bot-phase2-start.service",
    "ibc-gateway.service", "xvfb-ibkr.service", "x11vnc-ibkr.service",
    "trading-bot-phase2-start.timer",
)
UNIT_PROPERTIES = (
    "Id", "LoadState", "ActiveState", "SubState", "UnitFileState", "MainPID",
    "ActiveEnterTimestamp", "InactiveEnterTimestamp", "LastTriggerUSec",
)
AUDIT_LOG = Path(__file__).resolve().parent / "logs" / "gateway-connections.jsonl"


def run(*args: str) -> str:
    return subprocess.run(
        args, check=True, capture_output=True, text=True, timeout=15,
    ).stdout


def endpoint(value: str) -> tuple[str, int]:
    address, port = value.split(":")
    raw = bytes.fromhex(address)
    if len(raw) not in (4, 16):
        raise ValueError("Invalid proc TCP address length")
    raw = b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
    ip = ipaddress.ip_address(raw)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return str(ip), int(port, 16)


def classify_sockets(text: str, bot_addresses: set[str]) -> list[dict]:
    sockets = []
    for line in text.splitlines():
        columns = line.split()
        if not columns or columns[0] == "sl":
            continue
        if len(columns) < 10:
            raise ValueError("Malformed proc TCP row")
        local_ip, local_port = endpoint(columns[1])
        if local_port not in (4002, 4004) or columns[3] != "01":
            continue
        peer_ip, peer_port = endpoint(columns[2])
        loopback = ipaddress.ip_address(peer_ip).is_loopback
        if local_port == 4004 and peer_ip in bot_addresses:
            classification = "expected_bot"
        elif loopback:
            classification = "local_probe_or_relay"
        else:
            classification = "unexpected_peer"
        sockets.append({
            "local": f"{local_ip}:{local_port}",
            "peer": f"{peer_ip}:{peer_port}",
            "classification": classification,
            "inode": columns[9],
        })
    return sorted(sockets, key=lambda item: (item["local"], item["peer"]))


def host_counters() -> dict:
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        fields = line.split()
        if fields[0] in {"MemAvailable:", "SwapTotal:", "SwapFree:"}:
            memory[fields[0].rstrip(":") + "_KiB"] = int(fields[1])
    swap = {}
    for line in Path("/proc/vmstat").read_text().splitlines():
        key, value = line.split()
        if key in {"pswpin", "pswpout"}:
            swap[key + "_pages_since_boot"] = int(value)
    cpu = Path("/proc/stat").read_text().splitlines()[0].split()
    pressure = {}
    for resource in ("cpu", "memory", "io"):
        path = Path("/proc/pressure") / resource
        if not path.exists():
            pressure[resource] = {"available": False}
            continue
        pressure[resource] = {
            fields[0]: dict(item.split("=", 1) for item in fields[1:])
            for fields in (line.split() for line in path.read_text().splitlines())
        }
    return {
        **memory, **swap,
        "cpu_jiffies_since_boot": [int(value) for value in cpu[1:]],
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "pressure": pressure,
    }


def counter_interval(previous: dict, current: dict, seconds: float) -> dict:
    if seconds <= 0 or previous["boot_id"] != current["boot_id"]:
        return {"available": False, "reason": "boot_changed_or_invalid_interval"}
    # Guest time is already counted in user/nice; exclude it from the total.
    cpu = [
        new - old for old, new in zip(
            previous["cpu_jiffies_since_boot"][:8],
            current["cpu_jiffies_since_boot"][:8],
        )
    ]
    swap_in = current["pswpin_pages_since_boot"] - previous["pswpin_pages_since_boot"]
    swap_out = current["pswpout_pages_since_boot"] - previous["pswpout_pages_since_boot"]
    if len(cpu) != 8 or min(*cpu, swap_in, swap_out) < 0 or sum(cpu) == 0:
        return {"available": False, "reason": "counters_reset_or_not_advanced"}
    return {
        "available": True,
        "seconds": round(seconds, 3),
        "cpu_iowait_percent": round(cpu[4] * 100 / sum(cpu), 2),
        "cpu_steal_percent": round(cpu[7] * 100 / sum(cpu), 2),
        "swap_in_pages_per_second": round(swap_in / seconds, 2),
        "swap_out_pages_per_second": round(swap_out / seconds, 2),
    }


def legacy_units() -> list[dict]:
    text = run(
        "systemctl", "show", *LEGACY_UNITS,
        "--property=" + ",".join(UNIT_PROPERTIES), "--no-pager",
    )
    units = [
        dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        for block in text.strip().split("\n\n")
    ]
    if {unit.get("Id") for unit in units} != set(LEGACY_UNITS):
        raise ValueError("Incomplete legacy unit metadata")
    return units


def process_rows(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines()[1:]:
        pid, parent, name = line.split(maxsplit=2)
        rows.append({"pid": pid, "ppid": parent, "name": name})
    return rows


def snapshot() -> dict:
    gateway_networks = json.loads(run(
        "docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", GATEWAY,
    ))
    bot_networks = json.loads(run(
        "docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", BOT,
    ))
    shared = set(gateway_networks) & set(bot_networks)
    if not shared:
        raise ValueError("Gateway and bot have no shared Docker network")
    bot_addresses = {
        str(ipaddress.ip_address(values[key]))
        for network, values in bot_networks.items() if network in shared
        for key in ("IPAddress", "GlobalIPv6Address") if values.get(key)
    }
    if not bot_addresses:
        raise ValueError("Cannot establish expected bot network addresses")
    tcp = run(
        "docker", "exec", GATEWAY, "bash", "-c",
        "cat /proc/net/tcp && cat /proc/net/tcp6",
    )
    processes = run("docker", "top", GATEWAY, "-eo", "pid,ppid,comm")
    bot_processes = run("docker", "top", BOT, "-eo", "pid,ppid,comm")
    host_processes = run("ps", "-eo", "pid,ppid,comm")
    owned_pids = {
        row["pid"] for row in process_rows(processes) + process_rows(bot_processes)
    } | {str(os.getpid())}
    unmanaged = [
        row for row in process_rows(host_processes)
        if row["pid"] not in owned_pids and (
            row["name"].startswith("python") or row["name"] in {
                "java", "Xvfb", "ibcstart.sh", "socat", "run_socat.sh",
            }
        )
    ]
    gateway_jvms = [
        line.split()[0] for line in processes.splitlines()[1:]
        if len(line.split()) == 3 and line.split()[2] == "java"
    ]
    host_jvms = [
        line.split()[0] for line in host_processes.splitlines()[1:]
        if len(line.split()) == 3 and line.split()[2] == "java"
    ]
    sockets = classify_sockets(tcp, bot_addresses)
    warnings = []
    units = legacy_units()
    for unit in units:
        if (
            unit.get("ActiveState") not in {"inactive", "failed"}
            or unit.get("UnitFileState", "").startswith("enabled")
        ):
            warnings.append(f"Legacy activation path present: {unit['Id']}")
    if unmanaged:
        warnings.append(
            "Unmanaged candidate processes observed; names alone do not prove an IB login"
        )
    if any(item["classification"] == "unexpected_peer" for item in sockets):
        warnings.append("Unexpected Gateway API peer observed")
    bot_socket_count = sum(item["classification"] == "expected_bot" for item in sockets)
    if bot_socket_count > 1:
        warnings.append("Multiple bot API sockets; client IDs cannot be verified by TCP")
    if len(gateway_jvms) != 1:
        warnings.append("Expected exactly one Gateway JVM")
    if set(host_jvms) - set(gateway_jvms):
        warnings.append("Additional host JVM observed; identify it before assuming an IB login")
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gateway_jvm_pids": gateway_jvms,
        "host_jvm_pids": host_jvms,
        "unmanaged_candidate_processes": unmanaged,
        "legacy_units": units,
        "bot_socket_count": bot_socket_count,
        "sockets": sockets,
        "warnings": warnings,
        "host_counters": host_counters(),
        "scope": "TCP snapshot only; not an IB client-ID or broker-session audit",
    }


class AuditLogHandler(RotatingFileHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        # Logging normally suppresses write failures; evidence loss must be fatal.
        raise

    def _open(self):
        return os.fdopen(
            os.open(self.baseFilename, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600),
            "a", encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument(
        "--record", action="store_true",
        help="With --watch, retain metadata in logs/gateway-connections.jsonl (4 x 5 MiB)",
    )
    args = parser.parse_args()
    if args.record and not args.watch:
        parser.error("--record requires --watch")
    recorder = None
    previous = None
    try:
        if args.record:
            AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
            recorder = AuditLogHandler(
                AUDIT_LOG, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
            )
        while True:
            try:
                sample = snapshot()
            except (subprocess.SubprocessError, OSError, ValueError) as exc:
                print(f"Gateway audit failed ({type(exc).__name__}): {exc}", file=sys.stderr)
                sample = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "audit_error": type(exc).__name__,
                    "message": str(exc),
                }
                previous = None
                if not args.watch:
                    return 1
            else:
                sampled_at = time.monotonic()
                if previous is not None:
                    counters, timestamp = previous
                    sample["host_interval"] = counter_interval(
                        counters, sample["host_counters"], sampled_at - timestamp,
                    )
                previous = (sample["host_counters"], sampled_at)
            payload = json.dumps(sample, sort_keys=True)
            if recorder is not None:
                recorder.emit(logging.LogRecord(
                    "gateway_audit", logging.INFO, __file__, 0, payload, (), None,
                ))
            print(payload, flush=True)
            if not args.watch:
                return 0
            time.sleep(60)
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        print(f"Gateway audit failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if recorder is not None:
            recorder.close()


if __name__ == "__main__":
    sys.exit(main())
