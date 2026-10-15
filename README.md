# Project 2A — Messages through sound

A small browser experiment: enter a message, choose **50, 100 or 200 bits/s**, and send it through a speaker. A second device independently records and decodes it. Both browsers fetch [project2a.py](project2a.py) and run the Python OOK algorithms locally with Pyodide and NumPy.

**Open the app:** [robinlinus.github.io/engr76](https://robinlinus.github.io/engr76/) redirects to the existing [robinlinus.com/engr76](https://robinlinus.com/engr76/) Pages domain.

1. Open or reload the page on both devices and choose the same rate.
2. On the receiver, press **Receive**, allow microphone access and wait for “listening.”
3. Enter a message on the sender and press **Send**.
4. After the tone ends, press **Stop** on the receiver. The actual decoded text appears; errors may remain. Invalid UTF-8 is displayed with replacement characters (�).

Use nearby devices, a quiet room and a comfortable volume. Keep the receiving page open. Recording ends automatically after 20 seconds; sending is limited to 15 seconds. Maximum message sizes are 88 UTF-8 bytes at 50 bits/s, 181 at 100 bits/s and 256 at 200 bits/s. Reload both devices after an update.

## Implementation

The reference uses 44,100 Hz samples, a 2,000 Hz carrier, continuous-phase on-off keying, first RMS crossing and the 2A starter's mean-energy demodulation threshold. The sender and receiver call the public Python algorithm functions. A separate framing helper carries a fixed preamble, a two-byte length and UTF-8 text. Reception receives only recorded samples and the selected rate. It does not receive the sender's text or length through the application. There is no checksum or payload-integrity gate; the decoder retains actual payload bytes and bits alongside best-effort text. Preamble, length and complete-recording checks locate and bound the message.

The browser supplies real speaker/microphone I/O and converts the actual capture sample rate. Python runs in a Web Worker; the microphone uses an AudioWorklet. Audio tracks are stopped on Stop, errors and page exit. There is no upload, server message exchange, pairing, return protocol or error correction. The bundled Python reference was newly authored for this demonstration from the 2A algorithm requirements.

This is a simple teaching experiment: earlier loud sounds, echoes, clipping, noise, device processing or clock drift can produce errors or prevent acquisition. Numerical and controlled-capture tests do not establish successful laptop-to-phone acoustic message reception. The selected 50/100/200 rates are this client's controls, not the full set of assignment experiments.

## Static hosting and local preview

The repository root is the complete app. No build step is needed. GitHub Pages serves the `main` branch root; `.nojekyll` preserves direct static serving. Module, worker, worklet and Python paths are relative, including under `/engr76/`.

For a local desktop preview, run `python3 -m http.server 8770 --bind 127.0.0.1` in this folder and open `http://127.0.0.1:8770/`. Phone microphones require HTTPS; plain LAN HTTP does not suffice. Initial Pyodide/NumPy downloads require internet. Messages and microphone recordings remain on each device.
