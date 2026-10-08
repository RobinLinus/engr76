"""Run this unchanged numerical Python example locally or in the browser."""

import json

import numpy as np
from scipy.signal import firwin, lfilter

from channel import Channel


def run_example(mode="loopback", sample_rate=44100):
    channel = Channel(mode=mode, sample_rate=sample_rate)
    sample_rate = channel.sample_rate
    if sample_rate <= 4000:
        raise ValueError("This example's filter requires sample_rate > 4000.")
    t = np.arange(sample_rate // 10) / sample_rate
    tone = 0.25 * np.sin(2 * np.pi * 440 * t)
    tone += 0.1 * np.sin(2 * np.pi * 1100 * t)
    taps = firwin(17, 2000, fs=sample_rate)
    transmitted = lfilter(taps, [1.0], tone)
    received = channel.transmit(transmitted)
    return {
        "mode": mode,
        "sample_rate": sample_rate,
        "sample_count": int(received.size),
        "input_dtype": str(transmitted.dtype),
        "output_dtype": str(received.dtype),
        "transmitted_samples": transmitted.tolist(),
        "received_samples": received.tolist(),
        "peak": float(np.max(np.abs(received))),
        "rms": float(np.sqrt(np.mean(received.astype(np.float64) ** 2))),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("loopback", "microphone"), default="loopback")
    parser.add_argument("--sample-rate", type=int, default=44100)
    options = parser.parse_args()
    print(json.dumps(run_example(options.mode, options.sample_rate)))
