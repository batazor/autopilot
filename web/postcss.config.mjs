import path from "node:path";
import { fileURLToPath } from "node:url";

/** Always scan `web/` for class names (not the repo root when invoked via `npm --prefix web`). */
const webRoot = path.dirname(fileURLToPath(import.meta.url));

const config = {
  plugins: {
    "@tailwindcss/postcss": {
      base: webRoot,
    },
  },
};

export default config;
