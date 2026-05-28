import type { ReactNode } from "react";

type ShellProps = {
  children: ReactNode;
  contextPanel: ReactNode;
};

export function Shell({ children, contextPanel }: ShellProps) {
  return (
    <div className="console-shell">
      <header className="topbar" role="banner">
        <div className="brand">AI Company</div>
        <div className="topbar-meta">Phase 0 Console</div>
      </header>
      <nav className="sidebar" aria-label="Primary">
        <a href="#projects" aria-current="page">
          Projects
        </a>
        <a href="#agents">Agents</a>
        <a href="#runs">Runs</a>
        <a href="#settings">Settings</a>
      </nav>
      <main className="workspace">{children}</main>
      <aside className="context-panel" aria-label="Task context panel">
        {contextPanel}
      </aside>
    </div>
  );
}
