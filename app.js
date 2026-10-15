import { IndependentAudio } from './audio.js?v=2a-simple-1';
import { PythonRuntime } from './runtime.js?v=2a-simple-1';
import { ClientController } from './controller.js?v=2a-simple-1';
import { maxBytes, messageInfo } from './limits.js?v=2a-simple-1';

export function mount(document, window, { runtime = new PythonRuntime(), audio = new IndependentAudio() } = {}) {
  const get = id => document.getElementById(id);
  const message = get('message');
  const rate = get('rate');
  const receive = get('receive');
  const secure = window.isSecureContext;
  get('secure-notice').hidden = secure;

  function describeMessage() {
    const hint = get('message-info');
    hint.classList.remove('invalid');
    try {
      if (!message.value) {
        hint.textContent = `Up to ${maxBytes(Number(rate.value))} UTF-8 bytes at this rate. A character can use more than one byte.`;
        return;
      }
      const info = messageInfo(message.value, Number(rate.value));
      hint.textContent = `${info.bytes} / ${maxBytes(Number(rate.value))} UTF-8 bytes · ${info.seconds.toFixed(2)} seconds`;
    } catch (error) {
      hint.textContent = error.message;
      hint.classList.add('invalid');
    }
  }

  const controller = new ClientController({ runtime, audio, update({ state, status, result, ready }) {
    const busy = !['idle', 'error'].includes(state);
    get('send').disabled = !ready || busy;
    receive.disabled = !ready || busy || !secure;
    get('stop').disabled = !busy;
    get('retry').hidden = ready || state === 'loading';
    rate.disabled = busy;
    message.disabled = busy;
    get('status').textContent = status;
    get('state-dot').dataset.state = state;
    if (result !== undefined) {
      get('decoded').textContent = result || 'Your received message will appear here.';
      get('decoded').classList.toggle('empty', !result);
    }
  } });

  get('send').addEventListener('click', () => void controller.send(message.value, Number(rate.value)));
  // Deliberately reads ONLY the rate: receiving never examines the textarea.
  receive.addEventListener('click', () => void controller.receive(Number(rate.value)));
  get('stop').addEventListener('click', () => void controller.stop());
  get('retry').addEventListener('click', () => void controller.initialize());
  message.addEventListener('input', describeMessage);
  rate.addEventListener('change', describeMessage);
  window.addEventListener('pagehide', () => controller.dispose());
  window.addEventListener('beforeunload', () => controller.dispose());
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && !['idle', 'error'].includes(controller.state)) controller.dispose();
  });
  describeMessage();
  void controller.initialize();
  return controller;
}

if (typeof document !== 'undefined') mount(document, window);
