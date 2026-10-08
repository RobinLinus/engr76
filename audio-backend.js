// Device adapters only. Student signal processing remains in Python.
export const LOOPBACK_GAIN = 0.6;
export const LOOPBACK_DELAY = 128;
const MAX_SECONDS = 10;

function checkedSamples(samples, sampleRate) {
  if (!Number.isInteger(sampleRate) || sampleRate < 8000 || sampleRate > 96000) {
    throw new Error('Sample rate must be an integer between 8000 and 96000.');
  }
  const copy = Float32Array.from(samples);
  if (!copy.length || copy.length > sampleRate * MAX_SECONDS) {
    throw new Error('Signal must contain between one sample and ten seconds of audio.');
  }
  for (const value of copy) {
    if (!Number.isFinite(value) || Math.abs(value) > 1) {
      throw new Error('Signal samples must be finite and between -1 and 1.');
    }
  }
  return copy;
}

export class AudioBackend {
  constructor() {
    this.context = null;
    this.stream = null;
    this.busy = false;
    this.enabling = false;
    this.generation = 0;
    this.stats = {offlineTransmissions: 0, microphoneTransmissions: 0, permissionRequests: 0};
  }

  async enableMicrophone() {
    if (this.busy || this.enabling) throw new Error('Wait for the current audio action to finish.');
    this.enabling = true;
    let timer, stream, context, generation;
    try {
      await this.close();
      generation = ++this.generation;
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error('Microphone access requires HTTPS or localhost and a supported browser.');
      }
      this.stats.permissionRequests += 1;
      const setup = async () => {
        stream = await navigator.mediaDevices.getUserMedia({audio: {
          echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1,
        }});
        if (this.generation !== generation) {
          stream.getTracks().forEach(track => track.stop());
          throw new Error('Microphone setup was cancelled.');
        }
        // Expose pending resources to close() before any further await.
        this.stream = stream;
        context = new AudioContext();
        this.context = context;
        await context.audioWorklet.addModule(new URL('./capture-worklet.js', import.meta.url));
        await context.resume();
        if (generation !== this.generation) throw new Error('Microphone setup was cancelled.');
        return context.sampleRate;
      };
      return await Promise.race([setup(), new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('Microphone setup timed out. You can try again.')), 30000);
      })]);
    } catch (error) {
      this.generation += 1;
      stream?.getTracks().forEach(track => track.stop());
      if (this.stream === stream) this.stream = null;
      if (this.context === context) this.context = null;
      if (context && context.state !== 'closed') await context.close();
      throw error;
    } finally {
      clearTimeout(timer);
      this.enabling = false;
    }
  }

  async transmit(samples, sampleRate, mode) {
    const input = checkedSamples(samples, sampleRate);
    if (this.busy || this.enabling) throw new Error('Another audio action is already running.');
    this.busy = true;
    try {
      if (mode === 'loopback') return await this.offline(input, sampleRate);
      if (mode === 'microphone') return await this.capture(input, sampleRate);
      throw new Error('Unknown channel mode.');
    } finally {
      this.busy = false;
    }
  }

  async offline(input, sampleRate) {
    const context = new OfflineAudioContext(1, input.length, sampleRate);
    const buffer = context.createBuffer(1, input.length, sampleRate);
    buffer.copyToChannel(input, 0);
    const source = context.createBufferSource();
    const gain = context.createGain();
    source.buffer = buffer;
    gain.gain.value = LOOPBACK_GAIN;
    source.connect(gain).connect(context.destination);
    source.start(LOOPBACK_DELAY / sampleRate);
    try {
      const rendered = await context.startRendering();
      this.stats.offlineTransmissions += 1;
      return rendered.getChannelData(0).slice();
    } finally {
      source.disconnect();
      gain.disconnect();
    }
  }

  async capture(input, sampleRate) {
    const context = this.context;
    const stream = this.stream;
    if (!context || !stream || !stream.getAudioTracks().some(track => track.readyState === 'live')) {
      throw new Error('Enable the microphone explicitly before using the microphone channel.');
    }
    await context.resume();
    const frames = Math.max(1, Math.round(input.length * context.sampleRate / sampleRate));
    let microphone, recorder, mute, playback, timer;
    try {
    microphone = context.createMediaStreamSource(stream);
    recorder = new AudioWorkletNode(context, 'sample-capture', {
      processorOptions: {frames}, numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
    });
    mute = context.createGain();
    mute.gain.value = 0;
    const buffer = context.createBuffer(1, input.length, sampleRate);
    buffer.copyToChannel(input, 0);
    playback = context.createBufferSource();
    playback.buffer = buffer;
    microphone.connect(recorder).connect(mute).connect(context.destination);
    playback.connect(context.destination);
      const captured = new Promise((resolve, reject) => {
        recorder.port.onmessage = event => resolve(new Float32Array(event.data));
        recorder.onprocessorerror = () => reject(new Error('Audio capture failed.'));
        timer = setTimeout(() => reject(new Error('Audio capture timed out.')), input.length / sampleRate * 1000 + 4000);
      });
      playback.start();
      const raw = await captured;
      const resampled = await this.resample(raw, context.sampleRate, sampleRate, input.length);
      this.stats.microphoneTransmissions += 1;
      return resampled;
    } finally {
      clearTimeout(timer);
      try { playback.stop(); } catch {}
      playback?.disconnect();
      microphone?.disconnect();
      recorder?.disconnect();
      recorder?.port.close();
      mute?.disconnect();
    }
  }

  async resample(input, sourceRate, targetRate, frames) {
    if (sourceRate === targetRate && input.length === frames) return input.slice();
    const context = new OfflineAudioContext(1, frames, targetRate);
    const buffer = context.createBuffer(1, input.length, sourceRate);
    buffer.copyToChannel(input, 0);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    source.start();
    try {
      return (await context.startRendering()).getChannelData(0).slice();
    } finally { source.disconnect(); }
  }

  async close() {
    if (this.busy) throw new Error('Wait for the current transmission to finish.');
    this.generation += 1;
    const context = this.context;
    this.context = null;
    this.stream?.getTracks().forEach(track => track.stop());
    this.stream = null;
    if (context && context.state !== 'closed') await context.close();
  }
}
