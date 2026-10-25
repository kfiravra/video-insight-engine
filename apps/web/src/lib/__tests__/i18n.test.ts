import { describe, it, expect } from 'vitest';
import { getLabels } from '../i18n';

describe('getLabels', () => {
  it('should return the English labels when the language is English', () => {
    expect(getLabels('en').verdict).toBe('Verdict');
  });

  it('should return the Hebrew labels when the language is Hebrew', () => {
    expect(getLabels('he').verdict).toBe('פסק הדין');
  });

  it('should return the Arabic labels when the language is Arabic', () => {
    expect(getLabels('ar').verdict).toBe('الحكم');
  });

  it('should fall back to the English labels when the language has none', () => {
    expect(getLabels('fr')).toBe(getLabels('en'));
  });

  it('should fill every English key in Hebrew and Arabic when a key is added', () => {
    const keys = Object.keys(getLabels('en')).sort();

    expect([Object.keys(getLabels('he')).sort(), Object.keys(getLabels('ar')).sort()]).toEqual([keys, keys]);
  });
});
