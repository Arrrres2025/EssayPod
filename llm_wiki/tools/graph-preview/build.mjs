import { build } from "esbuild";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

await build({
  entryPoints: [resolve(here, "../../llm_wiki/web/graph-src.js")],
  bundle: true,
  format: "esm",
  minify: true,
  nodePaths: [resolve(here, "node_modules")],
  outfile: resolve(here, "../../llm_wiki/web/graph.js"),
});
