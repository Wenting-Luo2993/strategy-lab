# Docker Deployment Guide for Oracle Cloud Always Free

## Overview
This guide explains how to build and deploy the Strategy Lab application to Oracle Cloud's Always Free service using Docker.

## Prerequisites
- Docker installed locally
- Oracle Cloud Always Free account
- Oracle Cloud CLI (optional, but recommended)
- Docker Hub account or Oracle Container Registry access

## Building the Docker Image

### 1. Build Locally
```bash
# Navigate to the python directory first
cd python

# Build the image
docker build -t strategy-lab:latest .
```

### 2. Test Locally
```bash
# From the python directory

# Run with default command (orchestrator_main.py)
docker run --rm strategy-lab:latest

# Run with specific script
docker run --rm strategy-lab:latest python main.py

# Run interactively
docker run -it --rm strategy-lab:latest /bin/bash

# Using docker-compose (from python directory)
docker-compose up
```

## Tagging and Pushing to Registry

### Option A: Docker Hub
```bash
# Login to Docker Hub
docker login

# Tag the image
docker tag strategy-lab:latest <your-docker-username>/strategy-lab:latest

# Push to Docker Hub
docker push <your-docker-username>/strategy-lab:latest
```

### Option B: Oracle Container Registry (OCIR)
```bash
# Login to OCIR (use your Oracle Cloud credentials)
docker login <region>.ocir.io

# Tag the image
docker tag strategy-lab:latest <region>.ocir.io/<tenancy>/<repo>/strategy-lab:latest

# Push to OCIR
docker push <region>.ocir.io/<tenancy>/<repo>/strategy-lab:latest
```

## Deploying to Oracle Cloud Always Free

### Option 1: Container Instances (Recommended for Always Free)
1. Go to **Compute > Container Instances** in Oracle Cloud Console
2. Click **Create Container Instance**
3. Configure:
   - **Name**: strategy-lab
   - **Image**: Select your pushed image
   - **Compartment**: Select your compartment
   - **Availability Domain**: Choose any
   - **Container Memory**: 1 GB (free tier limit)
   - **CPUs**: 0.5-1 OCPU (within free tier)
4. Under **Container Configuration**:
   - Set environment variables if needed (API keys, credentials)
   - Mount volumes for data persistence if needed
5. Click **Create**

### Option 2: Compute Instance with Docker
1. Create a Compute Instance (VM.Standard.E2.1.Micro - always free eligible)
2. SSH into the instance
3. Install Docker:
   ```bash
   sudo yum update -y
   sudo yum install docker-ce -y
   sudo systemctl start docker
   sudo usermod -aG docker $USER
   ```
4. Pull and run your image:
   ```bash
   docker pull <your-registry>/strategy-lab:latest
   docker run -d --name strategy-lab <your-registry>/strategy-lab:latest
   ```

## Configuration for Oracle Cloud

### Environment Variables
Create a `.env` file with required configurations:
```
FINNHUB_API_KEY=your_key_here
POLYGON_API_KEY=your_key_here
# Add other required environment variables
```

### Networking
- If you need to expose ports (for a web service), configure:
  - Security Lists
  - Network Security Groups
  - Ingress rules

### Storage
- Use **Object Storage** for persistent data
- Use **Block Storage** volumes if deploying on Compute instances
- Configure volumes in docker-compose or container instance configuration

## Always Free Tier Limits
- **Compute**: 2 OCPUs, 12 GB RAM total
- **Container Instances**: Up to 2 containers total
- **Storage**: 100 GB free tier storage
- **Bandwidth**: Limited outbound bandwidth

## Monitoring and Logs

### View Container Logs

#### Option 1: Using docker-compose (Recommended)
With docker-compose, logs are automatically mounted to your host machine:

```bash
# Start the container
docker-compose up

# In another terminal, view the orchestrator logs directly on your host
tail -f logs/OrchestratorMain.log

# Or view all logs from docker-compose
docker-compose logs -f strategy-lab
```

#### Option 2: Using `docker logs` command
View container stdout/stderr output:

```bash
# If running with docker run
docker run -d --name strategy-lab strategy-lab:latest
docker logs -f strategy-lab

# If running with docker-compose
docker-compose logs -f strategy-lab
```

#### Option 3: Access logs inside the container
Execute commands inside the running container:

```bash
# View orchestrator logs
docker exec -it strategy-lab tail -f logs/OrchestratorMain.log

# Follow logs in real-time
docker exec -it strategy-lab tail -f logs/OrchestratorMain.log

# View the last 100 lines
docker exec -it strategy-lab tail -n 100 logs/OrchestratorMain.log
```

#### Option 4: Using Oracle Cloud CLI (On Oracle Cloud)
```bash
# List container instances
oci container-instances container list

# Get container logs
oci container-instances container-logs get --container-id <container-id>
```

### Log Files Location

- **Inside Container**: `/app/python/logs/OrchestratorMain.log`
- **Host Machine (with docker-compose)**: `./logs/OrchestratorMain.log`
- **Container stdout**: `docker logs <container_id>`

### Understanding the Logs

The orchestrator logs include:

- **INFO**: Market hours detection, script start/stop, periodic status updates
- **WARNING**: Script duration warnings, graceful shutdowns
- **ERROR**: Script failures, orchestrator errors
- **DEBUG**: Detailed monitoring information (if enabled)

Example log output:

```
2025-12-20 08:30:00 [OrchestratorMain] [INFO] Starting trading script at 2025-12-20 08:30:00 EST
2025-12-20 08:30:01 [OrchestratorMain] [INFO] Script started with PID 12345
2025-12-20 10:00:00 [OrchestratorMain] [INFO] Status: running | Market day: True | Time: 10:00:00 ET
```

### Performance Optimization
1. Use Python 3.12-slim base image (minimal size, ensures pandas_ta compatibility)
2. Multi-stage builds reduce final image size
3. Clean up pip cache and apt cache
4. Use `.dockerignore` to exclude unnecessary files

## Scaling Considerations

For Always Free tier:
- Single container instance is optimal
- Use scheduled jobs for backtesting to avoid constant resource usage
- Consider cron jobs on Compute instance for periodic tasks

## Troubleshooting

### Image Won't Start
- Check logs: `docker logs <container_id>`
- Verify environment variables are set
- Ensure API keys/credentials are valid

### Out of Memory
- Reduce backtesting data window
- Increase available memory (if not in free tier)
- Optimize data loading

### Network Issues
- Check security group rules
- Verify API endpoints are accessible
- Check for rate limiting on external APIs (Finnhub, Yahoo Finance)

## Next Steps
1. Add API key management (use Oracle Vault)
2. Implement persistent storage for results
3. Set up scheduled backtests
4. Monitor resource usage in Oracle Cloud Console
5. Consider managed databases for larger projects

## References
- [Oracle Container Instances Documentation](https://docs.oracle.com/en-us/iaas/container-instances/)
- [Oracle Cloud Always Free](https://www.oracle.com/cloud/free/)
- [Docker Best Practices](https://docs.docker.com/develop/dev-best-practices/)
