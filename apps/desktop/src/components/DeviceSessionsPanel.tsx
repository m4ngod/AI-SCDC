import { useEffect, useState } from "react";
import type {
  ConsoleApiClient,
  DeviceSessionCard
} from "../api/client";
import { WebConsoleAuthenticationError } from "../api/client";

type DeviceSessionsPanelProps = {
  apiClient: ConsoleApiClient;
  recentAuthenticationResult?: string | null;
};

function displayTime(value: string) {
  return `${value.replace("T", " ").slice(0, 16)} UTC`;
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

export function DeviceSessionsPanel({
  apiClient,
  recentAuthenticationResult = null
}: DeviceSessionsPanelProps) {
  const [sessions, setSessions] = useState<DeviceSessionCard[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [isRevokingOthers, setIsRevokingOthers] = useState(false);
  const [otherSessionsRevoked, setOtherSessionsRevoked] =
    useState(false);
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
    if (revokingId !== null || isRevokingOthers) {
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

  async function handleRevokeOtherSessions() {
    if (revokingId !== null || isRevokingOthers) {
      return;
    }
    setIsRevokingOthers(true);
    setError(null);
    try {
      await apiClient.revokeOtherDeviceSessions();
      setSessions((currentSessions) =>
        currentSessions.filter(
          (deviceSession) =>
            deviceSession.is_current
            || deviceSession.status !== "active"
        )
      );
      setOtherSessionsRevoked(true);
      try {
        setSessions(await apiClient.listDeviceSessions());
      } catch {
        setError(
          "Other Device Sessions were signed out, but the list could not be refreshed"
        );
      }
    } catch (revokeError) {
      if (
        revokeError instanceof WebConsoleAuthenticationError
        && revokeError.state === "reauthentication_required"
      ) {
        const recentAuthenticationUrl =
          apiClient.getRecentAuthenticationUrl?.(
            "/reauthentication/revoke-other-sessions"
          )
          ?? (
            "/auth/reauthenticate?"
            + "return_to=%2Freauthentication%2Frevoke-other-sessions"
          );
        try {
          globalThis.location.assign(recentAuthenticationUrl);
        } catch {
          setError(
            "Email verification is required before signing out other devices"
          );
        }
      } else {
        setError(
          errorMessage(
            revokeError,
            "Failed to sign out other Device Sessions"
          )
        );
      }
    } finally {
      setIsRevokingOthers(false);
    }
  }

  const otherActiveSessions = sessions.filter(
    (deviceSession) =>
      !deviceSession.is_current
      && deviceSession.status === "active"
  );
  const recoveryMessage =
    recentAuthenticationResult === "cancelled"
      ? "Email verification was cancelled. No other device was signed out."
      : recentAuthenticationResult === "provider_unavailable"
        ? "Email verification is temporarily unavailable. No other device was signed out."
        : recentAuthenticationResult === "failed"
          ? "Email verification could not be completed. No other device was signed out."
          : null;
  const retryUrl =
    apiClient.getRecentAuthenticationUrl?.(
      "/reauthentication/revoke-other-sessions"
    )
    ?? (
      "/auth/reauthenticate?"
      + "return_to=%2Freauthentication%2Frevoke-other-sessions"
    );

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
      {recoveryMessage ? (
        <div role="alert">
          <p>{recoveryMessage}</p>
          <a href={retryUrl}>Try email verification again</a>
        </div>
      ) : null}
      {
        recentAuthenticationResult === "confirmed"
        && !otherSessionsRevoked
        ? (
          <div>
            <h3>Sign out other devices?</h3>
            <p>
              Every other active Device Session will need a fresh email
              verification before it can sign in again.
            </p>
            <button
              type="button"
              disabled={isRevokingOthers || revokingId !== null}
              onClick={() => {
                void handleRevokeOtherSessions();
              }}
            >
              {
                isRevokingOthers
                  ? "Signing out other devices"
                  : "Confirm sign out other devices"
              }
            </button>
          </div>
        )
        : null
      }
      {otherSessionsRevoked ? (
        <p role="status">Other devices were signed out.</p>
      ) : null}
      {
        !isLoading
        && error === null
        && recoveryMessage === null
        && recentAuthenticationResult !== "confirmed"
        && otherActiveSessions.length > 0
        ? (
          <button
            type="button"
            disabled={isRevokingOthers || revokingId !== null}
            onClick={() => {
              void handleRevokeOtherSessions();
            }}
          >
            {
              isRevokingOthers
                ? "Signing out other devices"
                : "Sign out other devices"
            }
          </button>
        )
        : null
      }
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
                disabled={revokingId !== null || isRevokingOthers}
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
