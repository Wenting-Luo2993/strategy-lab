# Market Hours Orchestrator

## Overview

The **Market Hours Orchestrator** is a daemon script that runs 24/7 and automatically manages execution of your trading scripts during US market hours (9:30 AM - 4:00 PM Eastern Time).

### Key Features

- **24/7 Operation**: Runs continuously and monitors market hours
- **Automatic Start**: Starts trading scripts 1 hour before market open (8:30 AM ET)
- **Automatic Stop**: Stops scripts after market close (4:00 PM ET)
- **Weekend/Holiday Handling**: Skips execution on non-trading days
- **Graceful Shutdown**: Safely terminates scripts with signal handling
- **Comprehensive Logging**: Detailed logs of all activities
- **Script Monitoring**: Monitors running processes and enforces max duration limits
- **Error Recovery**: Handles failures gracefully and continues operation

## Usage

### Local Development

```bash
# Run the orchestrator
python orchestrator_main.py

# The orchestrator will:
# - Monitor market hours continuously
# - Start the trading script 1 hour before market open
# - Stop the trading script after market close
# - Run indefinitely until interrupted (Ctrl+C)
```

### Docker Deployment

```bash
# Navigate to the python directory first
cd python

# Build the image
docker build -t strategy-lab:latest .

# Run with docker-compose (recommended)
docker-compose up

# Or run directly with Docker
docker run -it --rm strategy-lab:latest

# To override and run a different script
docker run -it --rm strategy-lab:latest python main.py
```

### Docker with Background Execution

```bash
# From the python directory

# Run in background with auto-restart
docker run -d --restart unless-stopped --name strategy-lab strategy-lab:latest

# View logs
docker logs -f strategy-lab

# Stop the container
docker stop strategy-lab

# Remove the container
docker rm strategy-lab
```

## Configuration

### Market Hours

The script is configured for US Eastern Time with these settings:

```python
MARKET_OPEN_TIME = "09:30"      # 9:30 AM ET
MARKET_CLOSE_TIME = "16:00"     # 4:00 PM ET
PRE_MARKET_START = "08:30"      # 1 hour before market open
MAX_SCRIPT_DURATION = 7 * 3600  # 7 hours max (8:30 AM - 3:30 PM)
```

To modify these settings, edit the constants at the top of `orchestrator_main.py`:

```python
# Customize these values for your needs
MARKET_TZ = pytz.timezone("America/New_York")  # Timezone
MARKET_OPEN_TIME = "09:30"                      # Market open
MARKET_CLOSE_TIME = "16:00"                     # Market close
PRE_MARKET_START = "08:30"                      # When to start scripts (1 hour before)
MAX_SCRIPT_DURATION = 7 * 3600                  # Maximum execution time
```

### Trading Script

By default, the orchestrator runs:

```python
TRADING_SCRIPT = "scripts/test_finnhub_orchestrator.py"
```

To change which script runs, modify this constant:

```python
TRADING_SCRIPT = "scripts/your_script_name.py"
```

Or pass it as an environment variable (if you implement that feature):

```bash
export TRADING_SCRIPT="scripts/your_script.py"
python orchestrator_main.py
```

## Script Requirements

Your trading script should:

1. **Exit cleanly**: End naturally when market closes or after a set time
2. **Handle signals**: Respond to SIGTERM/SIGINT for graceful shutdown
3. **Log activity**: Use the project's logger for monitoring
4. **Complete before next market open**: Finish all work before 8:30 AM ET the next day

### Example Script Structure

```python
import sys
import signal
from pathlib import Path
from src.utils.logger import get_logger

logger = get_logger("MyTradingScript")

def main():
    """Main trading logic."""
    logger.info("Starting trading script")

    try:
        # Your trading logic here
        # This should complete before market close
        pass
    except KeyboardInterrupt:
        logger.info("Script interrupted")
        return 1
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return 1

    logger.info("Script completed successfully")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

## Logging

All activity is logged to the project's logging system. View logs:

```bash
# Local
tail -f python/logs/orchestrator_main.log

# Docker
docker logs -f strategy-lab
```

Log levels include:

- **INFO**: Market status, script start/stop, normal operations
- **WARNING**: Script duration warnings, graceful shutdowns
- **ERROR**: Failures, exceptions, process errors
- **DEBUG**: Detailed monitoring information

## Behavior Examples

### During Market Hours (Tuesday, 10:00 AM ET)

```
2025-12-20 10:00:00 INFO: Status: running | Market day: True | Time: 10:00:00 ET
2025-12-20 10:01:00 INFO: Status: running | Market day: True | Time: 10:01:00 ET
2025-12-20 10:02:00 INFO: Status: running | Market day: True | Time: 10:02:00 ET
```

### At Market Close (4:00 PM ET)

```
2025-12-20 16:00:01 INFO: Market has closed. Stopping script.
2025-12-20 16:00:02 INFO: Stopping trading script (PID 12345)
2025-12-20 16:00:03 INFO: Script stopped gracefully
2025-12-20 16:00:04 INFO: Status: idle | Market day: True | Time: 16:00:04 ET
```

### Before Market Open (7:00 AM ET)

```
2025-12-20 07:00:00 INFO: Status: idle | Market day: True | Time: 07:00:00 ET
```

### Market Pre-Start (8:25 AM ET - 5 minutes before)

```
2025-12-20 08:25:00 INFO: Status: idle | Market day: True | Time: 08:25:00 ET
```

### Market Starts (8:30 AM ET)

```
2025-12-20 08:30:00 INFO: Starting trading script at 2025-12-20 08:30:00 EST
2025-12-20 08:30:00 INFO: Running: scripts/test_finnhub_orchestrator.py
2025-12-20 08:30:01 INFO: Script started with PID 12345
2025-12-20 08:30:01 INFO: Status: running | Market day: True | Time: 08:30:01 ET
```

### Weekend (Saturday)

```
2025-12-20 10:00:00 INFO: Status: idle | Market day: False | Time: 10:00:00 ET
```

## Deployment on Oracle Cloud

### Container Instances Deployment

1. Push image to OCIR or Docker Hub (see [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md))
2. Create Container Instance in Oracle Cloud Console
3. Configure:
   - **Image**: Your pushed image
   - **Container Memory**: 1 GB (free tier)
   - **CPUs**: 0.5-1 OCPU (free tier)
4. Container will start automatically and run the orchestrator

### Compute Instance Deployment

1. Create VM.Standard.E2.1.Micro instance (always free eligible)
2. SSH in and install Docker
3. Pull and run:
   ```bash
   docker pull <your-registry>/strategy-lab:latest
   docker run -d --restart unless-stopped --name strategy-lab \
     <your-registry>/strategy-lab:latest
   ```

### Monitoring

```bash
# Check container status
docker ps | grep strategy-lab

# View logs
docker logs -f strategy-lab

# Get resource usage
docker stats strategy-lab
```

## Troubleshooting

### Script Not Starting

**Problem**: Script doesn't start at pre-market time

**Solution**:
1. Check timezone configuration matches your system
2. Verify script file exists at configured path
3. Test script runs manually: `python scripts/test_finnhub_orchestrator.py`
4. Check logs for errors

### Script Won't Stop

**Problem**: Script continues running after market close

**Solution**:
1. Script should exit naturally - add timeout logic
2. Orchestrator will force-kill after 10-second graceful period
3. Check if script is catching SIGTERM signals

### Container Exits

**Problem**: Docker container exits immediately

**Solution**:
1. Check logs: `docker logs strategy-lab`
2. Verify Python environment is configured
3. Ensure all dependencies are installed
4. Check for runtime errors in the orchestrator script

### High Memory Usage

**Problem**: Container using too much memory

**Solution**:
1. Reduce data window in trading script
2. Clear cache regularly: `rm -rf python/data_cache/*`
3. Increase container memory limit on Oracle Cloud

## Advanced Configuration

### Custom Timing

To run scripts at different times:

```python
# Run from 9:00 AM to 5:00 PM
PRE_MARKET_START = "09:00"
MARKET_CLOSE_TIME = "17:00"

# Run for only 4 hours (half day)
MAX_SCRIPT_DURATION = 4 * 3600
```

### Multiple Scripts

To run multiple scripts sequentially:

```python
# Modify orchestrator_main.py to support multiple scripts
TRADING_SCRIPTS = [
    "scripts/data_collection.py",
    "scripts/strategy_execution.py",
    "scripts/results_analysis.py",
]
```

### Custom Exit Signals

Scripts can signal early completion to the orchestrator by:

```python
# Exit with specific code
sys.exit(42)  # Signal successful early completion
```

## Performance Optimization

For Oracle Cloud Always Free tier:

1. **Minimize memory**: Use 512 MB - 1 GB container memory
2. **Optimize data**: Fetch only needed tickers and timeframes
3. **Cache aggressively**: Reuse fetched data across runs
4. **Batch operations**: Group API calls to reduce latency
5. **Monitor uptime**: Container automatically restarts if needed

## Next Steps

1. Deploy to Oracle Cloud using [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md)
2. Monitor logs for first successful run
3. Adjust market hours if needed for your timezone
4. Add persistent storage for trade results (Object Storage or Block Volume)
5. Set up alerts for script failures (Oracle Notifications Service)
