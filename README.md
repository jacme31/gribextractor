# GribExtractor

A real-time GFS (Global Forecast System) GRIB file downloader and monitor with automatic retry logic, source failover, and a web-based status interface.

## Overview

**GribExtractor** downloads GRIB2 format subsets from NOAA's Global Forecast System (GFS) as soon as they become available. It includes:

- **Real-time monitoring**: Automatically checks for new forecast data at configurable intervals
- **Robust download handling**: Automatic retries with exponential backoff and persistent checkpoint recovery
- **Multi-source failover**: Attempts downloads from multiple sources (AWS, Google Cloud, Azure, NOMADS, NCAR RDA) in priority order
- **Web dashboard**: Real-time status interface with download progress, statistics, and manual controls
- **Bilingual support**: French and English interfaces
- **Docker deployment**: Production-ready containerization with proper signal handling and diagnostics

## Use Cases

- Real-time weather model data processing pipelines
- Automated forecast extraction for specific regions or parameters
- Continuous data ingestion into weather analysis systems
- Research workflows requiring up-to-date GFS model runs

## Quick Start

### Local Installation

```bash
# Clone the repository
git clone https://github.com/jacme31/gribextractor.git
cd gribextractor

# Install dependencies
pip install -r requirements.txt

# Run the application
python gribextractor.py
```

The web interface will be available at `http://localhost:5959`

**⚠️ Note on Herbie dependency**: Currently, `requirements.txt` includes a development version of Herbie from [PR#554](https://github.com/blaylockbk/Herbie/pull/554) to improve error handling when sources fail. Once this PR is merged into the main Herbie release, the requirements will be updated to use the stable version.

### Docker Deployment

```bash
# Build and run with Docker Compose
docker compose up --build
```

See [README.Docker.md](README.Docker.md) for detailed Docker configuration options, including:
- Language selection (French/English)
- Source priority configuration
- Download recovery and checkpoint management
- Troubleshooting and diagnostics

## Configuration

Configuration is stored in `~/.config/gribextractor/config.txt` and includes:
- Next forecast hour to process (automatically updated after each download)
- Download statistics and state

### Environment Variables

Set these in `compose.yaml` (Docker) or pass them when running locally:

- `PRIORITY`: Comma-separated list of GFS sources (default: `aws,google,azure`)
  - Valid sources: `aws`, `google`, `azure`, `nomads`, `ncar_rda`
- `UI_LANGUAGE`: Interface language (`fr` for French, `en` for English; default: `fr`)
- `GRIB_MIN_MESSAGES`: Minimum number of GRIB messages required in a downloaded
  file (positive integer; default: `1`). This counts messages, not distinct
  fields, and does not verify that specific parameters are present.

## Features

### Download Management

- **Availability checking**: Uses `HerbieWait` with configurable timeout (default: 120 seconds) to wait for files
- **Automatic retry**: Failed downloads (other than timeouts) trigger up to 4 retry attempts with 60/120/180-second delays
- **Timeout persistence**: Availability timeouts don't consume failure budget and are retried indefinitely
- **Checkpoint recovery**: Saves progress so interrupted downloads can resume from the last successful download

### Source Failover

The application automatically falls back to the next source if one fails. Example priority chain:
```
AWS → Google Cloud → Azure → NOMADS → NCAR RDA
```

### Web Interface

The dashboard displays:
- Current download status (running, idle, error)
- Download statistics (total processed, validated, abandoned)
- Last successfully downloaded file
- Last abandoned forecast hour (with manual clear button)
- Start/Stop controls for the download worker
- Real-time status updates

### Error Handling & Diagnostics

- **Persistent logging**: All events (downloads, errors, timeouts) are logged
- **Thread diagnostics**: Linux/Docker: Send `SIGUSR1` signal to dump Python stack traces without stopping the application
- **Graceful shutdown**: Stop button responds immediately; active operations finish cleanly before shutdown

## Known Issues & Limitations

### Index File Availability

Occasionally, `HerbieWait` returns successfully but the GRIB index file (`.idx`) is missing, causing subsequent downloads to fail. This occurs because the data file and index file are not always available simultaneously.

**Workaround**: The application checks for index file presence and treats missing indices as availability timeouts.

**Related issue**: [blaylockbk/Herbie#569](https://github.com/blaylockbk/Herbie/issues/569) - Proposed enhancement to `HerbieWait` for robust index checking.

## Dependencies

- **herbie-data**: GRIB file downloader from NOAA sources
- **pygrib**: GRIB file validation and parsing
- **Flask**: Web framework for the status dashboard
- **Python 3.9+**

See [requirements.txt](requirements.txt) for exact versions.

## Project Structure

```
gribextractor/
├── gribextractor.py          # Main application (download worker + Flask server)
├── test_gribextractor.py     # Unit tests
├── requirements.txt          # Python dependencies
├── Dockerfile                # Container image definition
├── compose.yaml              # Docker Compose configuration
├── entrypoint.sh             # Container entrypoint script
├── templates/                # Flask HTML templates (status dashboard)
├── README.md                 # This file
└── README.Docker.md          # Docker-specific documentation
```

## Development & Testing

```bash
# Run tests
python -m pytest test_gribextractor.py -v

# Run with verbose logging
python gribextractor.py --verbose
```

## Contributing

Contributions are welcome! Please:
1. Test changes locally before submitting
2. Run the test suite to ensure nothing breaks
3. Update documentation if behavior changes

## License

[Check repository for license information]

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

**Note**: This project depends on external data availability from NOAA sources. Download timing depends on data release schedules and network conditions.
