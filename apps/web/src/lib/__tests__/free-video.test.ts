import { describe, it, expect } from 'vitest';
import { freeVideosPerWindow } from '../free-video';

const BASE = { available: true, nextFreeAt: null, maxVideoMinutes: 30 };

describe('freeVideosPerWindow', () => {
  it.each([0, 1, 2, 10])('should read %i from the server', (perWindow) => {
    expect(freeVideosPerWindow({ ...BASE, perWindow })).toBe(perWindow);
  });

  it('should read 0 as off, not as missing', () => {
    expect(freeVideosPerWindow({ ...BASE, perWindow: 0 })).toBe(0);
  });

  it('should treat a server that does not send the count as the standard single free video', () => {
    expect(freeVideosPerWindow(BASE)).toBe(1);
  });

  it.each([-1, Number.NaN, Number.POSITIVE_INFINITY])('should treat %s as the standard single free video', (perWindow) => {
    expect(freeVideosPerWindow({ ...BASE, perWindow })).toBe(1);
  });
});
