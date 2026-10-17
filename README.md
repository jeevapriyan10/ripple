# Ripple

[![Status: Pre-release v0.1.0](https://img.shields.io/badge/status-pre--release%20v0.1.0-blue.svg)](#)

> **Pre-release v0.1.0**: Domain model, 5 network adapters, adapter conformance suite, and multi-network synthetic transaction generator are implemented; graph storage, streaming and scoring are in progress.

Real-time, network-agnostic payment-fraud detection platform via graph learning.

## Architecture

Ripple is designed as a cloud-deployable, containerized monorepo platform featuring:
- **Backend**: Python FastAPI service with structured JSON logging, strict Pydantic settings management, and health/readiness probes.
- **Frontend**: React 18 + TypeScript client built with Vite, served via Nginx in production.
- **Tooling**: Docker Compose orchestration, Poetry dependency management, Ruff/Black/Mypy linters, and Pytest.

```
ripple/
├── backend/            # FastAPI service (Python ^3.11, Poetry)
│   ├── ripple/         # Core application package
│   │   ├── api/        # Routers (/health, /ready)
│   │   ├── config.py   # Centralized Pydantic settings
│   │   └── main.py     # App factory & structured logging
│   ├── tests/          # Pytest test suite
│   └── Dockerfile      # Multi-stage non-root runtime image
├── frontend/           # React + TypeScript client (Vite, Nginx)
│   ├── src/            # Application source code
│   ├── nginx.conf      # Reverse proxy & SPA fallback
│   └── Dockerfile      # Multi-stage Node builder & Nginx runner
├── docker-compose.yml  # Service composition & development mounts
├── .env.example        # Environment variable templates
└── Makefile            # Common developer commands
```

## Getting Started

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/)
- *(Optional for local development outside Docker)*:
  - Python >= 3.11 & [Poetry](https://python-poetry.org/)
  - Node.js >= 18 & npm

### 1. Environment Setup

Copy the template `.env.example` file to create your local `.env`:

```bash
cp .env.example .env
```

Review `.env` and adjust any values if necessary. Defaults are preconfigured for local development.

### 2. Start Services

Build and launch all services with Docker Compose:

```bash
make up
```

*(Alternatively: `docker compose up --build`)*

### 3. Verify Endpoints

Once the containers are running, access the services:

| Service | URL | Description |
|---|---|---|
| **Frontend UI** | [http://localhost:3000](http://localhost:3000) | React connectivity dashboard |
| **Backend API** | [http://localhost:8000](http://localhost:8000) | FastAPI server root |
| **Liveness Check** | [http://localhost:8000/health](http://localhost:8000/health) | `{"status": "ok", "version": "0.1.0", "env": "development"}` |
| **Readiness Check** | [http://localhost:8000/ready](http://localhost:8000/ready) | `{"status": "ready", "dependencies": {}}` |
| **API Docs (Swagger)** | [http://localhost:8000/docs](http://localhost:8000/docs) | Interactive OpenAPI documentation |

### 4. Development Commands

The root `Makefile` provides convenient shortcuts:

```bash
# Run backend tests
make test

# Run code linters (ruff, black --check, mypy)
make lint

# Auto-format codebase
make fmt

# Generate benchmark synthetic dataset
make generate-sample

# Verify dataset cryptographic integrity
make verify-sample

# Tail container logs
make logs

# Stop and remove containers
make down
```

## Synthetic Data Engine & Safety Disclaimer

Ripple includes a multi-network synthetic transaction generator (`ripple.generator`) that models realistic transaction traffic across all five payment networks, injects 10 complex multi-hop financial crime typologies, and outputs canonical payloads for testing and benchmark training.

### Safety Guarantees
- **No Real Customer Data**: All actors, transactions, and credentials are synthetically generated via deterministic pseudorandom generation.
- **Non-Routable Identifiers**: Phone numbers use the `+999` reserved prefix; Bitcoin Base58 addresses have deliberately corrupt checksums; IBANs are synthetic mod-97 valid numbers using fictional financial institution codes.
- **No Card PANs**: Raw 13–19 digit credit card numbers and Luhn-valid sequences are strictly prohibited and never generated.
- **Sidecar Truth**: Ground-truth labels are written to an isolated `truth.jsonl.gz` sidecar file and never leak into raw event payloads.

For full details, see [docs/generator/README.md](file:///docs/generator/README.md) and [docs/generator/scenarios.md](file:///docs/generator/scenarios.md).

## Scope & Limitations

### Real-World Rails vs. Documented JSON Payloads
Real-world payment networks utilize proprietary or binary transport protocols that are either not freely published or require specialized cryptographic hardware:
- **UPI (NPCI)**: Real NPCI wire communications are XML (ISO 20022 variants) passed over private MPLS/VPN networks with strict digital signature envelopes.
- **Card Networks**: Real card clearing and authorization streams are encoded in binary bitmap-based ISO 8583 (1987/1993/2003) or ISO 20022 messages.

To remain open, reproducible, and cloud-native, Ripple targets documented, simplified JSON payload specifications (**"Ripple UPI-style spec v1"** and **"Ripple card spec v1"**) that accurately model the publicly known semantics and lifecycle states of each rail. In a production enterprise deployment, a thin translation layer (e.g. an HSM ISO-bridge or an NPCI XML parser) sits immediately upstream of Ripple's network adapters to convert binary/XML feeds into Ripple's JSON specification.

### Additional Known Boundaries
- **Zero-Amount Authorizations**: Account-verification checks ($0.00 authorizations) cannot be modeled because the universal domain model strictly requires positive transaction amounts ($> 0$). These are explicitly rejected at adapter boundary.
- **Entity Resolution**: An individual counterparty transacting across multiple identifiers (e.g., card token vs. VPA vs. phone) initially manifests as separate `Party` nodes. Multi-identifier persona linkage is resolved downstream by Module 8 (Mesh Entity Resolution).
- **Institutional Hops as Super-Nodes**: In four-party card models, global payment schemes and major acquirers process millions of transactions, creating high-degree super-nodes in the transaction graph.
- **Generator Limitations**: See [docs/generator/limitations.md](file:///docs/generator/limitations.md) for assumptions regarding stationary actor populations, unobserved cash off-ramps, and empirical diurnal curves.