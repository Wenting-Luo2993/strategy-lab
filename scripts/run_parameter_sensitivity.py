#!/usr/bin/env python3
"""
Convenience wrapper for parameter sensitivity testing.

This script provides backward compatibility for the old location.
All logic has moved to vibe.backtester.analysis.sensitivity_runner
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vibe.backtester.analysis.sensitivity_runner import main

if __name__ == "__main__":
    main()
