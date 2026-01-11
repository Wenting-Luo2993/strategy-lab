#!/usr/bin/env python3
"""
Verify Environment Variables Setup
===================================

This script verifies that the environment variables configuration is set up correctly.
Run this after reading ENVIRONMENT_VARIABLES_SETUP.md to ensure everything is working.

Usage:
    python verify_env_setup.py

Expected Output:
    ✅ All configuration checks passed!
"""

import os
import sys
from pathlib import Path


def check_env_file():
    """Check if .env file exists in project root."""
    print("1️⃣  Checking for .env file...")

    # Check project root
    project_root = Path(__file__).parent.parent.parent
    env_file = project_root / ".env"

    if env_file.exists():
        print("   ✅ .env file found at:", env_file)
        return True
    else:
        print("   ⚠️  .env file NOT found")
        print("   📝 To create it:")
        print(f"      cp {project_root / '.env.template'} {env_file}")
        return False


def check_env_vars():
    """Check if key environment variables are set."""
    print("\n2️⃣  Checking environment variables...")

    critical_vars = {
        "FINNHUB_API_KEY": "Finnhub API key (for WebSocket)",
        "GOOGLE_SERVICE_ACCOUNT_KEY": "Google service account credentials",
        "GOOGLE_DRIVE_ROOT_FOLDER_ID": "Google Drive folder ID",
    }

    optional_vars = {
        "FINNHUB_SYMBOLS": "Trading symbols (default: AAPL,MSFT)",
        "FINNHUB_BAR_INTERVAL": "Bar interval (default: 5m)",
        "LOG_LEVEL": "Logging level (default: INFO)",
        "ENVIRONMENT": "Environment type (default: production)",
    }

    found_critical = {}
    found_optional = {}

    print("\n   🔴 CRITICAL Variables:")
    for var, description in critical_vars.items():
        value = os.getenv(var)
        if value:
            # Mask sensitive values
            if "KEY" in var or "SECRET" in var:
                masked = value[:4] + "*" * max(0, len(value) - 8) + value[-4:] if len(value) > 8 else "***"
            else:
                masked = value
            print(f"   ✅ {var:40} = {masked}")
            found_critical[var] = True
        else:
            print(f"   ❌ {var:40} - NOT SET")
            print(f"      └─ {description}")

    print("\n   🟡 OPTIONAL Variables:")
    for var, description in optional_vars.items():
        value = os.getenv(var)
        if value:
            print(f"   ✅ {var:40} = {value}")
            found_optional[var] = True
        else:
            print(f"   ⚠️  {var:40} - using default")
            print(f"      └─ {description}")

    critical_ok = all(found_critical.values())
    return critical_ok, found_optional


def check_config_files():
    """Check if config files exist."""
    print("\n3️⃣  Checking configuration files...")

    python_dir = Path(__file__).parent.parent

    files_to_check = [
        (python_dir / "src" / "config" / "finnhub_config.example.json",
         "Example Finnhub config (for reference)"),
        (python_dir / "src" / "config" / "finnhub_config_loader.py",
         "Updated config loader with env var support"),
        (Path(__file__).parent.parent.parent / ".env.template",
         "Environment variables template"),
    ]

    all_ok = True
    for file_path, description in files_to_check:
        if file_path.exists():
            print(f"   ✅ {file_path.relative_to(Path(__file__).parent.parent.parent)}")
            print(f"      └─ {description}")
        else:
            print(f"   ❌ {file_path.relative_to(Path(__file__).parent.parent.parent)} - NOT FOUND")
            all_ok = False

    return all_ok


def check_config_loading():
    """Test if config can be loaded."""
    print("\n4️⃣  Testing configuration loading...")

    try:
        from src.config.finnhub_config_loader import load_finnhub_config

        # Try loading config
        config = load_finnhub_config()

        print("   ✅ Configuration loaded successfully!")
        print(f"      └─ API Key: {'*' * len(config.api_key) if config.api_key else 'NOT SET'}")
        print(f"      └─ Symbols: {', '.join(config.symbols)}")
        print(f"      └─ Bar Interval: {config.bar_interval}")
        return True

    except FileNotFoundError as e:
        print(f"   ⚠️  Config file not found (this is OK if using env vars)")
        print(f"      └─ {e}")

        # Check if we have env vars instead
        if os.getenv("FINNHUB_API_KEY"):
            print("      └─ But FINNHUB_API_KEY is set in env vars ✅")
            return True
        return False

    except ValueError as e:
        print(f"   ❌ Configuration error: {e}")
        return False

    except Exception as e:
        print(f"   ⚠️  Error loading config: {e}")
        return False


def print_next_steps(critical_ok, env_vars_ok, config_ok):
    """Print next steps based on check results."""
    print("\n" + "=" * 70)
    print("📋 NEXT STEPS")
    print("=" * 70)

    if not critical_ok:
        print("\n🔴 CRITICAL - You must do this:")
        print("   1. Copy .env.template to .env")
        print("   2. Edit .env and add your:")
        print("      - FINNHUB_API_KEY (get from https://finnhub.io/register)")
        print("      - GOOGLE_SERVICE_ACCOUNT_KEY (path to Google service account JSON)")
        print("      - GOOGLE_DRIVE_ROOT_FOLDER_ID (your Google Drive folder ID)")
        print("\n   Then run this script again to verify ✅")
        return False

    if config_ok:
        print("\n✅ Configuration is ready!")
        print("\n📚 Next steps:")
        print("   1. Read: ENVIRONMENT_VARIABLES_SETUP.md")
        print("   2. Test: python scripts/test_finnhub_config.py")
        print("   3. Deploy: Follow ORACLE_CLOUD_DEPLOYMENT.md")
        return True
    else:
        print("\n⚠️  Config loading had issues - check above for details")
        print("   Try: python scripts/test_finnhub_config.py")
        return False


def main():
    """Run all verification checks."""
    print("\n" + "=" * 70)
    print("🔐 ENVIRONMENT VARIABLES VERIFICATION")
    print("=" * 70)

    env_file_ok = check_env_file()
    critical_ok, optional_vars = check_env_vars()
    files_ok = check_config_files()
    config_ok = check_config_loading()

    print("\n" + "=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)
    print(f"   .env file:              {'✅' if env_file_ok else '⚠️'}")
    print(f"   Critical env vars:      {'✅' if critical_ok else '❌'}")
    print(f"   Optional env vars:      {'✅' if optional_vars else '⚠️'}")
    print(f"   Config files:           {'✅' if files_ok else '❌'}")
    print(f"   Config loading:         {'✅' if config_ok else '⚠️'}")

    success = print_next_steps(critical_ok, optional_vars, config_ok)

    print("\n" + "=" * 70)
    if success:
        print("✅ All checks passed! You're ready to go!")
    else:
        print("❌ Some checks failed - see above for details")
    print("=" * 70 + "\n")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
