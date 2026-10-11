import React, { useEffect, useState } from 'react';
import { getHealth, HealthResponse, API_BASE_URL } from './api/client';

export const App: React.FC = () => {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);

  const checkConnectivity = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getHealth();
      setHealth(data);
      setLastChecked(new Date());
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Unknown network error';
      setError(message);
      setHealth(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    checkConnectivity();
  }, []);

  return (
    <div style={styles.container}>
      <header style={styles.header}>
        <div style={styles.brand}>
          <div style={styles.logoIcon}>⚡</div>
          <h1 style={styles.title}>Ripple</h1>
        </div>
        <p style={styles.subtitle}>Network-Agnostic Real-Time Payment-Fraud Detection Platform</p>
      </header>

      <main style={styles.main}>
        <div style={styles.card}>
          <div style={styles.cardHeader}>
            <h2 style={styles.cardTitle}>Backend Connectivity Status</h2>
            <span style={styles.endpointBadge}>
              Endpoint: {API_BASE_URL || window.location.origin}/health
            </span>
          </div>

          {loading && (
            <div style={styles.loadingContainer}>
              <div style={styles.spinner} />
              <p style={styles.loadingText}>Probing backend health endpoint...</p>
            </div>
          )}

          {!loading && error && (
            <div style={styles.errorContainer}>
              <div style={styles.errorIcon}>⚠️</div>
              <div style={styles.errorContent}>
                <h3 style={styles.errorTitle}>Backend Unreachable</h3>
                <p style={styles.errorMessage}>{error}</p>
                <p style={styles.errorHint}>
                  Verify that the backend service is running and accessible from the browser.
                </p>
                <button style={styles.retryButton} onClick={checkConnectivity}>
                  Retry Connection
                </button>
              </div>
            </div>
          )}

          {!loading && health && (
            <div style={styles.successContainer}>
              <div style={styles.statusRow}>
                <span style={styles.label}>Connection State:</span>
                <span style={styles.badgeSuccess}>Connected</span>
              </div>
              <div style={styles.grid}>
                <div style={styles.gridItem}>
                  <span style={styles.gridLabel}>Status</span>
                  <span style={styles.gridValue}>{health.status}</span>
                </div>
                <div style={styles.gridItem}>
                  <span style={styles.gridLabel}>Version</span>
                  <span style={styles.gridValue}>{health.version}</span>
                </div>
                <div style={styles.gridItem}>
                  <span style={styles.gridLabel}>Environment</span>
                  <span style={styles.gridValue}>{health.env}</span>
                </div>
              </div>

              {lastChecked && (
                <p style={styles.timestamp}>
                  Last verified at {lastChecked.toLocaleTimeString()}
                </p>
              )}

              <button style={styles.refreshButton} onClick={checkConnectivity}>
                Refresh Status
              </button>
            </div>
          )}
        </div>
      </main>

      <footer style={styles.footer}>
        Ripple Walking Skeleton &bull; React + TypeScript &bull; FastAPI
      </footer>
    </div>
  );
};

export default App;

const styles: Record<string, React.CSSProperties> = {
  container: {
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, "Helvetica Neue", sans-serif',
    backgroundColor: '#0b0f19',
    color: '#f3f4f6',
    minHeight: '100vh',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '32px 16px',
    boxSizing: 'border-box',
  },
  header: {
    textAlign: 'center',
    maxWidth: '640px',
  },
  brand: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '12px',
    marginBottom: '8px',
  },
  logoIcon: {
    fontSize: '28px',
    padding: '6px',
    background: 'linear-gradient(135deg, #3b82f6, #8b5cf6)',
    borderRadius: '10px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  title: {
    fontSize: '36px',
    fontWeight: 800,
    letterSpacing: '-0.03em',
    margin: 0,
    background: 'linear-gradient(135deg, #ffffff 40%, #94a3b8 100%)',
    WebkitBackgroundClip: 'text',
    WebkitTextFillColor: 'transparent',
  },
  subtitle: {
    color: '#94a3b8',
    fontSize: '15px',
    lineHeight: '1.5',
    margin: 0,
  },
  main: {
    width: '100%',
    maxWidth: '560px',
    margin: '32px 0',
  },
  card: {
    backgroundColor: '#111827',
    border: '1px solid #1f2937',
    borderRadius: '16px',
    padding: '28px',
    boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.4), 0 8px 10px -6px rgba(0, 0, 0, 0.3)',
  },
  cardHeader: {
    borderBottom: '1px solid #1f2937',
    paddingBottom: '16px',
    marginBottom: '20px',
  },
  cardTitle: {
    fontSize: '18px',
    fontWeight: 600,
    margin: '0 0 6px 0',
    color: '#f9fafb',
  },
  endpointBadge: {
    fontSize: '12px',
    color: '#6b7280',
    wordBreak: 'break-all',
  },
  loadingContainer: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    padding: '36px 0',
    gap: '12px',
  },
  spinner: {
    width: '32px',
    height: '32px',
    border: '3px solid #374151',
    borderTopColor: '#3b82f6',
    borderRadius: '50%',
    animation: 'spin 1s linear infinite',
  },
  loadingText: {
    color: '#9ca3af',
    fontSize: '14px',
    margin: 0,
  },
  errorContainer: {
    display: 'flex',
    gap: '16px',
    backgroundColor: 'rgba(239, 68, 68, 0.1)',
    border: '1px solid rgba(239, 68, 68, 0.3)',
    borderRadius: '12px',
    padding: '20px',
  },
  errorIcon: {
    fontSize: '28px',
  },
  errorContent: {
    flex: 1,
  },
  errorTitle: {
    color: '#f87171',
    fontSize: '16px',
    fontWeight: 600,
    margin: '0 0 6px 0',
  },
  errorMessage: {
    color: '#fca5a5',
    fontSize: '14px',
    fontFamily: 'monospace',
    margin: '0 0 10px 0',
  },
  errorHint: {
    color: '#9ca3af',
    fontSize: '12px',
    margin: '0 0 16px 0',
  },
  retryButton: {
    backgroundColor: '#ef4444',
    color: '#ffffff',
    border: 'none',
    padding: '8px 16px',
    borderRadius: '8px',
    cursor: 'pointer',
    fontWeight: 500,
    fontSize: '13px',
  },
  successContainer: {
    display: 'flex',
    flexDirection: 'column',
    gap: '16px',
  },
  statusRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  label: {
    color: '#9ca3af',
    fontSize: '14px',
  },
  badgeSuccess: {
    backgroundColor: 'rgba(34, 197, 94, 0.15)',
    color: '#4ade80',
    border: '1px solid rgba(34, 197, 94, 0.3)',
    padding: '4px 10px',
    borderRadius: '20px',
    fontSize: '12px',
    fontWeight: 600,
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, 1fr)',
    gap: '12px',
    marginTop: '4px',
  },
  gridItem: {
    backgroundColor: '#1f2937',
    padding: '12px',
    borderRadius: '10px',
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
  },
  gridLabel: {
    color: '#9ca3af',
    fontSize: '11px',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  gridValue: {
    color: '#f3f4f6',
    fontSize: '15px',
    fontWeight: 600,
  },
  timestamp: {
    color: '#6b7280',
    fontSize: '12px',
    margin: '8px 0 0 0',
    textAlign: 'center',
  },
  refreshButton: {
    backgroundColor: '#2563eb',
    color: '#ffffff',
    border: 'none',
    padding: '10px 18px',
    borderRadius: '8px',
    cursor: 'pointer',
    fontWeight: 600,
    fontSize: '13px',
    marginTop: '8px',
    alignSelf: 'flex-end',
  },
  footer: {
    color: '#6b7280',
    fontSize: '12px',
    textAlign: 'center',
  },
};
