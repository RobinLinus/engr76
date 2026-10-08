"""Contract checks without accessing a microphone or playing audio."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from channel import Channel, _native_backend


def run_contract_checks():
    """Run the same device-free grading checks in native or browser Python.

    Only the supplied deterministic loopback API is used. No microphone,
    native-only adapter, or mock is needed for this shared grading function.
    """
    failures = []
    counts = {"numerical": 0, "validation": 0}

    def check(name, group, operation):
        counts[group] += 1
        try:
            operation()
        except Exception as error:
            failures.append({"name": name, "error": f"{type(error).__name__}: {error}"})

    def numerical(signal):
        before = signal.copy()
        output = Channel().transmit(signal)
        expected = np.zeros(signal.size, dtype=np.float32)
        if signal.size > 128:
            expected[128:] = np.float32(0.6) * before[:-128].astype(np.float32)
        assert isinstance(output, np.ndarray), "output is not a NumPy array"
        assert output.dtype == np.dtype("float32"), "output dtype is not float32"
        assert output.shape == signal.shape, "output shape changed"
        assert np.all(np.isfinite(output)), "output contains non-finite samples"
        assert not np.shares_memory(signal, output), "output aliases student input"
        np.testing.assert_allclose(output, expected, rtol=0, atol=1e-7)
        np.testing.assert_array_equal(signal, before)

    impulse = np.zeros(512, dtype=np.float32)
    impulse[0] = 1
    fixtures = [
        ("impulse gain and delay", impulse),
        ("zero array", np.zeros(512, dtype=np.float32)),
        ("signed values", np.tile(np.array([-1.0, 0.5, -0.25, 0.0]), 128)),
        ("noncontiguous input", np.linspace(-1, 1, 1024)[::2]),
        ("float64 input", np.linspace(-0.25, 0.25, 512, dtype=np.float64)),
        ("short array", np.array([0.5], dtype=np.float32)),
    ]
    for name, signal in fixtures:
        check(name, "numerical", lambda signal=signal: numerical(signal))

    def rejects_signal(signal, expected_error):
        try:
            Channel().transmit(signal)
        except expected_error:
            return
        raise AssertionError(f"invalid input did not raise {expected_error.__name__}")

    invalid = [
        ("Python list", [0.0], TypeError),
        ("integer array", np.array([0, 1]), TypeError),
        ("boolean array", np.array([True]), TypeError),
        ("complex array", np.array([0j]), TypeError),
        ("two-dimensional array", np.zeros((1, 1)), ValueError),
        ("scalar array", np.array(0.1), ValueError),
        ("empty array", np.array([], dtype=float), ValueError),
        ("NaN sample", np.array([np.nan]), ValueError),
        ("positive infinity", np.array([np.inf]), ValueError),
        ("negative infinity", np.array([-np.inf]), ValueError),
        ("positive range overflow", np.array([1.01]), ValueError),
        ("negative range overflow", np.array([-1.01]), ValueError),
        ("duration overflow", np.zeros(441001, dtype=np.float32), ValueError),
    ]
    for name, signal, error in invalid:
        check(name, "validation", lambda signal=signal, error=error: rejects_signal(signal, error))

    def rejects_options(options, expected_error):
        try:
            Channel(**options)
        except expected_error:
            return
        raise AssertionError(f"invalid options did not raise {expected_error.__name__}")

    options = [
        ("unknown mode", {"mode": "unknown"}, ValueError),
        ("boolean sample rate", {"sample_rate": True}, TypeError),
        ("floating sample rate", {"sample_rate": 44100.5}, TypeError),
        ("low sample rate", {"sample_rate": 7999}, ValueError),
        ("high sample rate", {"sample_rate": 96001}, ValueError),
    ]
    for name, values, error in options:
        check(name, "validation", lambda values=values, error=error: rejects_options(values, error))

    total = sum(counts.values())
    result = {"passed": total - len(failures), "total": total, "failures": failures, "counts": counts}
    if failures:
        raise AssertionError(json.dumps(result))
    return result


class NativeContractTests(unittest.TestCase):
    def test_shared_device_free_grading_contract(self):
        result = run_contract_checks()
        self.assertEqual(result["passed"], result["total"])
        self.assertEqual(result["failures"], [])

    def test_analytic_gain_delay_and_input_preservation(self):
        signal = np.linspace(-1, 1, 512)
        before = signal.copy()
        received = Channel().transmit(signal)
        expected = np.zeros(512, dtype=np.float32)
        expected[128:] = np.float32(0.6) * before[:-128].astype(np.float32)
        self.assertIsInstance(received, np.ndarray)
        self.assertEqual(received.dtype, np.dtype("float32"))
        np.testing.assert_array_equal(received, expected)
        np.testing.assert_array_equal(signal, before)
        self.assertFalse(np.shares_memory(received, signal))
        received[200] = 0
        np.testing.assert_array_equal(signal, before)

    def test_short_and_strided_arrays(self):
        for signal in (np.ones(1, dtype=np.float32), np.ones(128)[::2]):
            with self.subTest(size=signal.size):
                np.testing.assert_array_equal(
                    Channel().transmit(signal), np.zeros(signal.size, dtype=np.float32)
                )
        signal = np.linspace(-1, 1, 1024)[::2]
        expected = np.zeros(signal.size, dtype=np.float32)
        expected[128:] = np.float32(0.6) * signal[:-128].astype(np.float32)
        np.testing.assert_array_equal(Channel().transmit(signal), expected)

    def test_invalid_signal_contract(self):
        bad = [
            ([0.0], TypeError),
            (np.array([0, 1]), TypeError),
            (np.array([True]), TypeError),
            (np.array([0j]), TypeError),
            (np.array(["0"]), TypeError),
            (np.zeros((1, 1)), ValueError),
            (np.array(0.1), ValueError),
            (np.array([], dtype=float), ValueError),
            (np.array([np.nan]), ValueError),
            (np.array([np.inf]), ValueError),
            (np.array([-np.inf]), ValueError),
            (np.array([1.01]), ValueError),
            (np.array([-1.01]), ValueError),
            (np.zeros(441001), ValueError),
        ]
        with patch("channel._native_backend") as adapter:
            for signal, error in bad:
                with self.subTest(signal_type=type(signal).__name__, error=error):
                    with self.assertRaises(error):
                        Channel(mode="microphone").transmit(signal)
            adapter.assert_not_called()

    def test_constructor_rejects_invalid_options(self):
        with self.assertRaises(ValueError):
            Channel(mode="unknown")
        for sample_rate in (True, 44100.5, "44100"):
            with self.assertRaises(TypeError):
                Channel(sample_rate=sample_rate)
        for sample_rate in (0, -1, 7999, 96001):
            with self.assertRaises(ValueError):
                Channel(sample_rate=sample_rate)

    def test_ten_second_boundary(self):
        self.assertEqual(Channel().transmit(np.zeros(441000)).size, 441000)

    def test_imports_and_loopback_do_not_load_sounddevice(self):
        sys.modules.pop("sounddevice", None)
        _native_backend.cache_clear()
        Channel().transmit(np.ones(256))
        self.assertNotIn("sounddevice", sys.modules)

    def test_explicit_microphone_uses_blocking_mono_device(self):
        signal = np.linspace(-0.1, 0.1, 256)
        calls = []

        def playrec(samples, **kwargs):
            calls.append(kwargs)
            samples[:] = 0  # Device mutation must not reach the caller's array.
            return np.full((samples.size, 1), 0.125, dtype=np.float32)

        with patch.dict(sys.modules, {"sounddevice": SimpleNamespace(playrec=playrec)}):
            received = Channel(mode="microphone").transmit(signal)
        self.assertEqual(calls, [{
            "samplerate": 44100, "channels": 1, "dtype": "float32", "blocking": True
        }])
        np.testing.assert_array_equal(received, np.full(256, 0.125, dtype=np.float32))
        np.testing.assert_array_equal(signal, np.linspace(-0.1, 0.1, 256))

    def test_bad_device_output_is_rejected(self):
        for recorded in (np.zeros(256), np.zeros((255, 1)), np.full((256, 1), np.nan)):
            def playrec(samples, **kwargs):
                return recorded

            with self.subTest(shape=recorded.shape):
                with patch.dict(sys.modules, {"sounddevice": SimpleNamespace(playrec=playrec)}):
                    with self.assertRaises(RuntimeError):
                        Channel(mode="microphone").transmit(np.zeros(256))

    def test_missing_native_microphone_support_is_clear(self):
        with patch.dict(sys.modules, {"sounddevice": None}):
            with self.assertRaisesRegex(RuntimeError, "requires sounddevice"):
                Channel(mode="microphone").transmit(np.zeros(256))

    def test_unsupported_browser_runner_never_starts_audio(self):
        ffi = ModuleType("pyodide.ffi")
        ffi.can_run_sync = lambda: False
        pyodide = ModuleType("pyodide")
        pyodide.ffi = ffi
        audio = SimpleNamespace(transmit=lambda *args: self.fail("Audio started."))
        with patch.dict(sys.modules, {
            "pyodide": pyodide, "pyodide.ffi": ffi, "audio_backend": audio
        }):
            with patch("channel.sys.platform", "emscripten"):
                with self.assertRaisesRegex(RuntimeError, "synchronous audio API"):
                    Channel(mode="microphone").transmit(np.zeros(256))

    def test_unchanged_student_example_matches_channel_model(self):
        source = Path(__file__).with_name("student-example.py")
        spec = importlib.util.spec_from_file_location("student_example", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.run_example()
        self.assertEqual(result["sample_count"], 4410)
        self.assertEqual(result["output_dtype"], "float32")
        signal = np.array(result["transmitted_samples"], dtype=np.float32)
        expected = np.zeros(signal.size, dtype=np.float32)
        expected[128:] = np.float32(0.6) * signal[:-128]
        np.testing.assert_array_equal(result["received_samples"], expected)
        self.assertTrue(0 < result["rms"] < result["peak"] < 1)

    def test_native_example_in_isolated_process(self):
        source = Path(__file__).with_name("student-example.py").resolve()
        with tempfile.TemporaryDirectory(prefix="engr76-native-grading-") as directory:
            child = subprocess.run(
                [sys.executable, "-E", "-s", str(source), "--mode", "loopback"],
                cwd=directory,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(child.stderr, "")
        result = json.loads(child.stdout)
        self.assertEqual(result["sample_count"], 4410)
        signal = np.array(result["transmitted_samples"], dtype=np.float32)
        expected = np.zeros(signal.size, dtype=np.float32)
        expected[128:] = np.float32(0.6) * signal[:-128]
        np.testing.assert_array_equal(result["received_samples"], expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
