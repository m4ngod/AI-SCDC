import { afterEach, describe, expect, it, vi } from "vitest";
import { createHttpApiClient, fakeApiClient } from "../api/client";

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    ...init
  });
}

describe("desktop API clients", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fake client creates the deterministic demo task", async () => {
    await expect(fakeApiClient.createTask("Ship the real goal")).resolves.toMatchObject({
      id: "task_demo_created",
      title: "Build task board",
      status: "CREATED",
      role_required: "frontend",
      assigned_agent: "Frontend Engineer",
      updated_at: "2026-05-29T00:00:00Z"
    });
  });

  it("fake client lists the deterministic demo board", async () => {
    await expect(fakeApiClient.listTasks()).resolves.toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "task_board_ui",
          title: "Implement task board UI",
          status: "PATCH_READY"
        })
      ])
    );
  });

  it("HTTP client lists mapped tasks for the resolved project", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse([{ id: "project_demo" }]))
      .mockResolvedValueOnce(
        jsonResponse([
          {
            id: "task_api_persisted",
            title: "Persisted API task",
            status: "REVIEWING",
            role_required: "backend",
            created_at: "2026-05-29T01:00:00Z"
          }
        ])
      );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({ baseUrl: "http://127.0.0.1:8000/" });
    const tasks = await client.listTasks();

    expect(fetchMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:8000/projects");
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:8000/projects/project_demo/tasks"
    );
    expect(tasks).toEqual([
      {
        id: "task_api_persisted",
        title: "Persisted API task",
        status: "REVIEWING",
        role_required: "backend",
        assigned_agent: "Backend Engineer",
        updated_at: "2026-05-29T01:00:00Z"
      }
    ]);
  });

  it("HTTP client formats FastAPI JSON detail errors", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse(
        {
          detail: {
            message: "Project not found"
          }
        },
        { status: 404, statusText: "Not Found" }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({
      baseUrl: "http://127.0.0.1:8000/",
      projectId: "project_missing"
    });

    await expect(client.listTasks()).rejects.toThrow(
      "GET /projects/project_missing/tasks failed with 404 Not Found: Project not found"
    );
  });

  it("HTTP client creates a demo project when none exists and posts a mapped task", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(
        jsonResponse(
          {
            id: "project_demo",
            name: "Demo Project",
            description: "Phase 0 desktop integration project"
          },
          { status: 201 }
        )
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            id: "task_api_1",
            title: "Connect the desktop",
            status: "CREATED",
            role_required: "frontend",
            created_at: "2026-05-29T01:00:00Z",
            updated_at: "2026-05-29T01:05:00Z"
          },
          { status: 201 }
        )
      );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({ baseUrl: "http://127.0.0.1:8000/" });
    const task = await client.createTask("Connect the desktop");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:8000/projects");
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:8000/projects",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          name: "Demo Project",
          description: "Phase 0 desktop integration project"
        })
      })
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "http://127.0.0.1:8000/projects/project_demo/tasks",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          title: "Connect the desktop",
          description: "Created from desktop shell.",
          role_required: "frontend",
          risk_level: "medium",
          acceptance_criteria: ["Task is visible in the desktop context panel."]
        })
      })
    );
    expect(task).toEqual({
      id: "task_api_1",
      title: "Connect the desktop",
      status: "CREATED",
      role_required: "frontend",
      assigned_agent: "Frontend Engineer",
      updated_at: "2026-05-29T01:05:00Z"
    });
  });
});
