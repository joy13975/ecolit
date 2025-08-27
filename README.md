# Ecolit

A Python tool for reading ECHONET Lite data from solar panel and battery HEMS (Home Energy Management System) to optimize energy consumption, particularly for Tesla vehicle charging.

## Features

- Read real-time data from ECHONET Lite compatible solar/battery systems
- Monitor energy production, consumption, and grid interaction
- Optimize Tesla charging current to minimize grid purchases
- Multiple charging modes:
  - **Grid-free mode**: Adjust charging to use only solar/battery power
  - **Emergency mode**: Full-speed charging when explicitly instructed
  - Additional modes to be implemented based on usage patterns

## Installation

```bash
# Install dependencies
make install

# For development
make dev
```

## Usage

```bash
# Run the main application
make run
```

## Development

This project uses `uv` for Python environment management. All Python commands are standardized through the Makefile:

```bash
make help       # Show all available commands
make test       # Run tests
make lint       # Check code with ruff
make format     # Format code with ruff
make clean      # Clean cache and build files
```

## Architecture

The system operates on a LAN to communicate with ECHONET Lite devices via broadcast protocol. Future iterations will include:
- House server for continuous monitoring
- Cloud integration for remote monitoring and control

## Documentation

Detailed documentation is available in the `docs/` directory.

## Requirements

- Python 3.12+
- uv (for virtual environment management)
- pychonet (for ECHONET Lite communication)
- Access to ECHONET Lite compatible HEMS devices on local network