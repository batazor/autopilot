#!/usr/bin/env node
// SVAR @svar-ui/* packages publish a broken `package.json` where
// `exports[".".require]` and `main` point at `./dist/index.cjs.js`, but the
// file is actually `./dist/index.cjs`. Next.js/webpack chokes on this during
// the server-component pass. Rewrite the paths to the file that exists.
//
// Idempotent — safe to re-run; logs nothing when there's nothing to fix.

import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = new URL("../node_modules/@svar-ui/", import.meta.url).pathname;

function walkSvarPackages(root) {
  let entries;
  try {
    entries = readdirSync(root);
  } catch {
    return [];
  }
  const out = [];
  for (const name of entries) {
    const pkgDir = join(root, name);
    const pkgJson = join(pkgDir, "package.json");
    try {
      if (statSync(pkgJson).isFile()) out.push(pkgJson);
    } catch {
      // not a package dir
    }
    // npm hoists most deps, but @svar-ui ships pinned peers in nested
    // node_modules/@svar-ui/* — walk those too so the patch reaches them.
    const nested = join(pkgDir, "node_modules", "@svar-ui");
    out.push(...walkSvarPackages(nested));
  }
  return out;
}

let touched = 0;
for (const path of walkSvarPackages(ROOT)) {
  const raw = readFileSync(path, "utf8");
  const pkgDir = join(path, "..");
  let fixed = raw;

  // (1) `exports.require` / `main` point at `./dist/index.cjs.js` but the file
  // is `./dist/index.cjs`. Rewrite when the .cjs target actually exists.
  if (fixed.includes(".cjs.js")) {
    let cjsExists = false;
    try {
      cjsExists = statSync(join(pkgDir, "dist", "index.cjs")).isFile();
    } catch {
      // not present — skip the cjs rewrite for this package
    }
    if (cjsExists) {
      fixed = fixed.replaceAll('"./dist/index.cjs.js"', '"./dist/index.cjs"');
    }
  }

  // (2) `types` / `exports[".".types]` advertise `./types/index.d.ts` but the
  // file ships missing (see @svar-ui/react-calendar 2.6.1). TS errors with
  // "Could not find a declaration file" instead of falling back to the
  // project's ambient `declare module` shim. Drop the broken declarations.
  const advertisesTypes = fixed.includes('"./types/index.d.ts"');
  let typesExist = false;
  if (advertisesTypes) {
    try {
      typesExist = statSync(join(pkgDir, "types", "index.d.ts")).isFile();
    } catch {
      // ditto — file genuinely missing
    }
    if (!typesExist) {
      fixed = fixed
        .replaceAll(/^\s*"types":\s*"\.\/types\/index\.d\.ts",?\s*$/gm, "")
        .replaceAll(/^\s*"types":\s*"\.\/types\/index\.d\.ts"\s*$/gm, "");
    }
  }

  if (fixed === raw) continue;
  writeFileSync(path, fixed);
  touched += 1;
  console.log(`fix-svar-exports: patched ${path.replace(ROOT, "@svar-ui/")}`);
}
if (touched === 0) {
  // silent — keeps `npm install` quiet on clean runs
}
