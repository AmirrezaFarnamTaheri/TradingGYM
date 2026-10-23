import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { JSDOM } from 'jsdom';
import { afterEach, describe, expect, it } from 'vitest';

let guide;

afterEach(() => guide?.window.close());

async function loadGuide() {
  const source = readFileSync(resolve(process.cwd(), '..', 'trading-gym-visualization.html'), 'utf8');
  guide = new JSDOM(source, {
    runScripts: 'dangerously',
    beforeParse(window) {
      window.HTMLElement.prototype.scrollIntoView = () => {};
    },
  });
  await new Promise(resolveReady => guide.window.document.addEventListener('DOMContentLoaded', resolveReady, { once: true }));
  return guide.window;
}

describe('offline architecture guide', () => {
  it('opens and closes SVG component details with the keyboard', async () => {
    const window = await loadGuide();
    const node = window.document.querySelector('#arch-svg-container [data-id="api"]');
    const panel = window.document.getElementById('inline-detail');
    node.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(panel.classList.contains('open')).toBe(true);
    expect(panel.textContent).toContain('apps/api/openapi.py');
    node.dispatchEvent(new window.KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    expect(panel.classList.contains('open')).toBe(false);
  });

  it('translates open component details and switches theme', async () => {
    const window = await loadGuide();
    window.document.querySelector('#arch-svg-container [data-id="api"]').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    window.toggleLang();
    expect(window.document.documentElement.lang).toBe('zh');
    expect(window.document.getElementById('panel-title').textContent).toBe('接口网关');
    window.toggleTheme();
    expect(window.document.documentElement.dataset.theme).toBe('light');
  });
});
