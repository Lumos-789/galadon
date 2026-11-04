import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { apply, inject, name } from "../index.js";

function createAgent(registrations) {
  let active = true;
  const ctx = {
    inject(services, callback) {
      assert.deepEqual(services, ["skills"]);
      const disposers = [];
      callback({
        skills: {
          register(skill) {
            const registration = { skill, disposed: 0 };
            registrations.push(registration);
            const dispose = () => {
              registration.disposed += 1;
            };
            disposers.push(dispose);
            return dispose;
          },
        },
      });
      return {
        dispose() {
          if (!active) return;
          active = false;
          for (const dispose of disposers.reverse()) dispose();
        },
      };
    },
  };
  return { ctx };
}

function createHarness(agents = []) {
  const hostRegistrations = [];
  const listeners = new Map();
  const cleanups = [];
  const context = {
    skills: {
      register(skill) {
        hostRegistrations.push(skill);
      },
    },
    agents: {
      list() {
        return [...agents];
      },
    },
    on(event, listener) {
      const eventListeners = listeners.get(event) ?? [];
      eventListeners.push(listener);
      listeners.set(event, eventListeners);
      return () => {
        listeners.set(
          event,
          (listeners.get(event) ?? []).filter((item) => item !== listener),
        );
      };
    },
    effect(factory) {
      const dispose = factory();
      cleanups.push(dispose);
      return dispose;
    },
  };

  return {
    context,
    hostRegistrations,
    emit(event, payload) {
      for (const listener of listeners.get(event) ?? []) listener(payload);
    },
    dispose() {
      for (const cleanup of cleanups.reverse()) cleanup();
    },
  };
}

test("registers the bundled galadon skill in host and agents", () => {
  const agentRegistrations = [];
  const agent = createAgent(agentRegistrations);
  const harness = createHarness([agent]);

  apply(harness.context);

  assert.equal(name, "galadon");
  assert.deepEqual(inject, ["skills", "agents"]);
  assert.equal(harness.hostRegistrations.length, 1);
  assert.equal(agentRegistrations.length, 1);
  assert.equal(harness.hostRegistrations[0].name, "galadon");
  assert.match(harness.hostRegistrations[0].description, /five-stage/);
  assert.equal(harness.hostRegistrations[0].resourceBase.kind, "directory");

  harness.emit("agent/disposed", { agent });
  assert.equal(agentRegistrations[0].disposed, 1);
  harness.dispose();
});

test("manifest versions stay in sync", () => {
  const manifest = JSON.parse(
    readFileSync(new URL("../.zcode-plugin/plugin.json", import.meta.url), "utf8"),
  );
  const packageJson = JSON.parse(
    readFileSync(new URL("../package.json", import.meta.url), "utf8"),
  );
  const codex = JSON.parse(
    readFileSync(new URL("../.codex-plugin/plugin.json", import.meta.url), "utf8"),
  );
  assert.equal(manifest.skills, "skills");
  assert.equal(manifest.version, packageJson.version);
  assert.equal(codex.version, packageJson.version);
});

test("npm tarball excludes Python bytecode", () => {
  const repo = fileURLToPath(new URL("..", import.meta.url));
  const stdout = execFileSync("npm", ["pack", "--dry-run", "--json"], {
    cwd: repo,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  const [pack] = JSON.parse(stdout);
  const bytecode = pack.files
    .map((file) => file.path)
    .filter((path) => path.endsWith(".pyc") || path.split("/").includes("__pycache__"));
  assert.deepEqual(bytecode, []);
});

test("release version and changelog are bumped together", () => {
  const packageJson = JSON.parse(
    readFileSync(new URL("../package.json", import.meta.url), "utf8"),
  );
  assert.equal(packageJson.version, "0.3.0");

  const changelog = readFileSync(new URL("../CHANGELOG.md", import.meta.url), "utf8");
  assert.match(changelog, /^## 0\.3\.0$/m);

  const pyproject = readFileSync(new URL("../pyproject.toml", import.meta.url), "utf8");
  assert.match(pyproject, /^version = "0\.3\.0"$/m);

  const pythonPackage = readFileSync(new URL("../src/galadon/__init__.py", import.meta.url), "utf8");
  assert.match(pythonPackage, /^__version__ = "0\.3\.0"$/m);
});
