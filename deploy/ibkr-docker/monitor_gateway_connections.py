"""Passive Gateway socket audit. No broker login, packet capture or mutations."""

from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

GATEWAY = "strategy-lab-ib-gateway"
BOT = "strategy-lab-trading-bot"


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
    return {
        **memory, **swap,
        "cpu_jiffies_since_boot": [int(value) for value in cpu[1:]],
    }


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
    host_processes = run("ps", "-eo", "pid,ppid,comm")
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
        "bot_socket_count": bot_socket_count,
        "sockets": sockets,
        "warnings": warnings,
        "host_counters": host_counters(),
        "scope": "TCP snapshot only; not an IB client-ID or broker-session audit",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    try:
        while True:
            print(json.dumps(snapshot(), sort_keys=True), flush=True)
            if not args.watch:
                return 0
            time.sleep(60)
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        print(f"Gateway audit failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
