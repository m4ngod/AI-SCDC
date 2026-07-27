import type { ReactNode } from "react";

type ShellProps = {
  children: ReactNode;
  contextPanel: ReactNode;
  accountName?: string;
  workspaceName?: string;
  onSignOut?: () => void;
  isSigningOut?: boolean;
  signOutError?: string | null;
};

export function Shell({
  children,
  contextPanel,
  accountName,
  workspaceName,
  onSignOut,
  isSigningOut = false,
  signOutError
}: ShellProps) {
  return (
    <div className="console-shell">
      <header className="topbar" role="banner">
        <div className="brand">AI Company</div>
        <div className="topbar-meta" aria-label="Workspace metadata">
          {accountName ? <span>{accountName}</span> : null}
          <span>{workspaceName ?? "Demo Workspace"}</span>
          <span>Demo Project</span>
          <span>main</span>
          <span>Local Runner: Mock</span>
          <span>Cost: $0.00</span>
          <button type="button">Settings</button>
          {onSignOut ? (
            <button
              type="button"
              disabled={isSigningOut}
              onClick={onSignOut}
            >
              {isSigningOut ? "Signing out" : "Sign out"}
            </button>
          ) : null}
          {signOutError ? <span role="alert">{signOutError}</span> : null}
        </div>
      </header>
      <nav className="sidebar" aria-label="Primary">
        <a href="#workspace" aria-current="page">
          Workspace
        </a>
        <a href="#projects">
          Projects
        </a>
        <a href="#conversations">Conversations</a>
        <a href="#agents">Agents</a>
        <a href="#approvals">Approvals</a>
        <a href="#settings">Settings</a>
      </nav>
      <main className="workspace">{children}</main>
      <aside className="context-panel" aria-label="Task context panel">
        {contextPanel}
      </aside>
    </div>
  );
}
