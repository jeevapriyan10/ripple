# Ripple

[![Status: Under active development](https://img.shields.io/badge/status-under%20active%20development-orange.svg)](#)

Real-time, network-agnostic fraud detection via graph learning.

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

# Tail container logs
make logs

# Stop and remove containers
make down
```