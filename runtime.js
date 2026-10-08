// Reusable runtime: clients supply UI, while Python owns numerical algorithms.
import {AudioBackend} from './audio-backend.js';

async function sourceFile(name) {
  const response = await fetch(new URL(name, import.meta.url));
  if (!response.ok) throw new Error(`Cannot load ${name}.`);
  return response.text();
}

export class PythonAudioRuntime {
  constructor(onOutput = () => {}, runtimeURL = new URL('https://cdn.jsdelivr.net/pyodide/v314.0.7/full/')) {
    this.audio = new AudioBackend();
    this.onOutput = onOutput;
    this.pyodide = null;
    this.busy = false;
    this.runtimeURL = new URL(runtimeURL).href;
  }
  async initialize(onStatus = () => {}) {
    if (typeof WebAssembly.promising !== 'function') {
      throw new Error('This prototype needs a browser with WebAssembly Promise Integration, such as current Chrome.');
    }
    onStatus('Loading Python…');
    const {loadPyodide} = await import(new URL('pyodide.mjs', this.runtimeURL).href);
    this.pyodide = await loadPyodide({indexURL: this.runtimeURL, stdout: this.onOutput, stderr: this.onOutput});
    onStatus('Loading NumPy and SciPy…');
    await this.pyodide.loadPackage(['numpy', 'scipy']);
    const names = ['channel.py', 'student-example.py', 'check-native.py'];
    const sources = await Promise.all(names.map(sourceFile));
    names.forEach((name, index) => this.pyodide.FS.writeFile(`/home/pyodide/${name}`, sources[index]));
    this.source = sources[1];
    this.pyodide.registerJsModule('audio_backend', {
      transmit: (samples, sampleRate, mode) => this.audio.transmit(samples, sampleRate, mode),
    });
    await this.pyodide.runPythonAsync('import sys\nsys.path.insert(0, "/home/pyodide")\nfrom pyodide.ffi import can_run_sync\nassert can_run_sync(), "Synchronous browser bridge is unavailable"');
    return this;
  }
  async run(source = this.source, mode = 'loopback') {
    if (!this.pyodide) throw new Error('Wait for Python to finish loading.');
    if (this.busy) throw new Error('Python is already running.');
    this.busy = true;
    try {
      this.pyodide.globals.set('_source', source);
      this.pyodide.globals.set('_mode', mode);
      const result = await this.pyodide.runPythonAsync(`
import json
_namespace = {"__name__": "student_example"}
exec(compile(_source, "student-example.py", "exec"), _namespace)
json.dumps(_namespace["run_example"](mode=_mode))
`);
      return JSON.parse(result);
    } finally {
      this.pyodide.globals.delete('_source');
      this.pyodide.globals.delete('_mode');
      this.busy = false;
    }
  }
  async contractChecks() {
    if (this.busy) throw new Error('Python is already running.');
    this.busy = true;
    try {
      return JSON.parse(await this.pyodide.runPythonAsync(`
import json
_checks = {"__name__": "browser_checks"}
exec(compile(open("/home/pyodide/check-native.py").read(), "check-native.py", "exec"), _checks)
json.dumps(_checks["run_contract_checks"]())
`));
    } finally { this.busy = false; }
  }
  addFile(name, bytes) {
    if (!this.pyodide || this.busy) throw new Error('Wait until Python is ready.');
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_.-]*$/.test(name) || ['channel.py', 'student-example.py', 'check-native.py', 'native-backend.py'].includes(name)) {
      throw new Error('Choose a filename that does not replace the supplied runtime files.');
    }
    if (bytes.length > 2 * 1024 * 1024) throw new Error('This prototype accepts files up to 2 MB.');
    this.pyodide.FS.writeFile(`/home/pyodide/${name}`, bytes);
  }
  async runScript(source, name = 'uploaded-script.py', mode = 'loopback') {
    if (!this.pyodide || this.busy) throw new Error('Wait until Python is ready.');
    this.busy = true;
    this.pyodide.globals.set('_source', source);
    this.pyodide.globals.set('_script_name', name);
    this.pyodide.globals.set('_mode', mode);
    try {
      await this.pyodide.runPythonAsync(`
import channel, sys
_previous_mode, _previous_args = channel.DEFAULT_MODE, sys.argv
channel.DEFAULT_MODE, sys.argv = _mode, [_script_name]
try:
    exec(compile(_source, _script_name, "exec"), {"__name__": "__main__", "__file__": _script_name})
finally:
    channel.DEFAULT_MODE, sys.argv = _previous_mode, _previous_args
`);
    } finally {
      ['_source', '_script_name', '_mode'].forEach(name => this.pyodide.globals.delete(name));
      this.busy = false;
    }
  }
  async close() { await this.audio.close(); }
}
