"""One synchronous array API for native Python and browser Python.

The host supplies the browser audio module and enters Python through Pyodide's
Promise-aware runner. Student algorithms do not need JavaScript or ``await``.
"""

from functools import lru_cache
import importlib.util
from pathlib import Path
import sys

import numpy as np


MAX_SECONDS = 10
DEFAULT_MODE = "loopback"


@lru_cache(maxsize=1)
def _native_backend():
    """Load the sibling adapter without making its filename a Python API."""
    source = Path(__file__).with_name("native-backend.py")
    spec = importlib.util.spec_from_file_location("_engr76_native_audio", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("The supplied native audio adapter is unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _browser_transmit(signal, sample_rate, mode):
    import pyodide.ffi as ffi

    feature_check = getattr(ffi, "can_run_sync", None)
    if not callable(feature_check) or not feature_check():
        raise RuntimeError(
            "This browser cannot run the synchronous audio API. "
            "Use the supplied Promise-aware browser runner in a supported "
            "browser, or run the same Python file locally."
        )

    import audio_backend

    # Explicit buffer conversion avoids passing a Python object proxy to audio.
    samples = ffi.to_js(signal, create_pyproxies=False)
    received = ffi.run_sync(audio_backend.transmit(samples, sample_rate, mode))
    return np.array(received.to_py(), dtype=np.float32, copy=True)


class Channel:
    """Transmit normalized mono samples and return a copied float32 array.

    ``loopback`` is a deterministic teaching model: gain 0.6, delay 128 samples,
    and a same-length output. ``microphone`` plays and records a real device.
    Constructing or importing this class never starts playback or recording.
    """

    def __init__(self, mode=None, sample_rate=44100):
        if mode is None:
            mode = DEFAULT_MODE
        if mode not in ("loopback", "microphone"):
            raise ValueError("mode must be 'loopback' or 'microphone'.")
        if isinstance(sample_rate, bool) or not isinstance(
            sample_rate, (int, np.integer)
        ):
            raise TypeError("sample_rate must be a positive integer.")
        if not 8000 <= sample_rate <= 96000:
            raise ValueError("sample_rate must be between 8000 and 96000 Hz.")
        self.mode = mode
        self.sample_rate = int(sample_rate)

    def transmit(self, signal: np.ndarray) -> np.ndarray:
        if not isinstance(signal, np.ndarray):
            raise TypeError("signal must be a NumPy array.")
        if signal.ndim != 1:
            raise ValueError("signal must be a one-dimensional mono array.")
        if not np.issubdtype(signal.dtype, np.floating):
            raise TypeError("signal must have a real floating-point dtype.")
        if signal.size == 0:
            raise ValueError("signal must contain at least one sample.")
        if signal.size > self.sample_rate * MAX_SECONDS:
            raise ValueError("signal must be at most 10 seconds long.")
        if not np.all(np.isfinite(signal)):
            raise ValueError("signal samples must be finite.")
        if np.any(np.abs(signal) > 1):
            raise ValueError("signal samples must be in the range [-1, 1].")

        # Device adapters own their buffers; they cannot change student input.
        samples = np.array(signal, dtype=np.float32, copy=True, order="C")
        if sys.platform == "emscripten":
            received = _browser_transmit(samples, self.sample_rate, self.mode)
        else:
            received = _native_backend().transmit(
                samples, self.sample_rate, self.mode
            )
        output = np.array(received, dtype=np.float32, copy=True)
        if output.ndim != 1 or output.size != signal.size:
            raise RuntimeError("The audio adapter returned an invalid shape.")
        if not np.all(np.isfinite(output)):
            raise RuntimeError("The audio adapter returned non-finite samples.")
        return output
