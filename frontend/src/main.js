import 'bootstrap/dist/css/bootstrap.css';
import 'bootstrap/dist/js/bootstrap.bundle.min.js';
import '../css/interface.css';
import '../css/svg.css';
import '../css/error.css';
import '../css/print.css';

import addEditor from './lib/addEditor.js';
import triggerRedraw from './lib/triggerRedraw.js';
import extract from './lib/extract.js';
import { getCurrentTurtle, getSVGBlob, getPNGBlob, getTurtleBlob } from './lib/export.js';

import { showError } from './ui/showError.js';

const BACKEND_URL = 'http://localhost:8000';
const PUBLISHED_NANOPUBS_STORAGE_KEY = 'iadopt-published-nanopubs';
const FALLBACK_MODEL_NAME = 'qwen/qwen3.5-flash-02-23';
const FALLBACK_MODEL_NAMES = [
  'qwen/qwen3.5-flash-02-23',
  'qwen/qwen3-32b',
  'qwen/qwen3.5-397b-a17b',
];

function setDecomposeStatus(message, isError = false) {
  const el = document.querySelector('#decomposeStatus');
  if (!el) return;

  el.textContent = message || '';
  el.classList.toggle('text-danger', isError);
  el.classList.toggle('text-secondary', !isError);
}

function setRetractStatus(message, isError = false) {
  const el = document.querySelector('#retractStatus');
  if (!el) return;

  el.textContent = message || '';
  el.classList.toggle('text-danger', isError);
  el.classList.toggle('text-secondary', !isError);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function renderValidationErrors(errors = []) {
  const container = document.querySelector('#validationErrors');
  if (!container) return;

  if (!errors.length) {
    container.innerHTML = '<div class="text-success">No schema validation errors.</div>';
    return;
  }

  container.innerHTML = `
    <div class="text-danger" style="white-space: pre-wrap;">
      ${errors.map((line) => escapeHtml(line)).join('<br>')}
    </div>
  `;
}

function readPublishedNanopubs() {
  try {
    const raw = localStorage.getItem(PUBLISHED_NANOPUBS_STORAGE_KEY);
    if (!raw) return [];

    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    console.error(e);
    return [];
  }
}

function writePublishedNanopubs(entries) {
  // Persisting published nanopubs in localStorage keeps the retract dropdown available across reloads.
  localStorage.setItem(PUBLISHED_NANOPUBS_STORAGE_KEY, JSON.stringify(entries));
}

function getManualRetractReference() {
  return document.querySelector('#retractInput')?.value?.trim() || '';
}

function updateRetractButtonState() {
  const retractButton = document.querySelector('#retractButton');
  const select = document.querySelector('#retractSelect');
  if (!retractButton) return;

  // Retraction is allowed when the user either selects a saved publication or pastes a manual nanopub reference.
  retractButton.disabled = !getManualRetractReference() && !select?.value;
}

function renderRetractOptions(selectedNanopubUrl = '') {
  const select = document.querySelector('#retractSelect');
  if (!select) return;

  const entries = readPublishedNanopubs();
  select.innerHTML = '';

  if (!entries.length) {
    select.innerHTML = '<option value="">No published nanopublications saved yet.</option>';
    select.disabled = true;
    updateRetractButtonState();
    return;
  }

  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = 'Select a published variable identifier';
  select.appendChild(placeholder);

  for (const entry of entries) {
    // The option label shows only the variable identifier, while the value keeps the nanopub URL needed to retract it.
    const option = document.createElement('option');
    option.value = entry.nanopubUrl;
    option.textContent = entry.variableIdentifier;
    option.title = entry.nanopubUrl;

    if (selectedNanopubUrl && entry.nanopubUrl === selectedNanopubUrl) {
      option.selected = true;
    }

    select.appendChild(option);
  }

  select.disabled = false;
  updateRetractButtonState();
}

function rememberPublishedNanopub(entry) {
  const entries = readPublishedNanopubs()
    .filter((item) => item.nanopubUrl !== entry.nanopubUrl);

  // New publications are inserted first so the most recent identifier is easiest to retract.
  entries.unshift(entry);
  writePublishedNanopubs(entries);
  renderRetractOptions(entry.nanopubUrl);
}

function forgetPublishedNanopub(nanopubUrl) {
  const entries = readPublishedNanopubs()
    .filter((entry) => entry.nanopubUrl !== nanopubUrl);

  writePublishedNanopubs(entries);
  renderRetractOptions();
}

function getSelectedPublishedNanopub() {
  const select = document.querySelector('#retractSelect');
  if (!select?.value) return null;

  return readPublishedNanopubs()
    .find((entry) => entry.nanopubUrl === select.value) || null;
}

async function visualizeTTL() {
  const raw = document.querySelector('#input').value;

  try {
    let content;

    try {
      content = await extract(raw);
    } catch (e) {
      console.error(e);
      showError('#svg', e, 'Parsing', raw);
      return;
    }

    triggerRedraw(content[0]);
    await addEditor();
    document.querySelector('#export').classList.remove('invisible');

  } catch (e) {
    console.error(e);
    showError('#svg', e, 'Rendering', raw);
  }
}

let isDecomposing = false;

function setDecomposeButtonState(isLoading) {
  const button = document.querySelector('#decompose');
  const modelSelect = document.querySelector('#modelSelect');
  const thinkingToggle = document.querySelector('#disableThinkingToggle');
  if (!button) return;

  button.disabled = isLoading;
  button.textContent = isLoading ? 'Decomposing...' : 'Decompose';
  button.classList.toggle('is-loading', isLoading);
  if (modelSelect) modelSelect.disabled = isLoading;
  if (thinkingToggle) thinkingToggle.disabled = isLoading;
}

function appendRawOutputDelta(delta) {
  const rawOutputEl = document.querySelector('#rawOutput');
  if (!rawOutputEl || !delta) return;

  rawOutputEl.value += delta;
  rawOutputEl.scrollTop = rawOutputEl.scrollHeight;
}

function renderModelOptions(modelNames = FALLBACK_MODEL_NAMES, defaultModelName = FALLBACK_MODEL_NAME) {
  const modelSelect = document.querySelector('#modelSelect');
  if (!modelSelect) return;

  const uniqueModelNames = [...new Set((modelNames || []).filter(Boolean))];
  const finalModelNames = uniqueModelNames.length ? uniqueModelNames : FALLBACK_MODEL_NAMES;
  const selectedModelName = finalModelNames.includes(defaultModelName) ? defaultModelName : finalModelNames[0];

  modelSelect.innerHTML = '';

  for (const modelName of finalModelNames) {
    const option = document.createElement('option');
    option.value = modelName;
    option.textContent = modelName;
    option.selected = modelName === selectedModelName;
    modelSelect.appendChild(option);
  }
}

async function loadModelOptions() {
  try {
    const response = await fetch(`${BACKEND_URL}/model-options`);
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || 'Could not load model options.');
    }

    renderModelOptions(data.model_names, data.default_model_name);
  } catch (e) {
    console.error(e);
    // Keep the dropdown usable even if the backend config endpoint is temporarily unavailable.
    renderModelOptions();
  }
}

async function decomposeDefinition() {
  if (isDecomposing) return;
  const definition = document.querySelector('#definitionInput')?.value?.trim();
  const modelName = document.querySelector('#modelSelect')?.value?.trim() || FALLBACK_MODEL_NAME;
  const disableThinking = Boolean(document.querySelector('#disableThinkingToggle')?.checked);
  const rawOutputEl = document.querySelector('#rawOutput');
  const ttlEl = document.querySelector('#input');

  if (!definition) {
    renderValidationErrors(['Please enter a variable definition first.']);
    setDecomposeStatus('Missing input.', true);
    return;
  }

  if (rawOutputEl) rawOutputEl.value = '';
  if (ttlEl) ttlEl.value = '';
  renderValidationErrors([]);
  setDecomposeStatus(
    disableThinking ? 'This is going to take a moment. Thinking is disabled.' : 'This is going to take a moment...',
  );

  try {
    isDecomposing = true;
    setDecomposeButtonState(true);
    const response = await fetch(`${BACKEND_URL}/decompose/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // The backend treats the absence of the override as normal thinking, so we only send a boolean flag here.
      body: JSON.stringify({ definition, model_name: modelName, disable_thinking: disableThinking }),
    });

    if (!response.ok) {
      let detail = 'Backend request failed.';

      try {
        const errorData = await response.json();
        detail = errorData.detail || detail;
      } catch (error) {
        try {
          detail = (await response.text()) || detail;
        } catch (textError) {
          console.error(textError);
        }
      }

      throw new Error(detail);
    }

    if (!response.body) {
      throw new Error('The backend did not return a readable stream.');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalPayload = null;

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

      let newlineIndex = buffer.indexOf('\n');
      while (newlineIndex !== -1) {
        const line = buffer.slice(0, newlineIndex).trim();
        buffer = buffer.slice(newlineIndex + 1);

        if (line) {
          const event = JSON.parse(line);

          if (event.type === 'raw_delta') {
            appendRawOutputDelta(event.delta || '');
          } else if (event.type === 'error') {
            throw new Error(event.detail || 'Streaming backend error.');
          } else if (event.type === 'final') {
            finalPayload = event.data || null;
          }
        }

        newlineIndex = buffer.indexOf('\n');
      }

      if (done) break;
    }

    const trailingLine = buffer.trim();
    if (trailingLine) {
      const event = JSON.parse(trailingLine);
      if (event.type === 'raw_delta') {
        appendRawOutputDelta(event.delta || '');
      } else if (event.type === 'error') {
        throw new Error(event.detail || 'Streaming backend error.');
      } else if (event.type === 'final') {
        finalPayload = event.data || null;
      }
    }

    if (!finalPayload) {
      throw new Error('The backend stream ended before returning the final decomposition payload.');
    }

    if (rawOutputEl) {
      rawOutputEl.value = finalPayload.raw_llm_output || rawOutputEl.value;
    }

    if (ttlEl) {
      ttlEl.value = finalPayload.ttl || '';
    }

    if ((finalPayload.ttl || '').trim()) {
      await visualizeTTL(); // auto-run visualize once API returned TTL
    }

    renderValidationErrors(finalPayload.validation_errors || []);
    setDecomposeStatus('Decomposition finished.');

  } catch (e) {
    console.error(e);
    renderValidationErrors([e.message || 'Unknown backend error.']);
    setDecomposeStatus('Decomposition failed.', true);
  }
  finally {
    isDecomposing = false;
    setDecomposeButtonState(false);
  }
}

async function publishNanopub() {
  // Open the tab before the network roundtrip so browsers keep the user-triggered popup allowance.
  const publishTab = window.open('', '_blank', 'noopener,noreferrer');
  const ttl = getCurrentTurtle().trim();

  if (!ttl) {
    if (publishTab) publishTab.close();
    throw new Error('No Turtle available to publish.');
  }

  setDecomposeStatus('Publishing nanopublication...');

  try {
    const response = await fetch(`${BACKEND_URL}/nanopub/publish`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ttl }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || 'Nanopub publish failed.');
    }

    if (!data.nanopub_url) {
      throw new Error('Backend did not return the nanopub URL.');
    }

    rememberPublishedNanopub({
      variableIdentifier: data.variable_identifier || data.variable_uri || data.nanopub_url,
      variableUri: data.variable_uri || '',
      nanopubUrl: data.nanopub_url,
      publishedTo: data.published_to || '',
      savedAt: new Date().toISOString(),
    });

    // Every successful publish immediately becomes available in the retract dropdown below the visualization.
    setRetractStatus(`Saved ${data.variable_identifier || data.nanopub_url} for retraction.`);

    // Reuse the reserved tab so the final published nanopub opens directly for the user.
    if (publishTab) {
      publishTab.location = data.nanopub_url;
    } else {
      window.open(data.nanopub_url, '_blank', 'noopener,noreferrer');
    }

    setDecomposeStatus('Nanopublication published.');
  } catch (e) {
    if (publishTab) publishTab.close();
    setDecomposeStatus('Nanopublication publish failed.', true);
    throw e;
  }
}

async function retractNanopub() {
  // Reserve the browser tab before the request so the final retraction nanopub opens without popup blocking.
  const retractTab = window.open('', '_blank', 'noopener,noreferrer');
  const manualReference = getManualRetractReference();
  const selectedEntry = getSelectedPublishedNanopub();
  const targetReference = manualReference || selectedEntry?.nanopubUrl;
  const targetLabel = manualReference || selectedEntry?.variableIdentifier || 'selected nanopublication';

  if (!targetReference) {
    if (retractTab) retractTab.close();
    throw new Error('Choose a published variable identifier or paste a nanopublication reference to retract.');
  }

  setRetractStatus(`Retracting ${targetLabel}...`);

  try {
    const response = await fetch(`${BACKEND_URL}/nanopub/retract`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nanopub_uri: targetReference }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || 'Nanopub retract failed.');
    }

    if (!data.retraction_url) {
      throw new Error('Backend did not return the retraction nanopub URL.');
    }

    const matchingEntry = readPublishedNanopubs()
      .find((entry) => entry.nanopubUrl === data.retracted_nanopub_url || entry.nanopubUrl === targetReference);

    if (matchingEntry) {
      forgetPublishedNanopub(matchingEntry.nanopubUrl);
    }

    const retractInput = document.querySelector('#retractInput');
    if (retractInput) retractInput.value = '';

    setRetractStatus(`Retracted ${targetLabel}.`);
    updateRetractButtonState();

    if (retractTab) {
      retractTab.location = data.retraction_url;
    } else {
      window.open(data.retraction_url, '_blank', 'noopener,noreferrer');
    }
  } catch (e) {
    if (retractTab) retractTab.close();
    // Surface the backend detail directly so users can see whether the failure is a key mismatch or a registry rejection.
    setRetractStatus(e.message || 'Nanopublication retract failed.', true);
    throw e;
  }
}

document.querySelector('#decompose')
  ?.addEventListener('click', async () => {
    await decomposeDefinition();
  });

document.querySelector('#visualize')
  ?.addEventListener('click', async () => {
    await visualizeTTL();
  });

// ---- PRELOAD TTL FROM URL (optional) ----
const url = new URL(window.location.href);
if (url.searchParams.has('ttl')) {
  const ttl = decodeURI(url.searchParams.get('ttl'));
  const input = document.querySelector('#input');
  if (input) input.value = ttl;
}

// Only auto-visualize if some TTL is already present.
const initialTTL = document.querySelector('#input')?.value?.trim();
if (initialTTL) {
  document.querySelector('#visualize')?.click();
}

loadModelOptions();
renderRetractOptions();

document.querySelector('#export')
  ?.addEventListener('click', async (e) => {
    const link = e.target.closest('a[data-format]');
    if (!link) {
      return;
    }

    e.preventDefault();

    try {
      let blob, ext;
      switch (link.dataset.format) {
        case 'svg':
          blob = getSVGBlob();
          ext = 'svg';
          break;

        case 'png':
          blob = await getPNGBlob();
          ext = 'png';
          break;

        case 'ttl':
          blob = await getTurtleBlob();
          ext = 'ttl';
          break;

        case 'nanopub':
          await publishNanopub();
          return;

        default:
          throw Error('Unknown export format!');
      }

      const svg = document.querySelector('#svg');
      const iri = svg.dataset.iri || 'visualization';
      const filename = iri.split(/[/#]/).pop() + '.' + ext;

      const dlURL = URL.createObjectURL(blob);
      const downloadLink = document.createElement('a');
      downloadLink.href = dlURL;
      downloadLink.download = filename;
      document.body.appendChild(downloadLink);
      downloadLink.click();
      document.body.removeChild(downloadLink);

    } catch (e) {
      console.error(e);
    }
  });

document.querySelector('#retractSelect')
  ?.addEventListener('change', (e) => {
    // Changing the saved-selection path updates the shared retract button state.
    updateRetractButtonState();
  });

document.querySelector('#retractInput')
  ?.addEventListener('input', () => {
    // Typing a manual nanopub reference enables the same retract button without needing a saved dropdown entry.
    updateRetractButtonState();
  });

document.querySelector('#retractButton')
  ?.addEventListener('click', async () => {
    try {
      await retractNanopub();
    } catch (e) {
      console.error(e);
    }
  });

/* XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX ORDER XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX */

document.querySelector('#order kbd')
  ?.addEventListener('click', () => {
    document.querySelector('.orderDialog')?.classList.remove('hidden');
  });

document.querySelector('.orderDialog button')
  ?.addEventListener('click', () => {
    const order = Array.from(document.querySelectorAll('.orderDialog .dropzone .concept'))
      .map((el) => el.dataset.id)
      .join('')
      .toUpperCase();

    document.querySelector('#order kbd').innerHTML = order;
    document.querySelector('#order input').value = order;

    document.querySelector('#visualize')?.click();
    document.querySelector('.orderDialog')?.classList.add('hidden');
  });

// drag & drop
let draggedItem;
for (const el of document.querySelectorAll('.orderDialog .concept')) {
  el.addEventListener('dragstart', dragStart);
  el.addEventListener('dragover', dragOver);
  el.addEventListener('dragend', dragEnd);
}

function dragEnd() {
  draggedItem = null;
}

function dragOver(e) {
  if (isBefore(draggedItem, e.target)) {
    e.target.parentNode.insertBefore(draggedItem, e.target);
  } else {
    e.target.parentNode.insertBefore(draggedItem, e.target.nextSibling);
  }
}

function dragStart(e) {
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', null);
  draggedItem = e.target;
}

function isBefore(el1, el2) {
  let cur;
  if (el2.parentNode === el1.parentNode) {
    for (cur = el1.previousSibling; cur; cur = cur.previousSibling) {
      if (cur === el2) return true;
    }
  }
  return false;
}
