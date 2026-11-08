"""Static guardrails for the production Docker deployment (deploy/ibkr-docker).

These run in CI (.github/workflows/deploy-guardrails.yml) and locally with
`python -m pytest tests/deploy -q`. They protect invariants that a well-meaning
edit could silently break: paper-only trading, no public ports, no inline
secrets, pinned gateway image, and the deploy harness/skill staying in sync.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy" / "ibkr-docker"
COMPOSE_FILE = DEPLOY / "docker-compose.paper.yml"
DEPLOY_SH = DEPLOY / "deploy.sh"
SKILL_DIR = REPO / ".claude" / "skills" / "deploy-trading-bot"
SKILL_MD = SKILL_DIR / "SKILL.md"

SECRET_NAME = re.compile(r"(KEY|SECRET|PASSWORD|TOKEN|WEBHOOK)", re.IGNORECASE)
ALLOWED_INLINE = {"TWS_PASSWORD_FILE"}


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def gateway(compose) -> dict:
    return compose["services"]["ib-gateway"]


@pytest.fixture(scope="module")
def bot(compose) -> dict:
    return compose["services"]["trading-bot"]


def _env(service: dict) -> dict:
    env = service.get("environment", {})
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    return {k: str(v) for k, v in env.items()}


def test_project_name_is_stable(compose):
    assert compose["name"] == "strategy-lab"


def test_only_expected_services(compose):
    assert set(compose["services"]) == {"ib-gateway", "trading-bot"}


def test_gateway_image_pinned_by_tag_and_digest(gateway):
    assert re.fullmatch(
        r"ghcr\.io/gnzsnz/ib-gateway:[\w.-]+@sha256:[0-9a-f]{64}", gateway["image"]
    ), "bump the gateway deliberately: tag AND digest (README 'Upgrading IB Gateway')"


def test_paper_trading_only(gateway, bot):
    assert _env(gateway)["TRADING_MODE"] == "paper"
    assert _env(bot)["BROKER__MODE"] == "paper"


def test_gateway_not_published_to_host(gateway):
    assert "ports" not in gateway, "IB API must stay on the compose network only"
    assert "network_mode" not in gateway


def test_bot_ports_bound_to_loopback(bot):
    for port in bot.get("ports", []):
        assert str(port).startswith("127.0.0.1:"), f"public port binding: {port}"
    assert "network_mode" not in bot


def test_bot_talks_to_gateway_over_compose_network(bot):
    env = _env(bot)
    assert env["BROKER__IB_HOST"] == "ib-gateway"
    assert env["BROKER__IB_PORT"] == "4004"


def test_bot_waits_for_healthy_gateway(bot):
    assert bot["depends_on"]["ib-gateway"]["condition"] == "service_healthy"


def test_gateway_healthcheck_probes_paper_api(gateway):
    assert "4002" in " ".join(gateway["healthcheck"]["test"])


@pytest.mark.parametrize("service", ["ib-gateway", "trading-bot"])
def test_restart_policy(compose, service):
    assert compose["services"][service]["restart"] == "unless-stopped"


@pytest.mark.parametrize("service", ["ib-gateway", "trading-bot"])
def test_no_inline_secrets(compose, service):
    leaked = [k for k in _env(compose["services"][service]) if SECRET_NAME.search(k) and k not in ALLOWED_INLINE]
    assert not leaked, f"secret-like variables must live in secrets/*.env: {leaked}"


@pytest.mark.parametrize("service", ["ib-gateway", "trading-bot"])
def test_env_files_are_required_secrets(compose, service):
    entries = compose["services"][service]["env_file"]
    assert entries
    for entry in entries:
        assert isinstance(entry, dict) and entry.get("required") is True
        assert entry["path"].startswith("./secrets/")


def test_password_is_a_file_secret(compose):
    assert compose["secrets"]["tws_password"]["file"] == "./secrets/tws_password"


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


@pytest.mark.parametrize(
    "path",
    [
        "deploy/ibkr-docker/secrets/trading-bot.env",
        "deploy/ibkr-docker/secrets/ib-gateway.env",
        "deploy/ibkr-docker/secrets/tws_password",
        "deploy/ibkr-docker/data/trading-bot/x.db",
        "deploy/ibkr-docker/logs/trading-bot/x.log",
        "deploy/ibkr-docker/state/tws_settings/x",
        "deploy/ibkr-docker/deploys.log",
    ],
)
def test_runtime_files_are_gitignored(path):
    assert _git("check-ignore", "-q", path).returncode == 0, f"{path} must be gitignored"


def test_secret_examples_are_tracked():
    tracked = _git("ls-files", "deploy/ibkr-docker/secrets").stdout.split()
    assert tracked and all(p.endswith(".example") for p in tracked)


def test_shell_scripts_executable_and_lf():
    listing = _git("ls-files", "-s", "deploy/ibkr-docker").stdout.splitlines()
    scripts = [line for line in listing if line.endswith(".sh")]
    assert any(line.endswith("deploy/ibkr-docker/deploy.sh") for line in scripts)
    for line in scripts:
        mode, _, _, path = line.split(maxsplit=3)
        assert mode == "100755", f"{path} must be committed executable"
        assert b"\r\n" not in (REPO / path).read_bytes(), f"{path} has CRLF line endings"


def _deploy_actions() -> set[str]:
    text = DEPLOY_SH.read_text(encoding="utf-8")
    match = re.search(r"^\s*(status\|[\w|-]+)\)", text, re.MULTILINE)
    assert match, "could not find the action list in deploy.sh"
    return set(match.group(1).split("|"))


def test_deploy_actions_documented_in_skill():
    skill = SKILL_MD.read_text(encoding="utf-8")
    actions = _deploy_actions()
    assert actions == {"status", "check", "bot", "gateway", "rollback-bot"}
    for action in actions:
        assert f"deploy.sh {action}" in skill, f"SKILL.md does not document 'deploy.sh {action}'"


def test_skill_frontmatter():
    text = SKILL_MD.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "SKILL.md needs YAML frontmatter"
    meta = yaml.safe_load(match.group(1))
    assert meta["name"] == SKILL_DIR.name
    assert len(meta["description"]) <= 1024
    assert "deploy" in meta["description"].lower()


def test_deploy_script_guards():
    text = DEPLOY_SH.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert "EUID -ne 0" in text, "deploy.sh must refuse to run as root"
    assert "git pull -q --ff-only origin main" in text
    assert "--no-deps" in text, "bot redeploys must not recreate the gateway"
    assert "in_market_hours" in text


@pytest.mark.parametrize(
    "doc",
    ["deploy/ibkr-docker/README.md", "CLAUDE.md", ".vscode/copilot-instructions.md"],
)
def test_docs_point_to_harness(doc):
    text = (REPO / doc).read_text(encoding="utf-8")
    assert "deploy.sh" in text or "deploy-trading-bot" in text
