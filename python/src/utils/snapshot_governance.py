"""Snapshot governance utilities for Stage 5.

Provides guard rails to ensure snapshot quality, security, and maintainability:
- Size limits to prevent snapshot bloat
- Secret/credential detection in metadata and fixtures
- Snapshot count warnings
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Dict, Any

# Guard rail constants
MAX_SNAPSHOT_SIZE_MB = 10  # Maximum size for a single snapshot file
MAX_SNAPSHOT_COUNT = 100  # Warning threshold for total snapshot count
MAX_FIXTURE_SIZE_MB = 50  # Maximum size for a single fixture file

# Common patterns for secrets (compiled for performance)
SECRET_PATTERNS = [
    re.compile(r'api[_-]?key["\']?\s*[:=]\s*["\']?[\w-]{20,}', re.IGNORECASE),
    re.compile(r'secret[_-]?key["\']?\s*[:=]\s*["\']?[\w-]{20,}', re.IGNORECASE),
    re.compile(r'password["\']?\s*[:=]\s*["\']?[\w!@#$%^&*()-+=]{8,}', re.IGNORECASE),
    re.compile(r'token["\']?\s*[:=]\s*["\']?[\w-]{20,}', re.IGNORECASE),
    re.compile(r'aws[_-]?access[_-]?key[_-]?id["\']?\s*[:=]\s*["\']?[A-Z0-9]{20}', re.IGNORECASE),
    re.compile(r'aws[_-]?secret[_-]?access[_-]?key["\']?\s*[:=]\s*["\']?[\w/+=]{40}', re.IGNORECASE),
    re.compile(r'bearer\s+[\w-]+\.[\w-]+\.[\w-]+', re.IGNORECASE),  # JWT tokens
    re.compile(r'-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----'),
]


def check_file_size(path: Path, max_size_mb: float = MAX_SNAPSHOT_SIZE_MB) -> Dict[str, Any]:
    """Check if file exceeds size limit.

    Args:
        path: Path to file to check.
        max_size_mb: Maximum allowed size in megabytes.

    Returns:
        Dict with 'pass', 'size_mb', and 'message' keys.
    """
    if not path.exists():
        return {"pass": True, "size_mb": 0, "message": "File does not exist"}

    size_bytes = path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)

    if size_mb > max_size_mb:
        return {
            "pass": False,
            "size_mb": round(size_mb, 2),
            "message": f"File size {size_mb:.2f}MB exceeds limit of {max_size_mb}MB"
        }

    return {
        "pass": True,
        "size_mb": round(size_mb, 2),
        "message": f"File size {size_mb:.2f}MB within limit"
    }


def scan_for_secrets(content: str, context_chars: int = 30) -> List[Dict[str, Any]]:
    """Scan text content for potential secrets or credentials.

    Args:
        content: Text content to scan.
        context_chars: Number of characters to include before/after match for context.

    Returns:
        List of dicts with 'pattern', 'match', 'line', and 'context' keys.
    """
    findings = []
    lines = content.split('\n')

    for line_num, line in enumerate(lines, 1):
        for pattern in SECRET_PATTERNS:
            match = pattern.search(line)
            if match:
                start = max(0, match.start() - context_chars)
                end = min(len(line), match.end() + context_chars)
                findings.append({
                    "pattern": pattern.pattern[:50] + "..." if len(pattern.pattern) > 50 else pattern.pattern,
                    "match": match.group(0)[:50] + "..." if len(match.group(0)) > 50 else match.group(0),
                    "line": line_num,
                    "context": line[start:end]
                })

    return findings


def check_snapshot_security(path: Path) -> Dict[str, Any]:
    """Check snapshot file for security issues.

    Args:
        path: Path to snapshot or metadata file.

    Returns:
        Dict with 'pass', 'findings', and 'message' keys.
    """
    if not path.exists():
        return {"pass": True, "findings": [], "message": "File does not exist"}

    try:
        content = path.read_text(encoding='utf-8')
    except Exception as e:
        return {"pass": False, "findings": [], "message": f"Failed to read file: {e}"}

    findings = scan_for_secrets(content)

    if findings:
        return {
            "pass": False,
            "findings": findings,
            "message": f"Found {len(findings)} potential secret(s) in file"
        }

    return {
        "pass": True,
        "findings": [],
        "message": "No secrets detected"
    }


def check_snapshot_directory(snapshot_dir: Path, max_count: int = MAX_SNAPSHOT_COUNT) -> Dict[str, Any]:
    """Check snapshot directory for governance compliance.

    Args:
        snapshot_dir: Path to snapshot directory.
        max_count: Warning threshold for snapshot count.

    Returns:
        Dict with 'pass', 'count', 'oversized', 'insecure', and 'message' keys.
    """
    if not snapshot_dir.exists():
        return {
            "pass": True,
            "count": 0,
            "oversized": [],
            "insecure": [],
            "message": "Snapshot directory does not exist"
        }

    snapshot_files = list(snapshot_dir.glob("*.csv"))
    metadata_files = list(snapshot_dir.glob("*.json"))
    all_files = snapshot_files + metadata_files

    oversized = []
    insecure = []

    for file in all_files:
        # Check size
        size_check = check_file_size(file, max_size_mb=MAX_SNAPSHOT_SIZE_MB)
        if not size_check["pass"]:
            oversized.append({"file": file.name, "size_mb": size_check["size_mb"]})

        # Check security
        security_check = check_snapshot_security(file)
        if not security_check["pass"]:
            insecure.append({
                "file": file.name,
                "findings_count": len(security_check["findings"]),
                "findings": security_check["findings"][:3]  # Limit to first 3
            })

    count = len(snapshot_files)
    messages = []

    if count > max_count:
        messages.append(f"Snapshot count {count} exceeds recommended limit of {max_count}")

    if oversized:
        messages.append(f"{len(oversized)} file(s) exceed size limits")

    if insecure:
        messages.append(f"{len(insecure)} file(s) contain potential secrets")

    passed = not oversized and not insecure and count <= max_count

    return {
        "pass": passed,
        "count": count,
        "oversized": oversized,
        "insecure": insecure,
        "message": "; ".join(messages) if messages else f"{count} snapshots, all within limits"
    }


__all__ = [
    "check_file_size",
    "scan_for_secrets",
    "check_snapshot_security",
    "check_snapshot_directory",
    "MAX_SNAPSHOT_SIZE_MB",
    "MAX_SNAPSHOT_COUNT",
    "MAX_FIXTURE_SIZE_MB",
]
