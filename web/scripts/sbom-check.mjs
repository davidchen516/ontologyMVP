/**
 * SBOM 一致性检查（issue #34）：sbom.spdx.json 包数 == npm 实际安装树包数。
 * 漂移即失败——依赖变更必须同步 SBOM（license-audit 已查许可）。
 */
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";

const sbom = JSON.parse(readFileSync("sbom.spdx.json", "utf-8"));
const raw = execSync("npm ls --all --json 2>/dev/null || true").toString();
const tree = JSON.parse(raw || "{}");

let count = 0;
const walk = (deps) => {
  for (const meta of Object.values(deps || {})) {
    count++;
    walk(meta.dependencies);
  }
};
walk(tree.dependencies);

if (sbom.packages.length !== count) {
  console.error(
    `SBOM drift: sbom has ${sbom.packages.length} packages, ` +
    `installed tree has ${count}. Regenerate sbom.spdx.json.`,
  );
  process.exit(1);
}
console.log(`SBOM consistent: ${count} packages`);
