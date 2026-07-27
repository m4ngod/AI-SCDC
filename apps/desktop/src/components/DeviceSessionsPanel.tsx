import { useEffect, useState } from "react";
import type {
  ConsoleApiClient,
  DeviceSessionCard
} from "../api/client";

type DeviceSessionsPanelProps = {
  apiClient: ConsoleApiClient;
};

function displayTime(value: string) {
  return `${value.replace("T", " ").slice(0, 16)} UTC`;
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

export function DeviceSessionsPanel({
  apiClient
}: DeviceSessionsPanelProps) {
  const [sessions, setSessions] = useState<DeviceSessionCard[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    apiClient
      .listDeviceSessions()
      .then((result) => {
        if (!cancelled) {
          setSessions(result);
        }
      })
      .catch((loadError: unknown) => {
        if (!cancelled) {
          setError(
            errorMessage(loadError, "Failed to load Device Sessions")
          );
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient]);

  async function handleRevoke(deviceSessionId: string) {
    if (revokingId !== null) {
      return;
    }
    setRevokingId(deviceSessionId);
    setError(null);
    try {
      await apiClient.revokeDeviceSession(deviceSessionId);
      setSessions((currentSessions) =>
        currentSessions.filter(
          (deviceSession) => deviceSession.id !== deviceSessionId
        )
      );
      try {
        setSessions(await apiClient.listDeviceSessions());
      } catch {
        setError(
          "Device Session was revoked, but the list could not be refreshed"
        );
      }
    } catch (revokeError) {
      setError(
        errorMessage(revokeError, "Failed to revoke Device Session")
      );
    } finally {
      setRevokingId(null);
    }
  }

  return (
    <section
      className="context-section device-sessions"
      aria-label="Device sessions"
    >
      <div className="section-heading">
        <h2>Device sessions</h2>
        {!isLoading && error === null ? (
          <span>{sessions.length} active</span>
        ) : null}
      </div>
      {isLoading ? <p>Loading sessions…</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {!isLoading && error === null && sessions.length === 0 ? (
        <p>No active Device Sessions.</p>
      ) : null}
      <ul className="device-session-list">
        {sessions.map((deviceSession) => (
          <li key={deviceSession.id} className="device-session-row">
            <div className="device-session-title">
              <strong>{deviceSession.device_description}</strong>
              {deviceSession.is_current ? (
                <span className="status-pill">This device</span>
              ) : null}
            </div>
            <dl>
              <div>
                <dt>Status</dt>
                <dd>
                  {deviceSession.status === "active"
                    ? "Active"
                    : deviceSession.status}
                </dd>
              </div>
              <div>
                <dt>Created</dt>
                <dd>
                  <time dateTime={deviceSession.created_at}>
                    {displayTime(deviceSession.created_at)}
                  </time>
                </dd>
              </div>
              <div>
                <dt>Recent activity</dt>
                <dd>
                  <time dateTime={deviceSession.last_seen_at}>
                    {displayTime(deviceSession.last_seen_at)}
                  </time>
                </dd>
              </div>
            </dl>
            {!deviceSession.is_current ? (
              <button
                type="button"
                aria-label={`Revoke ${deviceSession.device_description}`}
                disabled={revokingId !== null}
                onClick={() => {
                  void handleRevoke(deviceSession.id);
                }}
              >
                {revokingId === deviceSession.id ? "Revoking" : "Revoke"}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
