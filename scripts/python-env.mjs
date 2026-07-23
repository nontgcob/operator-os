import { existsSync, readFileSync } from "node:fs";
import { delimiter, join } from "node:path";
import { spawnSync } from "node:child_process";

const root = process.cwd();
const isWindows = process.platform === "win32";
const venvPython = join(root, ".venv", isWindows ? "Scripts/python.exe" : "bin/python");

function repositoryEnv() {
  const envPath = join(root, ".env");
  if (!existsSync(envPath)) return {};
  return Object.fromEntries(
    readFileSync(envPath, "utf8")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith("#") && line.includes("="))
      .map((line) => {
        const separator = line.indexOf("=");
        const key = line.slice(0, separator).trim();
        const value = line.slice(separator + 1).trim().replace(/^(?:"(.*)"|'(.*)')$/, "$1$2");
        return [key, value];
      })
  );
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { stdio: "inherit", cwd: root, ...options });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
  return result.status === 0;
}

const [action, ...args] = process.argv.slice(2);

if (action === "setup") {
  const launcher = isWindows ? "py" : "python3";
  const launcherArgs = isWindows ? ["-3.12", "-m", "venv", ".venv"] : ["-m", "venv", ".venv"];
  if (!existsSync(venvPython) && !run(launcher, launcherArgs)) process.exit(1);
  if (!run(venvPython, ["-m", "pip", "install", "--upgrade", "pip"])) process.exit(1);
  run(venvPython, ["-m", "pip", "install", "-r", "requirements-local.txt"]);
} else if (!existsSync(venvPython)) {
  console.error("Missing .venv. Run npm run setup first.");
  process.exit(1);
} else if (action === "service") {
  const [servicePath, port] = args;
  const env = {
    ...process.env,
    ...repositoryEnv(),
    PYTHONPATH: [root, join(root, servicePath)].join(delimiter),
  };
  run(venvPython, ["-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", port, "--reload"], { env });
} else if (action === "pip") {
  run(venvPython, ["-m", "pip", ...args]);
} else if (action === "pytest") {
  run(venvPython, ["-m", "pytest", ...args]);
} else if (action === "diagnose-sam3") {
  const code = `
from pathlib import Path
import torch, ultralytics
checkpoint = Path("models/sam3.pt").resolve()
print(f"torch: {torch.__version__}")
print(f"CUDA build: {torch.version.cuda}")
print(f"CUDA available: {torch.cuda.is_available()}")
print(f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none'}")
print(f"ultralytics: {ultralytics.__version__}")
print(f"checkpoint: {checkpoint}")
print(f"checkpoint exists: {checkpoint.exists()}")
`;
  run(venvPython, ["-c", code]);
} else {
  console.error(`Unknown python environment action: ${action ?? "(missing)"}`);
  process.exit(1);
}
