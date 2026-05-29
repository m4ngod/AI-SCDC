import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { ConsoleApiClient } from "../api/client";

describe("App", () => {
  it("renders sidebar, main thread, and right context panel", () => {
    render(<App />);

    const topbar = screen.getByRole("banner");
    for (const item of [
      "AI Company",
      "Demo Workspace",
      "Demo Project",
      "main",
      "Local Runner: Mock",
      "Cost: $0.00",
      "Settings"
    ]) {
      expect(topbar).toHaveTextContent(item);
    }
    const primaryNav = screen.getByRole("navigation", { name: "Primary" });
    for (const item of [
      "Workspace",
      "Projects",
      "Conversations",
      "Agents",
      "Approvals",
      "Settings"
    ]) {
      expect(within(primaryNav).getByText(item)).toBeInTheDocument();
    }
    expect(within(primaryNav).queryByText("Runs")).not.toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveTextContent("Project command thread");
    expect(screen.getByLabelText("Task context panel")).toHaveTextContent("Agent status");
  });

  it("renders task title, status, and agent", async () => {
    render(<App />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    expect(await within(board).findByText("Implement task board UI")).toBeInTheDocument();
    expect(await within(board).findByText("PATCH_READY")).toBeInTheDocument();
    expect(await within(board).findByText("Frontend Engineer")).toBeInTheDocument();
    expect(within(screen.getByRole("main")).queryByLabelText("Task board")).not.toBeInTheDocument();
  });

  it("loads the initial task board from the API client", async () => {
    const apiClient: ConsoleApiClient = {
      listTasks: vi.fn().mockResolvedValue([
        {
          id: "task_api_persisted",
          title: "Persisted API task",
          status: "REVIEWING",
          role_required: "backend",
          assigned_agent: "Backend Engineer",
          updated_at: "2026-05-29T01:00:00Z"
        }
      ]),
      createTask: vi.fn(),
      createPlannerRun: vi.fn(),
      approvePlannerRun: vi.fn(),
      rejectPlannerRun: vi.fn()
    };

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    expect(await within(contextPanel).findByText("Persisted API task")).toBeInTheDocument();
    expect(within(contextPanel).queryByText("Implement task board UI")).not.toBeInTheDocument();
    expect(apiClient.listTasks).toHaveBeenCalledOnce();
  });

  it("shows initial task loading errors in the context panel", async () => {
    const apiClient: ConsoleApiClient = {
      listTasks: vi.fn().mockRejectedValue(new Error("API unavailable")),
      createTask: vi.fn(),
      createPlannerRun: vi.fn(),
      approvePlannerRun: vi.fn(),
      rejectPlannerRun: vi.fn()
    };

    render(<App apiClient={apiClient} />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("API unavailable");
  });

  it("submitting a goal with the fake client creates the deterministic demo task", async () => {
    const user = userEvent.setup();

    render(<App />);

    await user.type(screen.getByLabelText("Goal"), "Any non-empty goal");
    await user.click(screen.getByRole("button", { name: "Create task" }));

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    expect(await within(contextPanel).findByText("Build task board")).toBeInTheDocument();
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
    const apiClient: ConsoleApiClient = {
      listTasks: vi.fn().mockResolvedValue([]),
      createTask,
      createPlannerRun: vi.fn(),
      approvePlannerRun: vi.fn(),
      rejectPlannerRun: vi.fn()
    };

    render(<App apiClient={apiClient} />);

    await user.type(screen.getByLabelText("Goal"), "Build task board");
    await user.click(screen.getByRole("button", { name: "Create task" }));

    expect(createTask).toHaveBeenCalledWith("Build task board");
    expect(await screen.findByText("Build task board")).toBeInTheDocument();
  });

  it("shows task creation errors inline and clears them after a successful submission", async () => {
    const user = userEvent.setup();
    const createTask = vi
      .fn<ConsoleApiClient["createTask"]>()
      .mockRejectedValueOnce(new Error("API unavailable"))
      .mockResolvedValueOnce({
        id: "task_recovered",
        title: "Recovered task",
        status: "CREATED",
        role_required: "frontend",
        assigned_agent: "Frontend Engineer",
        updated_at: "2026-05-29T00:00:00Z"
      });
    const apiClient: ConsoleApiClient = {
      listTasks: vi.fn().mockResolvedValue([]),
      createTask,
      createPlannerRun: vi.fn(),
      approvePlannerRun: vi.fn(),
      rejectPlannerRun: vi.fn()
    };

    render(<App apiClient={apiClient} />);

    await user.type(screen.getByLabelText("Goal"), "Build when offline");
    await user.click(screen.getByRole("button", { name: "Create task" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("API unavailable");

    await user.clear(screen.getByLabelText("Goal"));
    await user.type(screen.getByLabelText("Goal"), "Build after recovery");
    await user.click(screen.getByRole("button", { name: "Create task" }));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(await screen.findByText("Recovered task")).toBeInTheDocument();
  });
});
