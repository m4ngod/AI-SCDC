import { Ajv } from "ajv/dist/ajv.js";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

const root = fileURLToPath(new URL("..", import.meta.url));

function readJson(path: string) {
  return JSON.parse(readFileSync(join(root, path), "utf8"));
}

function validatorFor(schemaName: string) {
  const ajv = new Ajv({ allErrors: true, strict: true });
  ajv.addSchema(readJson("schemas/agent-role.schema.json"), "agent-role.schema.json");
  ajv.addSchema(readJson("schemas/task-status.schema.json"), "task-status.schema.json");
  return ajv.compile(readJson(`schemas/${schemaName}`));
}

describe("agent protocol schemas", () => {
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
});
