#!/usr/bin/env node
/**
 * 前端依赖许可与供应链审计（#30 证据；CI web job 调用）。
 *
 * - 读取实际安装树（node_modules 下各包 package.json）而非 lockfile 声明；
 * - 允许清单：宽松宽松的宽松集合（MIT/ISC/BSD/Apache-2.0/0BSD/Python-2.0/
 *   MPL-2.0/CC-BY-4.0 等宽松许可证）；
 * - 非宽松或无法判定 → 退出码 1（豁免必须在本文件登记理由）。
 */
import fs from "node:fs";
import path from "node:path";

// 允许的许可证标识（宽松集合）
const ALLOWED = new Set([
  "MIT",
  "MIT-0",
  "ISC",
  "Apache-2.0",
  "BSD-2-Clause",
  "BSD-3-Clause",
  "0BSD",
  "Python-2.0",
  "Python-2.0.1",
  "MPL-2.0",
  "CC-BY-4.0",
  "CC-BY-3.0",
  "Unlicense",
  "Zlib",
  "BlueOak-1.0.0",
]);

// 登记豁免（package@version → 理由；新增必须写理由）
const WAIVERS = new Map([
  // （无）
]);

function readLicense(pkgDir) {
  try {
    const pkg = JSON.parse(
      fs.readFileSync(path.join(pkgDir, "package.json"), "utf8"),
    );
    if (typeof pkg.license === "string") return pkg.license;
    if (Array.isArray(pkg.licenses) && pkg.licenses[0]) {
      return pkg.licenses[0].type ?? pkg.licenses[0];
    }
    if (pkg.license && typeof pkg.license === "object") {
      return pkg.license.type ?? "UNKNOWN";
    }
    return "UNKNOWN";
  } catch {
    return "UNKNOWN";
  }
}

function collect(nodeModules, prefix = "") {
  const found = [];
  let entries;
  try {
    entries = fs.readdirSync(nodeModules, { withFileTypes: true });
  } catch {
    return found;
  }
  for (const entry of entries) {
    // 跳过工具缓存与元数据目录（非依赖）
    if (
      entry.name === ".bin" ||
      entry.name === ".package-lock.json" ||
      entry.name.startsWith(".vite") ||
      entry.name.startsWith(".cache")
    ) {
      continue;
    }
    const full = path.join(nodeModules, entry.name);
    if (entry.name.startsWith("@")) {
      found.push(...collect(full, `${prefix}${entry.name}/`));
      continue;
    }
    if (!entry.isDirectory()) continue;
    const name = `${prefix}${entry.name}`;
    const license = readLicense(full);
    let version = "?";
    try {
      version = JSON.parse(
        fs.readFileSync(path.join(full, "package.json"), "utf8"),
      ).version;
    } catch {
      /* 忽略 */
    }
    found.push({ name, version, license });
    // 嵌套依赖
    found.push(...collect(path.join(full, "node_modules"), ""));
  }
  return found;
}

const packages = collect(path.resolve("node_modules"));
const violations = [];
for (const pkg of packages) {
  const id = `${pkg.name}@${pkg.version}`;
  if (WAIVERS.has(id) || WAIVERS.has(`${pkg.name}@*`)) continue;
  if (!ALLOWED.has(pkg.license)) {
    violations.push(`${id}: ${pkg.license}`);
  }
}

const byLicense = {};
for (const pkg of packages) {
  byLicense[pkg.license] = (byLicense[pkg.license] || 0) + 1;
}

console.log(`audited ${packages.length} packages`);
for (const [license, count] of Object.entries(byLicense).sort(
  (a, b) => b[1] - a[1],
)) {
  console.log(`  ${license}: ${count}`);
}

if (violations.length > 0) {
  console.error(`\ndisallowed/unknown licenses (${violations.length}):`);
  for (const v of violations.slice(0, 30)) console.error(`  ${v}`);
  if (violations.length > 30) {
    console.error(`  ... and ${violations.length - 30} more`);
  }
  process.exit(1);
}
console.log("license audit passed");
