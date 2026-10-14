/**
 * Reads a HyperFrames composition and prints its timeline structure as one
 * JSON document on stdout. Deliberately dumb: it extracts, it does not
 * decide. Flattening, track mapping, frame quantization and every lint rule
 * live in Python where they are unit-tested without node.
 *
 * Why it exists: `@hyperframes/parsers` is TypeScript in a separate
 * checkout, and reimplementing the `data-*` timing contract in Python would
 * drift from upstream silently. Shelling out keeps one source of truth.
 *
 * Three things about the parsers package shape this file, all verified
 * against v0.7.85 rather than assumed:
 *
 *   1. The package is NOT resolvable by name. `node_modules/@hyperframes/`
 *      in the hyperframes checkout links only `player`, so `import
 *      "@hyperframes/parsers"` throws ERR_MODULE_NOT_FOUND. Entry points
 *      are imported by absolute path into `packages/parsers/dist/`.
 *   2. `parseHtml` needs a global `DOMParser` and does not provide one -
 *      it throws ReferenceError without it. linkedom is resolved out of the
 *      hyperframes checkout (createRequire against its package.json, so the
 *      resolution base is that repo and not this script's directory) and
 *      installed on globalThis, mirroring packages/cli/src/utils/dom.ts.
 *   3. `parseHtml` runs `ensureHfIds` on an internal copy and returns the
 *      minted ids. Any second pass over the raw HTML would therefore key on
 *      ids the first pass never saw. This file calls `ensureHfIds` once up
 *      front and runs both passes over that same normalized string.
 *
 * `dist/` is a build artifact of the other checkout: a clean there breaks
 * this bridge, which is why the failure is reported as a typed error rather
 * than an unhandled import throw.
 */

import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const MAX_DEPTH = 20;

function fail(code, message, extra) {
  process.stdout.write(JSON.stringify({ status: "ERROR", code, message, ...(extra || {}) }));
  process.exit(0); // The payload carries the failure; a non-zero exit would hide it.
}

function readJsonArg() {
  const raw = process.argv[2];
  if (!raw) fail("BRIDGE_BAD_INVOCATION", "Expected a JSON argument.");
  try {
    return JSON.parse(raw);
  } catch (error) {
    fail("BRIDGE_BAD_INVOCATION", `Argument is not valid JSON: ${error.message}`);
  }
}

async function loadParsers(repoRoot) {
  const distDir = path.join(repoRoot, "packages", "parsers", "dist");
  const indexPath = path.join(distDir, "index.js");
  const hfIdsPath = path.join(distDir, "hfIds.js");
  for (const required of [indexPath, hfIdsPath]) {
    if (!fs.existsSync(required)) {
      fail(
        "BRIDGE_PARSERS_UNBUILT",
        `${required} is missing. The HyperFrames checkout has not been built ` +
          `(or was cleaned). Run 'bun run build' in ${repoRoot}.`,
      );
    }
  }

  // Resolve against the parsers package, not the repo root: bun installs
  // per-package rather than hoisting, so linkedom sits in
  // packages/parsers/node_modules and is invisible from the root. It is
  // parsers' own dependency, so this is also the honest resolution base.
  const parsersPackageJson = path.join(repoRoot, "packages", "parsers", "package.json");
  const require = createRequire(parsersPackageJson);
  let linkedomPath;
  try {
    linkedomPath = require.resolve("linkedom");
  } catch {
    fail(
      "BRIDGE_LINKEDOM_MISSING",
      `linkedom is not resolvable from ${parsersPackageJson}. Run 'bun install' in ${repoRoot}.`,
    );
  }
  const { DOMParser } = await import(pathToFileURL(linkedomPath).href);
  if (typeof globalThis.DOMParser === "undefined") globalThis.DOMParser = DOMParser;

  const parsers = await import(pathToFileURL(indexPath).href);
  const { ensureHfIds } = await import(pathToFileURL(hfIdsPath).href);
  // `resolution` is a named preset ("landscape"/"portrait"/...), not pixels.
  // Resolve it here against the parsers' own table so the pixel dimensions
  // can never disagree with upstream.
  const canvasDimensions = parsers.CANVAS_DIMENSIONS || null;

  let version = null;
  try {
    version = require(parsersPackageJson).version;
  } catch {
    /* version is informational; a missing package.json is not fatal here */
  }
  return {
    parseHtml: parsers.parseHtml,
    parseGsapScript: parsers.parseGsapScript,
    ensureHfIds,
    canvasDimensions,
    version,
  };
}

/**
 * Attributes parseHtml does not surface. Its allowlist is fixed, so
 * data-treatment - the entire input to the treatment lint - is invisible to
 * it and has to be read separately.
 */
function visibleText(el) {
  // textContent alone swallows any inline <script>/<style> the element
  // wraps - on a composition root that means the whole GSAP source ends up
  // labelled as the clip's copy. Strip those from a clone first.
  let source = el;
  try {
    const clone = el.cloneNode(true);
    for (const node of clone.querySelectorAll("script, style")) node.remove();
    source = clone;
  } catch {
    /* fall back to the live node; a bit of script text beats no text */
  }
  const text = (source.textContent || "").replace(/\s+/g, " ").trim();
  return text ? text.slice(0, 400) : null;
}

function readCustomAttributes(normalizedHtml) {
  const doc = new globalThis.DOMParser().parseFromString(normalizedHtml, "text/html");
  const records = [];
  for (const el of doc.querySelectorAll("[data-start]")) {
    records.push({
      id: el.getAttribute("id") || null,
      hfId: el.getAttribute("data-hf-id") || null,
      tag: (el.tagName || "").toLowerCase(),
      treatment: el.getAttribute("data-treatment") || null,
      beat: el.getAttribute("data-beat") || null,
      // A composition root carries data-start too, so parseHtml returns it
      // as an element alongside the real clips. Surfacing its id is what
      // lets the flattener drop it instead of treating the whole document
      // as one beat.
      compositionId: el.getAttribute("data-composition-id") || null,
      compositionSrc: el.getAttribute("data-composition-src") || null,
      textContent: visibleText(el),
    });
  }
  return records;
}

/** fps lives in hyperframes.json, not in the composition. */
function findProjectFps(startDir) {
  let dir = startDir;
  for (let i = 0; i < 8; i += 1) {
    const candidate = path.join(dir, "hyperframes.json");
    if (fs.existsSync(candidate)) {
      try {
        const parsed = JSON.parse(fs.readFileSync(candidate, "utf8"));
        return { fps: parsed.fps ?? null, configPath: candidate };
      } catch {
        return { fps: null, configPath: candidate, configUnreadable: true };
      }
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return { fps: null, configPath: null };
}

async function main() {
  const args = readJsonArg();
  const htmlPath = args.htmlPath;
  const repoRoot = args.repoRoot;
  if (!htmlPath || !repoRoot) {
    fail("BRIDGE_BAD_INVOCATION", "htmlPath and repoRoot are required.");
  }
  if (!fs.existsSync(htmlPath)) {
    fail("BRIDGE_HTML_NOT_FOUND", `No composition at ${htmlPath}`);
  }

  const { parseHtml, parseGsapScript, ensureHfIds, canvasDimensions, version } =
    await loadParsers(repoRoot);
  const maxDepth = Number.isInteger(args.maxDepth) ? args.maxDepth : MAX_DEPTH;

  const documents = [];
  const visited = new Map(); // resolved path -> index in documents
  const warnings = [];

  // Breadth-first over sub-compositions. The visited map is keyed by
  // resolved path and spans files, which parseHtml's own cycle guard does
  // not: its guard is per-document, so an A -> B -> A chain across files
  // would recurse forever without this.
  const queue = [{ filePath: path.resolve(htmlPath), depth: 0, referencedBy: null }];
  while (queue.length) {
    const { filePath, depth, referencedBy } = queue.shift();
    if (visited.has(filePath)) continue;
    if (depth > maxDepth) {
      warnings.push({ code: "SUBCOMPOSITION_DEPTH_EXCEEDED", filePath, depth, referencedBy });
      continue;
    }
    if (!fs.existsSync(filePath)) {
      warnings.push({ code: "SUBCOMPOSITION_NOT_FOUND", filePath, referencedBy });
      continue;
    }

    let parsed;
    let normalized;
    try {
      normalized = ensureHfIds(fs.readFileSync(filePath, "utf8"));
      parsed = parseHtml(normalized);
    } catch (error) {
      warnings.push({ code: "COMPOSITION_PARSE_FAILED", filePath, message: String(error && error.message) });
      continue;
    }

    let animations = [];
    let gsapError = null;
    if (parsed.gsapScript) {
      try {
        animations = parseGsapScript(parsed.gsapScript).animations || [];
      } catch (error) {
        gsapError = String(error && error.message);
        warnings.push({ code: "GSAP_PARSE_FAILED", filePath, message: gsapError });
      }
    }

    visited.set(filePath, documents.length);
    const dir = path.dirname(filePath);
    const customAttributes = readCustomAttributes(normalized);

    // `element.type === "composition"` is NOT a reliable way to find sub
    // compositions: getElementType only returns "composition" for an
    // explicit data-type="composition", and the documented authoring shape
    // (a bare <div data-composition-id data-composition-src>) has no
    // data-type, so it is typed "text" like any other div. The attribute is
    // the truth, so both the recursion and the rebuild-band classification
    // key off data-composition-src rather than the parsed type.
    const customById = new Map();
    for (const record of customAttributes) {
      if (record.hfId) customById.set(record.hfId, record);
      if (record.id) customById.set(record.id, record);
    }

    const elements = parsed.elements.map((element) => {
      const record = { ...element };
      const custom = customById.get(element.id) || null;
      const src = (custom && custom.compositionSrc) || (element.type === "composition" ? element.src : null);
      if (src) {
        record.isSubComposition = true;
        record.resolvedSrc = path.resolve(dir, src);
      }
      // The document's own root carries data-start/data-duration and so
      // comes back as an element; it is the container, not a beat.
      if (custom && custom.compositionId && !src) record.isCompositionRoot = true;
      return record;
    });

    const resolution = parsed.resolution || null;
    documents.push({
      filePath,
      depth,
      referencedBy,
      resolution,
      dimensions: (canvasDimensions && resolution && canvasDimensions[resolution]) || null,
      elements,
      customAttributes,
      animations,
      gsapScriptPresent: Boolean(parsed.gsapScript),
      gsapError,
    });

    for (const element of elements) {
      if (element.isSubComposition && element.resolvedSrc) {
        queue.push({ filePath: element.resolvedSrc, depth: depth + 1, referencedBy: filePath });
      }
    }
  }

  const project = findProjectFps(path.dirname(path.resolve(htmlPath)));
  process.stdout.write(JSON.stringify({
    status: "SUCCESS",
    bridgeVersion: 1,
    parsersVersion: version,
    entry: path.resolve(htmlPath),
    project,
    documents,
    warnings,
  }));
}

main().catch((error) => {
  fail("BRIDGE_UNHANDLED", String((error && error.stack) || error));
});
