"""Small Project 2A OOK reference, authored from the unimplemented starters.

The three public signal functions implement the starter algorithms. Framing below
is separate: D391 preamble | uint16 byte length | UTF-8 | CRC16-CCITT-FALSE.
All bytes/bits are big-endian. Only NumPy and the Python standard library are used.
See README.md for acquisition choices and acoustic limitations.
"""

import binascii
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
OVERHEAD_BITS = 48


class DecodeError(ValueError):
    """No verified message was recovered; safe to show the reason to the user."""


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


def _normalized(signal):
    peak = float(np.max(np.abs(signal)))
    if peak < 1e-5:
        raise DecodeError("No audible signal found. Check speaker volume and microphone access.")
    return signal / peak  # private copy; also prevents overflow in squares


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
    """First absolute-value crossing of an RMS-derived threshold.

    A half-peak-block-RMS floor (10 ms blocks) prevents long idle silence from
    lowering the threshold into modest background noise. No preamble search,
    frequency correlation, carrier recovery or streaming modem is hidden here.
    visual_test retains the starter's optional visualize hook if supplied by a host.
    """
    array = _samples(signal)
    normalized = _normalized(array)
    power = normalized ** 2
    block = fs // 100
    starts = np.arange(0, len(array), block)
    counts = np.minimum(block, len(array) - starts)
    block_rms = np.sqrt(np.add.reduceat(power, starts) / counts)
    threshold = max(float(np.sqrt(np.mean(power))), 0.5 * float(block_rms.max()))
    crossings = np.flatnonzero(np.abs(normalized) > threshold)
    if not crossings.size:
        raise DecodeError("No clear signal start found. Try again in a quieter room.")
    start = int(crossings[0])
    if visual_test and callable(globals().get("visualize")):
        visualize(array, threshold * float(np.max(np.abs(array))), start, None)
    return start


def _origin(signal):
    # First crossing is slightly after the sine starts. Back off at most a quarter
    # of the fixed carrier period (6 samples), well below one 200-bps symbol.
    return max(0, detect_start(signal) - math.ceil(fs / (4 * CARRIER)))


def _bits_at(signal, rate, length, start):
    count = _sample_count(length, rate)
    if len(signal) - start < count:
        raise DecodeError("Incomplete recording. Start Receive before Send and wait for the tone to finish.")
    frame = signal[start:start + count]
    power = frame ** 2
    # The threshold only sees the requested symbols, never trailing idle audio.
    threshold = float(np.mean(power)) / 2
    groups = np.floor(np.arange(count) * rate / fs).astype(np.int64)
    energies = np.bincount(groups, weights=power, minlength=length)
    counts = np.bincount(groups, minlength=length)
    return (energies / counts > threshold).astype(np.uint8)


def demodulate(signal, rate, length):
    """Recover length bits using first-crossing alignment and mean symbol power."""
    rate = _rate(rate)
    if isinstance(length, (bool, np.bool_)) or not isinstance(length, numbers.Integral) or not 0 < length <= rate * MAX_SEND_SECONDS:
        raise ValueError("Expected bit length must be a positive bounded integer.")
    array = _samples(signal)
    return _bits_at(_normalized(array), rate, int(length), _origin(array))


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
    checksum = binascii.crc_hqx(body, 0xFFFF).to_bytes(2, "big")
    bits = np.unpackbits(np.frombuffer(PREAMBLE + body + checksum, dtype=np.uint8), bitorder="big")
    wave = generate_modulated_signal(bits, rate, CARRIER)
    result = np.zeros(count, dtype=np.float32)
    result[GUARD_SAMPLES:GUARD_SAMPLES + wave.size] = 0.8 * wave
    return result


def decode_recording(samples, rate):
    """Only recording + rate enter this function. No sender state is retained."""
    rate = _rate(rate)
    array = _samples(samples)
    start = _origin(array)  # acquire ONCE: header and full frame have the same clock
    normalized = _normalized(array)
    header_bits = _bits_at(normalized, rate, 32, start)
    header = np.packbits(header_bits, bitorder="big").tobytes()
    if header[:2] != PREAMBLE:
        raise DecodeError("Preamble not found. Check that both devices use the same rate and try a quieter room.")
    byte_length = int.from_bytes(header[2:4], "big")
    try:
        transmission_samples(byte_length, rate)  # bounds checked BEFORE full-frame allocation
    except ValueError as error:
        raise DecodeError("Invalid length header. Check the rate or move the devices closer.") from error
    frame_bits = _bits_at(normalized, rate, OVERHEAD_BITS + byte_length * 8, start)
    frame = np.packbits(frame_bits, bitorder="big").tobytes()
    if frame[:4] != header:
        raise DecodeError("Unstable signal level. Try a quieter room or a lower rate.")
    expected = binascii.crc_hqx(frame[2:-2], 0xFFFF)
    if int.from_bytes(frame[-2:], "big") != expected:
        raise DecodeError("Checksum failed: the message was incomplete or corrupted. Try again.")
    try:
        return frame[4:-2].decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise DecodeError("Invalid UTF-8 payload. No verified text was recovered.") from error
