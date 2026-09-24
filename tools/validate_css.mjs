#!/usr/bin/env node
/**
 * CSS validation using the PostCSS spec-compliant parser.
 *
 * Notes on parser choice:
 *  - `postcss.parse()` throws `CssSyntaxError` on structurally broken CSS
 *    (unclosed blocks, bad at-rule declarations ...). That is exactly what a
 *    validator needs.
 *  - `postcss-safe-parser` is fault-tolerant BY DESIGN (parses anything,
 *    never reports errors), so it is NOT suitable for validation. We keep
 *    it installed only if this project later needs to parse hostile CSS.
 *
 * Usage: node tools/validate_css.mjs <path-to-css...>
 */
import fs from "node:fs";
import postcss from "postcss";

const files = process.argv.slice(1);
let allOk = true;

for (const f of files) {
  if (!f.endsWith(".css")) {
    console.log(`SKIP ${f}: not a .css file`);
    continue;
  }
  const css = fs.readFileSync(f, "utf-8");
  try {
    postcss.parse(css, { from: f });
    console.log(`OK   ${f}: CSS valid via PostCSS`);
  } catch (e) {
    const first = e.message.split("\n")[0];
    console.log(`FAIL ${f}: ${first}`);
    allOk = false;
  }
}

process.exit(allOk ? 0 : 1);