import {PythonAudioRuntime} from './runtime.js';

const status = document.querySelector('#status');
const source = document.querySelector('#source');
const result = document.querySelector('#result');
const buttons = [...document.querySelectorAll('button')];
const run = document.querySelector('#run');
const compare = document.querySelector('#compare');
const enableMic = document.querySelector('#enable-mic');
const runMic = document.querySelector('#run-mic');
const stopMic = document.querySelector('#stop-mic');
const micStatus = document.querySelector('#mic-status');
const micDevice = document.querySelector('#microphone-device');
const runtime = new PythonAudioRuntime(line => {
  document.querySelector('#output').textContent += `${line}\n`;
});
let baseline;
let active = false;
let micEnabled = false;
let scriptName = null;
const fileNames = new Set();

function setStatus(text, error = false) {
  status.textContent = text;
  status.classList.toggle('error', error);
}
function setButtons() {
  run.disabled = compare.disabled = enableMic.disabled = active || !runtime.pyodide;
  runMic.disabled = active || !micEnabled;
  stopMic.disabled = active || !micEnabled;
  micDevice.disabled = active || !runtime.pyodide;
}
async function refreshMicrophones(selectedId = micDevice.value) {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  const inputs = (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === 'audioinput');
  const previous = selectedId;
  micDevice.replaceChildren();
  for (const [index, device] of inputs.entries()) {
    const option = document.createElement('option');
    option.value = device.deviceId;
    option.textContent = device.label || `Microphone ${index + 1}`;
    micDevice.append(option);
  }
  if (!inputs.length) {
    const option = document.createElement('option');
    option.value = ''; option.textContent = 'Browser default'; micDevice.append(option);
  }
  micDevice.value = inputs.some(device => device.deviceId === previous) ? previous
    : (inputs.find(device => device.deviceId === 'default')?.deviceId || inputs[0]?.deviceId || '');
}
async function sha256(text) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
function plot(data) {
  const canvas = document.querySelector('#waveform');
  const context = canvas.getContext('2d');
  const width = canvas.width, height = canvas.height;
  context.clearRect(0, 0, width, height);
  context.strokeStyle = '#d8e2d8';
  context.beginPath(); context.moveTo(0, height / 2); context.lineTo(width, height / 2); context.stroke();
  for (const [samples, color] of [[data.transmitted_samples, '#9aaea4'], [data.received_samples, '#287353']]) {
    context.strokeStyle = color;
    context.lineWidth = 1.6;
    context.beginPath();
    // Show the beginning clearly instead of reducing the full waveform to a blur.
    const count = Math.min(samples.length, 650);
    for (let index = 0; index < count; index++) {
      const x = index / Math.max(count - 1, 1) * width;
      const y = height / 2 - samples[index] * height * 1.2;
      index ? context.lineTo(x, y) : context.moveTo(x, y);
    }
    context.stroke();
  }
}
function summarize(data) {
  return `${data.sample_count.toLocaleString()} samples · ${data.sample_rate.toLocaleString()} Hz\nReceived: ${data.output_dtype} NumPy array\nPeak: ${data.peak.toFixed(6)} · RMS: ${data.rms.toFixed(6)}`;
}
async function action(callback) {
  if (active) return;
  active = true; setButtons();
  try { await callback(); }
  catch (error) { setStatus(error.message, true); }
  finally { active = false; setButtons(); }
}
async function compareExample() {
  if (await sha256(source.value) !== baseline.source_sha256) {
    throw new Error('The code has changed. Restore the original example to compare with the saved local Python result.');
  }
  const browser = await runtime.run(source.value);
  const native = baseline.result;
  if (browser.sample_count !== native.sample_count || browser.sample_rate !== native.sample_rate || browser.output_dtype !== native.output_dtype) {
    throw new Error('Native and browser array contracts differ.');
  }
  let maximum = 0;
  for (const key of ['transmitted_samples', 'received_samples']) {
    if (browser[key].length !== native[key].length) throw new Error('Result lengths differ.');
    browser[key].forEach((value, index) => { maximum = Math.max(maximum, Math.abs(value - native[key][index])); });
  }
  if (maximum > 1e-7) throw new Error(`Native/browser numerical mismatch: ${maximum}.`);
  plot(browser);
  result.textContent = `${summarize(browser)}\n\nSame Python source: verified\nMaximum native/browser difference: ${maximum.toExponential(3)}\nComparison passed (tolerance 0.0000001).`;
  document.body.dataset.comparison = 'passed';
  document.body.dataset.maxDifference = String(maximum);
  document.querySelector('#verification').textContent = JSON.stringify({passed: true,
    source_sha256: baseline.source_sha256, sample_count: browser.sample_count,
    maximum_difference: maximum, tolerance: 1e-7, audio: runtime.audio.stats}, null, 2);
  setStatus('The same Python code passed in both environments.');
  return {passed: true, maximum, source_sha256: baseline.source_sha256};
}

run.addEventListener('click', () => action(async () => {
  setStatus('Running your Python code…');
  document.querySelector('#output').textContent = '';
  await runtime.runScript(source.value, scriptName || 'student-example.py');
  const output = document.querySelector('#output').textContent;
  let data;
  try { data = JSON.parse(output); } catch {}
  if (Array.isArray(data?.transmitted_samples) && Array.isArray(data?.received_samples)) {
    plot(data); result.textContent = summarize(data);
  } else {
    result.textContent = output || 'Python finished without printed output.';
  }
  setStatus('Your Python script finished. Files remained in this browser session.');
}));
compare.addEventListener('click', () => action(compareExample));
enableMic.addEventListener('click', () => action(async () => {
  micEnabled = false;
  micStatus.textContent = 'Microphone is off while setup is in progress.';
  setStatus('Waiting for microphone permission…');
  let rate;
  try { rate = await runtime.audio.enableMicrophone(micDevice.value); }
  catch (error) {
    micStatus.textContent = 'Microphone is off. Setup failed; you can try again.';
    throw error;
  }
  micEnabled = true;
  const track = runtime.audio.stream?.getAudioTracks()[0];
  micStatus.textContent = `Microphone enabled: ${track?.label || 'selected input'}. Device rate: ${rate.toLocaleString()} Hz. Capture happens only when you run the microphone test.`;
  try { await refreshMicrophones(track?.getSettings().deviceId || micDevice.value); } catch { /* Device labels are optional; capture remains available. */ }
  setStatus('Microphone is ready. No recording has started.');
}));
micDevice.addEventListener('change', () => action(async () => {
  await runtime.close(); micEnabled = false;
  micStatus.textContent = 'Microphone is off. Enable the selected input to use it.';
  setStatus('Microphone input changed. Enable it before recording.');
}));
runMic.addEventListener('click', () => action(async () => {
  setStatus('Playing and recording the short Python signal…');
  try {
    if (scriptName) {
      document.querySelector('#output').textContent = '';
      await runtime.runScript(source.value, scriptName, 'microphone');
      result.textContent = document.querySelector('#output').textContent || 'Python finished without printed output.';
      setStatus('Your Python file finished with the microphone channel.');
      return;
    }
    const data = await runtime.run(source.value, 'microphone');
    const silent = data.received_samples.every(sample => sample === 0);
    plot(data); result.textContent = `${summarize(data)}\n\n${silent
      ? 'Only silence was received. Check your microphone, input device and speaker volume; audio transmission is not verified.'
      : 'Physical audio: delay and noise depend on your devices.'}`;
    setStatus(silent
      ? 'Capture completed, but the microphone returned only silence.'
      : 'The microphone test finished. Audio was processed in this page.', silent);
  } finally {
    await runtime.close(); micEnabled = false;
    micStatus.textContent = 'Microphone is off.';
  }
}));
stopMic.addEventListener('click', () => action(async () => {
  await runtime.close(); micEnabled = false;
  micStatus.textContent = 'Microphone is off.';
  setStatus('Microphone access stopped.');
}));
window.addEventListener('pagehide', () => {
  runtime.audio.generation += 1;
  runtime.audio.stream?.getTracks().forEach(track => track.stop());
});

async function acceptFiles(files) {
  if (active) return;
  try {
    for (const file of files) {
      if (file.size > 2 * 1024 * 1024) throw new Error('This prototype accepts files up to 2 MB.');
      const bytes = new Uint8Array(await file.arrayBuffer());
      runtime.addFile(file.name, bytes);
      fileNames.add(file.name);
      if (file.name.endsWith('.py')) {
        source.value = new TextDecoder('utf-8', {fatal: true}).decode(bytes);
        scriptName = file.name;
      }
    }
    document.querySelector('#file-list').textContent = [...fileNames].join(' · ');
    setStatus('Files loaded locally. Run your Python file when ready.');
  } catch (error) { setStatus(error.message, true); }
}
document.querySelector('#files').addEventListener('change', event => acceptFiles(event.target.files));
const dropZone = document.querySelector('#drop-zone');
dropZone.addEventListener('dragover', event => {event.preventDefault(); dropZone.classList.add('over');});
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('over'));
dropZone.addEventListener('drop', event => {event.preventDefault(); dropZone.classList.remove('over'); acceptFiles(event.dataTransfer.files);});

try {
  const response = await fetch(new URL('./native-baseline.json', import.meta.url));
  if (!response.ok) throw new Error('The native Python baseline is missing.');
  baseline = await response.json();
  await runtime.initialize(setStatus);
  source.value = runtime.source;
  source.disabled = false;
  document.querySelector('#files').disabled = false;
  const checks = await runtime.contractChecks();
  if (!checks.passed) throw new Error('Browser API contract checks failed.');
  document.querySelector('#checks').textContent = JSON.stringify(checks, null, 2);
  document.body.dataset.contract = 'passed';
  document.body.dataset.ready = 'true';
  try { await refreshMicrophones(); } catch { /* Keep the browser-default option if enumeration is unavailable. */ }
  setButtons();
  setStatus('Ready. Python, NumPy and SciPy are loaded. Microphone is off.');
} catch (error) {
  document.body.dataset.ready = 'error';
  setStatus(error.message, true);
  buttons.forEach(button => { button.disabled = true; });
}
