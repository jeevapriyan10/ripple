export interface HealthResponse {
  status: string;
  version: string;
  env: "development" | "staging" | "production";
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL;

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${apiBaseUrl}/health`);

  if (!response.ok) {
    throw new Error(`Backend health check failed (${response.status})`);
  }

  return (await response.json()) as HealthResponse;
}
