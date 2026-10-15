"""Small Project 2A OOK reference, authored from the unimplemented starters.

The three public signal functions implement the starter algorithms. Framing below
is separate: D391 preamble | uint16 byte length | UTF-8 payload.
All bytes/bits are big-endian. Only NumPy and the Python standard library are used.
See README.md for acquisition choices and acoustic limitations.
"""

import math
import numbers

import numpy as np

fs = 44_100
CARRIER = 2_000
RATES = (50, 100, 200)
MAX_PAYLOAD = 256
MAX_SEND_SECONDS = 15
MAX_CAPTURE_SECONDS = 20
GUARD_SAMPLES = 4_410  # 100 ms before and after the frame
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


def generate_modulated_signal(message, rate, fc):
    """Continuous-phase sine × bits[floor(n * rate / fs)], with no symbol loop."""
    rate = _rate(rate)
    if isinstance(fc, bool) or not isinstance(fc, numbers.Real) or not math.isfinite(fc) or not 0 < fc < fs / 2:
        raise ValueError("Carrier must be finite and between 0 and the Nyquist frequency.")
    bits = np.asarray(message)
    if bits.ndim != 1 or not 0 < bits.size <= rate * MAX_SEND_SECONDS:
        raise ValueError("Supply a nonempty bit vector lasting at most 15 seconds.")
    if bits.dtype.kind not in "biuf" or not np.all((bits == 0) | (bits == 1)):
        raise ValueError("The message vector must contain only 0 and 1.")
    n = np.arange(_sample_count(bits.size, rate))
    symbols = np.floor(n * rate / fs).astype(np.int64)
    return bits[symbols] * np.sin(2 * np.pi * fc * n / fs)


def detect_start(signal, visual_test=False):
    """First absolute-value crossing of signal RMS (scalar multiplier: 1).

    visual_test retains the starter's optional visualize hook if supplied by a host.
    """
    array = _samples(signal)
    peak = float(np.max(np.abs(array)))
    normalized = array / peak if peak else array  # scale only; no amplitude floor
    threshold = float(np.sqrt(np.mean(normalized ** 2)))
    crossings = np.flatnonzero(np.abs(normalized) > threshold)
    if not crossings.size:
        raise DecodeError("No clear signal start found. Try again in a quieter room.")
    start = int(crossings[0])
    if visual_test and callable(globals().get("visualize")):
        visualize(array, threshold * peak, start, None)
    return start


def demodulate(signal, rate, length):
    """Use the first RMS crossing and half the power of ALL remaining samples."""
    rate = _rate(rate)
    if isinstance(length, (bool, np.bool_)) or not isinstance(length, numbers.Integral) or not 0 < length <= rate * MAX_SEND_SECONDS:
        raise ValueError("Expected bit length must be a positive bounded integer.")
    array = _samples(signal)
    start_index = detect_start(array)
    length = int(length)
    count = _sample_count(length, rate)
    if len(array) - start_index < count:
        raise DecodeError("Incomplete recording. Start Receive before Send and wait for the tone to finish.")
    bit_threshold = np.mean(array[start_index:] ** 2) / 2
    power = array[start_index:start_index + count] ** 2
    groups = np.floor(np.arange(count) * rate / fs).astype(np.int64)
    energies = np.bincount(groups, weights=power, minlength=length)
    counts = np.bincount(groups, minlength=length)
    return (energies / counts > bit_threshold).astype(np.uint8)


def max_payload_bytes(rate):
    rate = _rate(rate)
    available = fs * MAX_SEND_SECONDS - 2 * GUARD_SAMPLES
    return min(MAX_PAYLOAD, (available * rate // fs - OVERHEAD_BITS) // 8)


def transmission_samples(byte_length, rate):
    rate = _rate(rate)
    if isinstance(byte_length, (bool, np.bool_)) or not isinstance(byte_length, numbers.Integral) or not 1 <= byte_length <= MAX_PAYLOAD:
        raise ValueError("Message must contain 1–256 UTF-8 bytes.")
    count = 2 * GUARD_SAMPLES + _sample_count(OVERHEAD_BITS + 8 * int(byte_length), rate)
    if count > fs * MAX_SEND_SECONDS:
        raise ValueError(f"At {rate} bits/s, use at most {max_payload_bytes(rate)} UTF-8 bytes (15-second limit).")
    return count


def encode_message(text, rate):
    """Encode actual text. Returns mono float32 samples at exactly fs Hz."""
    rate = _rate(rate)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Enter a nonempty message.")
    if len(text) > MAX_PAYLOAD:
        raise ValueError("Message exceeds 256 UTF-8 bytes.")
    try:
        payload = text.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError("Message contains invalid Unicode.") from error
    count = transmission_samples(len(payload), rate)
    body = len(payload).to_bytes(2, "big") + payload
    bits = np.unpackbits(np.frombuffer(PREAMBLE + body, dtype=np.uint8), bitorder="big")
    wave = generate_modulated_signal(bits, rate, CARRIER)
    result = np.zeros(count, dtype=np.float32)
    result[GUARD_SAMPLES:GUARD_SAMPLES + wave.size] = 0.8 * wave
    return result


def decode_recording(samples, rate):
    """Return actual payload bytes/bits and best-effort text from samples + rate."""
    rate = _rate(rate)
    array = _samples(samples)
    header_bits = demodulate(array, rate, 32)
    header = np.packbits(header_bits, bitorder="big").tobytes()
    if header[:2] != PREAMBLE:
        raise DecodeError("Preamble not found. Check that both devices use the same rate and try a quieter room.")
    byte_length = int.from_bytes(header[2:4], "big")
    try:
        transmission_samples(byte_length, rate)  # bounds checked BEFORE full-frame allocation
    except ValueError as error:
        raise DecodeError("Invalid length header. Check the rate or move the devices closer.") from error
    # The same unchanged capture gives the public function the same origin and
    # whole-tail threshold on both calls; only the requested bit count differs.
    frame_bits = demodulate(array, rate, OVERHEAD_BITS + byte_length * 8)
    frame = np.packbits(frame_bits, bitorder="big").tobytes()
    payload = frame[4:]
    return {
        "text": payload.decode("utf-8", errors="replace"),
        "payload_bytes": list(payload),
        "payload_bits": frame_bits[OVERHEAD_BITS:].tolist(),
    }
