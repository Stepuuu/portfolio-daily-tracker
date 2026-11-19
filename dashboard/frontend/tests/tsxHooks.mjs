// Module hooks that let a node test import a JSX-free `.tsx` module from src/ (routes.tsx): types are
// stripped with node's own stripper, `@/` resolves to src/, extensionless relative imports find their
// `.ts`/`.tsx` file, and `import.meta.env` is a development build's. Registered by tests that need it:
//   register('./tsxHooks.mjs', import.meta.url)
import { existsSync, readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src');
const EXTENSIONS = ['', '.ts', '.tsx', '/index.ts', '/index.tsx'];

function probe(base) {
  for (const extension of EXTENSIONS) {
    const candidate = base + extension;
    if (existsSync(candidate) && !candidate.endsWith('/') && (extension || /\.[cm]?[jt]sx?$/.test(candidate))) return candidate;
  }
  return null;
}

export async function resolve(specifier, context, nextResolve) {
  const fromSource = context.parentURL?.startsWith('file:') && /\.tsx?$/.test(context.parentURL);
  if (specifier.startsWith('@/')) {
    const found = probe(join(SRC, specifier.slice(2)));
    if (found) return { url: pathToFileURL(found).href, shortCircuit: true };
  } else if (fromSource && (specifier.startsWith('./') || specifier.startsWith('../'))) {
    const found = probe(join(dirname(fileURLToPath(context.parentURL)), specifier));
    if (found) return { url: pathToFileURL(found).href, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url.endsWith('.tsx')) {
    const source = stripTypeScriptTypes(readFileSync(fileURLToPath(url), 'utf8'), { mode: 'strip' });
    return { format: 'module', source: `import.meta.env ??= { DEV: true, PROD: false, MODE: 'test' };\n${source}`, shortCircuit: true };
  }
  return nextLoad(url, context);
}
