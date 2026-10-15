export class PythonRuntime {
  constructor({ WorkerClass = globalThis.Worker, timeout = 120000 } = {}) {
    this.WorkerClass = WorkerClass;
    this.timeout = timeout;
    this.pending = new Map();
    this.nextId = 0;
    this.worker = null;
    this.ready = false;
    this.sourceInfo = null;
  }

  init({ notebookContent } = {}) {
    if (notebookContent !== undefined && (this.ready || this.loading)) {
      return Promise.reject(new Error('Use a fresh runtime to load different notebook content.'));
    }
    if (this.ready) return Promise.resolve(this.sourceInfo);
    if (this.loading) return this.loading;
    try {
      this.worker = new this.WorkerClass(new URL('./runtime-worker.js', import.meta.url), { type: 'module' });
      const worker = this.worker;
      this.worker.onmessage = ({ data }) => {
        const request = this.pending.get(data.id);
        if (!request) return;
        this.pending.delete(data.id);
        clearTimeout(request.timer);
        if (data.error) request.reject(new Error(data.error));
        else request.resolve(data.result);
      };
      this.worker.onerror = () => {
        if (this.worker === worker) this.dispose(new Error('Python could not run. Check your connection and reload.'));
      };
      this.worker.onmessageerror = () => {
        if (this.worker === worker) this.dispose(new Error('Python returned unreadable data. Please reload.'));
      };
      this.loading = this.request('init', notebookContent === undefined ? {} : { notebookContent }).then(info => {
        if (this.worker === worker) {
          this.ready = true;
          this.sourceInfo = info;
        }
        return info;
      }).catch(error => {
        // A cancelled startup may reject after Retry has created a new worker.
        if (this.worker === worker) this.dispose(error);
        throw error;
      });
      return this.loading;
    } catch (error) {
      this.dispose(error);
      return Promise.reject(error);
    }
  }

  request(kind, fields = {}, transfer = []) {
    return new Promise((resolve, reject) => {
      if (!this.worker) { reject(new Error('Python is not ready.')); return; }
      const id = ++this.nextId;
      const timer = setTimeout(() => this.dispose(new Error('Python timed out. Check your connection and try again.')), this.timeout);
      this.pending.set(id, { resolve, reject, timer });
      try { this.worker.postMessage({ id, kind, ...fields }, transfer); }
      catch (error) { this.dispose(error); }
    });
  }

  encode(text, rate) { return this.request('encode', { text, rate }); }
  decode(samples, rate) {
    // Copy so transferring to the worker never detaches a caller's input array.
    const copy = samples.slice();
    return this.request('decode', { samples: copy, rate }, [copy.buffer]);
  }

  dispose(reason = new Error('Stopped.')) {
    this.worker?.terminate();
    this.worker = null;
    this.ready = false;
    this.sourceInfo = null;
    this.loading = null;
    for (const request of this.pending.values()) {
      clearTimeout(request.timer);
      request.reject(reason);
    }
    this.pending.clear();
  }
}
