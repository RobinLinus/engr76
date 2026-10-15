import { createCodecBridge } from './python-bridge.js';

const CDN = 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/';
let codec;

self.onmessage = async ({ data }) => {
  const { id, kind } = data;
  try {
    if (kind === 'init') {
      const { loadPyodide } = await import(`${CDN}pyodide.mjs`);
      const pyodide = await loadPyodide({ indexURL: CDN, enableRunUntilComplete: false });
      await pyodide.loadPackage('numpy');
      const response = await fetch(new URL('./project2a.py', import.meta.url));
      if (!response.ok) throw new Error(`Could not load project2a.py (${response.status}).`);
      pyodide.FS.writeFile('/home/pyodide/project2a.py', await response.text());
      codec = createCodecBridge(pyodide);
      self.postMessage({ id, result: true });
    } else if (kind === 'encode' && codec) {
      const result = codec.encode(data.text, data.rate);
      self.postMessage({ id, result }, [result.buffer]);
    } else if (kind === 'decode' && codec) {
      self.postMessage({ id, result: codec.decode(data.samples, data.rate) });
    } else {
      throw new Error('Python is not ready. Reload the page and try again.');
    }
  } catch (error) {
    // Keep Python tracebacks out of the primary UI; preserve the operative reason.
    const lines = String(error.message || error).trim().split('\n');
    self.postMessage({ id, error: lines.at(-1).replace(/^(?:\w+\.)?\w*Error: /, '') });
  }
};
