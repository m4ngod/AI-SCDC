import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { ConsoleApiClient } from "../api/client";

describe("App", () => {
  it("renders sidebar, main thread, and right context panel", () => {
    render(<App />);

    expect(screen.getByRole("banner")).toHaveTextContent("AI Company");
    expect(
      within(screen.getByRole("navigation", { name: "Primary" })).getByText("Projects")
    ).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveTextContent("Project command thread");
    expect(screen.getByLabelText("Task context panel")).toHaveTextContent("Agent status");
  });

  it("renders task title, status, and agent", () => {
    render(<App />);

    const board = screen.getByLabelText("Task board");
    expect(within(board).getByText("Implement task board UI")).toBeInTheDocument();
    expect(within(board).getByText("PATCH_READY")).toBeInTheDocument();
    expect(within(board).getByText("Frontend Engineer")).toBeInTheDocument();
  });

  it("submitting a goal calls task creation client", async () => {
    const user = userEvent.setup();
    const createTask = vi.fn<ConsoleApiClient["createTask"]>().mockResolvedValue({
      id: "task_new",
      title: "Build task board",
      status: "CREATED",
      role_required: "frontend",
      assigned_agent: "Frontend Engineer",
      updated_at: "2026-05-29T00:00:00Z"
    });
    const apiClient: ConsoleApiClient = { createTask };

    render(<App apiClient={apiClient} />);

    await user.type(screen.getByLabelText("Goal"), "Build task board");
    await user.click(screen.getByRole("button", { name: "Create task" }));

    expect(createTask).toHaveBeenCalledWith("Build task board");
    expect(await screen.findByText("Build task board")).toBeInTheDocument();
  });
});
