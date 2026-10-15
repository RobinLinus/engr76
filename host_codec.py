"""Host framing/bounds only. All four signal algorithms come from notebook JSON."""

from functools import wraps
import numbers

import numpy as np
from notebook_importer import import_notebook, REQUIRED_FUNCTIONS

fs = 44_100
CARRIER = 2_000
RATES = (50, 100, 200)
MAX_PAYLOAD = 256
MAX_SEND_SECONDS = 15
MAX_CAPTURE_SECONDS = 20
GUARD_SAMPLES = 4_410  # host leading 100 ms; demo also appends 100 ms itself
PREAMBLE = b"\xd3\x91"
OVERHEAD_BITS = 32


class DecodeError(ValueError):
    """Could not locate a bounded message; safe to show the reason to the user."""


def _rate(rate):
    if isinstance(rate, (bool, np.bool_)) or not isinstance(rate, numbers.Real) or rate not in RATES:
        raise ValueError("Choose 50, 100 or 200 bits/s on both devices.")
    return int(rate)


def _samples(signal):
    # Inspect size before converting/copying a possibly large input.
    array = np.asarray(signal)
    if array.ndim != 1 or not 0 < array.size <= fs * MAX_CAPTURE_SECONDS:
        raise ValueError("Audio must be a nonempty, one-dimensional recording of at most 20 seconds.")
    if array.dtype.kind not in "fiu" or not np.all(np.isfinite(array)):
        raise ValueError("Audio samples must be finite real numbers.")
    return array.astype(np.float64, copy=False)


def _sample_count(bits, rate):
    return (bits * fs + rate - 1) // rate


def max_payload_bytes(rate):
    rate = _rate(rate)
    available = fs * MAX_SEND_SECONDS - 2 * GUARD_SAMPLES
    return min(MAX_PAYLOAD, (available * rate // fs - OVERHEAD_BITS) // 8)


def transmission_samples(byte_length, rate):
    """Demo-based preflight byte budget, never an output buffer allocation."""
    rate = _rate(rate)
    if isinstance(byte_length, (bool, np.bool_)) or not isinstance(byte_length, numbers.Integral) or not 1 <= byte_length <= MAX_PAYLOAD:
        raise ValueError("Message must contain 1–256 UTF-8 bytes.")
    count = 2 * GUARD_SAMPLES + _sample_count(OVERHEAD_BITS + 8 * int(byte_length), rate)
    if count > fs * MAX_SEND_SECONDS:
        raise ValueError(f"At {rate} bits/s, use at most {max_payload_bytes(rate)} UTF-8 bytes (15-second limit).")
    return count


def _call(function, *args):
    try:
        return function(*args)
    except Exception as error:
        raise DecodeError(f"{function.__name__}: {error}") from error


def _returned_array(result, function):
    try:
        return np.asarray(result)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{function} must return a numeric array (got invalid output).") from error


def _wave(result, function, limit):
    array = _returned_array(result, function)
    if array.ndim != 1 or not 0 < array.size <= limit or array.dtype.kind not in "fiu":
        raise ValueError(f"{function} must return a nonempty 1-D real sample array within the 15-second send limit (got None/invalid output or too many samples).")
    if not np.all(np.isfinite(array)) or np.any(np.abs(array) > np.finfo(np.float32).max):
        raise ValueError(f"{function} must return finite samples representable as float32.")
    return array.astype(np.float64, copy=False)


class Codec:
    def __init__(self, algorithms):
        self.algorithms = algorithms
        original_start = algorithms.detect_start

        @wraps(original_start)
        def checked_start(signal, visual_test=False):
            if visual_test:
                raise DecodeError("detect_start: optional visualization is unsupported in the browser audio path.")
            result = _call(original_start, signal, visual_test)
            if isinstance(result, (bool, np.bool_)) or not isinstance(result, numbers.Integral) or not 0 <= result < len(signal):
                raise DecodeError("detect_start must return an integer sample index within the capture (got None/invalid output).")
            return result

        # A return-value boundary, not an algorithm rewrite: demodulate still
        # invokes the original detect_start body through this checked call.
        algorithms.detect_start = checked_start

    def source_info(self):
        return {"functions": list(REQUIRED_FUNCTIONS), "declarations": list(self.algorithms.__declarations__)}

    def encode_message(self, text, rate):
        """Frame text, run imported modulation AND padding, then bound actual output."""
        rate = _rate(rate)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Enter a nonempty message.")
        if len(text) > MAX_PAYLOAD:
            raise ValueError("Message exceeds 256 UTF-8 bytes.")
        try:
            payload = text.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise ValueError("Message contains invalid Unicode.") from error
        transmission_samples(len(payload), rate)
        body = len(payload).to_bytes(2, "big") + payload
        bits = np.unpackbits(np.frombuffer(PREAMBLE + body, dtype=np.uint8), bitorder="big")
        wave = _wave(_call(self.algorithms.generate_modulated_signal, bits, rate, CARRIER),
                     "generate_modulated_signal", fs * MAX_SEND_SECONDS)
        padded = _wave(_call(self.algorithms.add_silence, wave), "add_silence",
                       fs * MAX_SEND_SECONDS - GUARD_SAMPLES)
        return np.concatenate((np.zeros(GUARD_SAMPLES), 0.8 * padded)).astype(np.float32)

    def _demodulate(self, array, rate, length):
        bits = _returned_array(_call(self.algorithms.demodulate, array, rate, length), "demodulate")
        if (bits.ndim != 1 or bits.size != length or bits.dtype.kind not in "biuf"
                or not np.all((bits == 0) | (bits == 1))):
            raise DecodeError(f"demodulate must return exactly {length} bits containing only 0 and 1 (got None/invalid output).")
        return bits.astype(np.uint8, copy=False)

    def decode_recording(self, samples, rate):
        """Receive only samples + rate; both demodulations see the same capture."""
        rate = _rate(rate)
        array = _samples(samples)
        header_bits = self._demodulate(array, rate, 32)
        header = np.packbits(header_bits, bitorder="big").tobytes()
        if header[:2] != PREAMBLE:
            raise DecodeError("Preamble not found. Check that both devices use the same rate and try a quieter room.")
        byte_length = int.from_bytes(header[2:4], "big")
        try:
            transmission_samples(byte_length, rate)  # bound BEFORE full-frame call
        except ValueError as error:
            raise DecodeError("Invalid length header. Check the rate or move the devices closer.") from error
        frame_bits = self._demodulate(array, rate, OVERHEAD_BITS + byte_length * 8)
        frame = np.packbits(frame_bits, bitorder="big").tobytes()
        payload = frame[4:]
        return {
            "text": payload.decode("utf-8", errors="replace"),
            "payload_bytes": list(payload),
            "payload_bits": frame_bits[OVERHEAD_BITS:].tolist(),
        }


def create_codec(notebook_content):
    return Codec(import_notebook(notebook_content))
