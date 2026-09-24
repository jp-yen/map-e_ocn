#!/usr/bin/env node
/**
 * JS syntax validation using Node's `node:vm` Script class.
 *
 * `new Script(src)` compiles the source with the full engine syntax support
 * (ES2022+) and throws on syntax errors, without executing the code. This is
 * the most reliable static check for the dashboard's JavaScript files.
 *
 * CSS validation is handled separately by tools/validate_css.mjs (PostCSS).
 *
 * Usage: node tools/validate.mjs <path-to-js...>
 */
import { Script } from "node:vm";
import { readFileSync } from "node:fs";

const files = process.argv.slice(1);
let allOk = true;

for (const f of files) {
  if (!f.endsWith(".js")) {
    console.log(`SKIP ${f}: not a .js file`);
    continue;
  }
  const src = readFileSync(f, "utf-8");
  try {
    // コンパイル時に構文エラーなら throw（実行はしない）
    new Script(src);
    const lines = src.split("\n").length;
    console.log(`OK   ${f}: JS syntax valid (${lines} lines)`);
  } catch (e) {
    console.log(`FAIL ${f}: JS syntax error: ${e.message}`);
    allOk = false;
  }
}

process.exit(allOk ? 0 : 1);