# Python audio lab

A browser prototype for running the same numerical Python code locally and in WebAssembly, using a supplied NumPy-array channel API. Open the GitHub Pages site in current Chrome. Python, NumPy and SciPy are loaded through pinned Pyodide 314.0.7 from jsDelivr.

Imported Python files and assets remain in the browser session. The default loopback uses Web Audio without opening a microphone. Real audio requires explicit permission and a separate run action.

The original example and 24 shared API checks were verified in native and browser Python. The saved native comparison uses the exact original Python source. This prototype does not establish compatibility with an existing course grader or migrate all project notebooks. Physical audio and phone pairing are not qualified; QR pairing is not implemented.

The browser requires WebAssembly Promise Integration. Long-running user code currently runs on the page thread. Reload clears uploaded files.
