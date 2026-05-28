import { Ajv2020 } from "ajv/dist/2020.js";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

const root = fileURLToPath(new URL("..", import.meta.url));

function readJson(path: string) {
  return JSON.parse(readFileSync(join(root, path), "utf8"));
}

const schemaNames = [
  "agent-role.schema.json",
  "task-status.schema.json",
  "task-spec.schema.json",
  "patch-result.schema.json",
  "review-result.schema.json",
  "debug-result.schema.json",
  "tool-call.schema.json",
  "tool-permission.schema.json"
];

function createAjv() {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  ajv.addSchema(readJson("schemas/agent-role.schema.json"), "agent-role.schema.json");
  ajv.addSchema(readJson("schemas/task-status.schema.json"), "task-status.schema.json");
  return ajv;
}

function validatorFor(schemaName: string) {
  const ajv = createAjv();
  return ajv.getSchema(schemaName) ?? ajv.compile(readJson(`schemas/${schemaName}`));
}

describe("agent protocol schemas", () => {
  test.each(schemaNames)("%s compiles under strict Ajv2020", (schemaName) => {
    expect(() => validatorFor(schemaName)).not.toThrow();
  });

  test("TaskSpec sample validates", () => {
    const validate = validatorFor("task-spec.schema.json");
    const sample = readJson("samples/task-spec.sample.json");
    expect(validate(sample), JSON.stringify(validate.errors)).toBe(true);
  });

  test("PatchResult sample validates", () => {
    const validate = validatorFor("patch-result.schema.json");
    const sample = readJson("samples/patch-result.sample.json");
    expect(validate(sample), JSON.stringify(validate.errors)).toBe(true);
  });

  test("ReviewResult sample validates", () => {
    const validate = validatorFor("review-result.schema.json");
    const sample = readJson("samples/review-result.sample.json");
    expect(validate(sample), JSON.stringify(validate.errors)).toBe(true);
  });

  test("TaskSpec requires acceptance criteria", () => {
    const validate = validatorFor("task-spec.schema.json");
    const invalid = {
      title: "Implement task board UI",
      role_required: "frontend",
      objective: "Show active agent tasks.",
      allowed_paths: ["apps/desktop/**"],
      required_tests: ["TaskBoard renders"],
      risk_level: "medium"
    };
    expect(validate(invalid)).toBe(false);
  });

  test("DebugResult accepts valid results and rejects invalid status", () => {
    const validate = validatorFor("debug-result.schema.json");
    const valid = {
      root_cause: "The task stream ignored PATCH_READY events.",
      fix_summary: "Handled PATCH_READY in the state reducer.",
      tests_run: ["pnpm --filter @ai-scdc/desktop test"],
      status: "fixed"
    };
    const invalid = { ...valid, status: "inconclusive" };

    expect(validate(valid), JSON.stringify(validate.errors)).toBe(true);
    expect(validate(invalid)).toBe(false);
  });

  test("ToolCall accepts valid calls and rejects extra error message", () => {
    const validate = validatorFor("tool-call.schema.json");
    const valid = {
      tool_name: "shell_command",
      input_json: { command: "pnpm test" },
      output_json: { exit_code: 0 },
      status: "succeeded",
      risk_level: "low"
    };
    const invalid = { ...valid, error_message: "not allowed by the protocol" };

    expect(validate(valid), JSON.stringify(validate.errors)).toBe(true);
    expect(validate(invalid)).toBe(false);
  });

  test("ToolPermission accepts valid permissions and rejects allowed paths", () => {
    const validate = validatorFor("tool-permission.schema.json");
    const valid = {
      tool_name: "shell_command",
      permission_level: "write",
      requires_approval: true,
      risk_level: "medium"
    };
    const invalid = { ...valid, allowed_paths: ["packages/agent-protocol/**"] };

    expect(validate(valid), JSON.stringify(validate.errors)).toBe(true);
    expect(validate(invalid)).toBe(false);
  });

  test("TaskStatus accepts known statuses and rejects unknown statuses", () => {
    const validate = validatorFor("task-status.schema.json");

    expect(validate("PATCH_READY"), JSON.stringify(validate.errors)).toBe(true);
    expect(validate("BLOCKED")).toBe(false);
  });
});
