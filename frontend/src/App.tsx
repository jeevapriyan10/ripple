import { useEffect, useState } from "react";

import { getHealth, type HealthResponse } from "./api/client";

export function App() {
  const [health, setHealth] = useState<HealthResponse>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setError("Backend is unreachable."));
  }, []);

  if (error) {
    return <main><h1>Ripple</h1><p role="alert">{error}</p></main>;
  }

  if (!health) {
    return <main><h1>Ripple</h1><p>Checking backend status…</p></main>;
  }

  return (
    <main>
      <h1>Ripple</h1>
      <p>Backend status: {health.status}</p>
      <p>Version: {health.version}</p>
      <p>Environment: {health.env}</p>
    </main>
  );
}
