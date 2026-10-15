// The only boundary into Python. The receive call has no text/length argument.
export function createCodecBridge(pyodide) {
  const codec = pyodide.pyimport('project2a');
  return {
    encode(text, rate) {
      const result = codec.encode_message(text, rate);
      try { return new Float32Array(result.toJs()); }
      finally { result.destroy(); }
    },
    decode(samples, rate) {
      const input = pyodide.toPy(samples);
      try {
        const result = codec.decode_recording(input, rate);
        try { return result.toJs({ dict_converter: Object.fromEntries, create_pyproxies: false }); }
        finally { result.destroy(); }
      }
      finally { input.destroy(); }
    },
    destroy() { codec.destroy(); },
  };
}
