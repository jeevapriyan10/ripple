# Ripple

Real-time, network-agnostic fraud detection via graph learning.

![Status: Under active development](https://img.shields.io/badge/status-under%20active%20development-yellow)

## Architecture

## Getting Started

Prerequisites: Docker Desktop (with Docker Compose) and GNU Make. For local backend
development, install Python 3.11+ and Poetry; for local frontend development, install
Node.js 20+.

Copy the committed environment template, then start both services:

```sh
cp .env.example .env
make up
```

The frontend is available at `http://localhost:5173`; its health panel calls the
backend through the development proxy. Stop the stack with `make down`.
