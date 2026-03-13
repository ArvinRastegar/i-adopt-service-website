import 'bootstrap/dist/css/bootstrap.css';
import '../css/interface.css';
import '../css/svg.css';
import '../css/error.css';
import '../css/print.css';

import addEditor from './lib/addEditor.js';
import triggerRedraw from './lib/triggerRedraw.js';
import extract from './lib/extract.js';
import { getSVGBlob, getPNGBlob, getTurtleBlob } from './lib/export.js';

import { showError } from './ui/showError.js';

// import * as bootstrap from 'bootstrap';

const BACKEND_URL = 'http://localhost:8000';

function setDecomposeStatus(message, isError = false) {
  const el = document.querySelector('#decomposeStatus');
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
  if (!button) return;

  button.disabled = isLoading;
  button.textContent = isLoading ? 'Decomposing...' : 'Decompose';
  button.classList.toggle('is-loading', isLoading);
}

async function decomposeDefinition() {
  if (isDecomposing) return;
  const definition = document.querySelector('#definitionInput')?.value?.trim();
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
  setDecomposeStatus('This is going to take a moment...');

  try {
    isDecomposing = true;
    setDecomposeButtonState(true);
    const response = await fetch(`${BACKEND_URL}/decompose`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ definition }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || 'Backend request failed.');
    }

    if (rawOutputEl) {
      rawOutputEl.value = data.raw_llm_output || '';
    }

    if (ttlEl) {
      ttlEl.value = data.ttl || '';
    }

    if ((data.ttl || '').trim()) {
      await visualizeTTL(); // auto-run visualize once API returned TTL
    }

    renderValidationErrors(data.validation_errors || []);
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

document.querySelector('#export')
  ?.addEventListener('click', async (e) => {
    if (e.target.tagName.toUpperCase() !== 'A') {
      return;
    }

    try {
      let blob, ext;
      switch (e.target.dataset.format) {
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