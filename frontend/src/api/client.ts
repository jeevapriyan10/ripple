export interface HealthResponse {
  status: string;
  version: string;
  env: string;
}

export interface ReadyResponse {
  status: string;
  dependencies: Record<string, unknown>;
}

// Retrieve base URL from environment or default to relative path (reverse proxy)
const rawBaseUrl = import.meta.env.VITE_API_BASE_URL;
export const API_BASE_URL = (rawBaseUrl !== undefined && rawBaseUrl !== '')
  ? rawBaseUrl.replace(/\/+$/, '')
  : '';

/**
 * Fetch backend liveness status.
 */
export async function getHealth(): Promise<HealthResponse> {
  const url = `${API_BASE_URL}/health`;
  const response = await fetch(url, {
    method: 'GET',
    headers: {
      Accept: 'application/json',
    },
  });

  if (!response.ok) {
    throw new Error(`Health check failed with HTTP ${response.status}: ${response.statusText}`);
  }

  return response.json();
}

/**
 * Fetch backend readiness status.
 */
export async function getReady(): Promise<ReadyResponse> {
  const url = `${API_BASE_URL}/ready`;
  const response = await fetch(url, {
    method: 'GET',
    headers: {
      Accept: 'application/json',
    },
  });

  if (!response.ok) {
    throw new Error(`Readiness check failed with HTTP ${response.status}: ${response.statusText}`);
  }

  return response.json();
}
