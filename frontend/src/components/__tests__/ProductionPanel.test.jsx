import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, test } from 'vitest';

describe('ProductionPanel authority contract', () => {
  test('keeps live authority and non-custodial language visible', () => {
    const source = fs.readFileSync(path.resolve('src/components/ProductionPanel.jsx'), 'utf8');
    expect(source).toContain('Production Authority');
    expect(source).toMatch(/single production coordinator/i);
    expect(source).toMatch(/non-custodial/i);
  });
});
