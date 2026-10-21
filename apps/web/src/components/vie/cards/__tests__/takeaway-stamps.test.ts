import { describe, it, expect } from 'vitest';
import { splitTakeawayStamps } from '../takeaway-stamps';

describe('splitTakeawayStamps', () => {
  it('should read a closing m:ss marker as seconds when the takeaway ends with one', () => {
    expect(splitTakeawayStamps('Pencils down at 37signals [20:45]')).toEqual({
      text: 'Pencils down at 37signals',
      stamps: [1245],
    });
  });

  it('should read an h:mm:ss marker as seconds when the moment is past the hour', () => {
    expect(splitTakeawayStamps('Total optimism [1:00:25]').stamps).toEqual([3625]);
  });

  it('should return the claim untouched with no stamps when there is no marker', () => {
    expect(splitTakeawayStamps('English beats Ruby')).toEqual({ text: 'English beats Ruby', stamps: [] });
  });

  it('should leave a bracket that is not a clock in the text when its seconds pass 59', () => {
    expect(splitTakeawayStamps('A ratio [12:60] stays')).toEqual({ text: 'A ratio [12:60] stays', stamps: [] });
  });

  it('should lift a marker out of the middle of a claim when one appears there', () => {
    expect(splitTakeawayStamps('Opus 4.5 [11:20] is the inflection')).toEqual({
      text: 'Opus 4.5 is the inflection',
      stamps: [680],
    });
  });

  it('should keep every marker in reading order when a takeaway cites two moments', () => {
    expect(splitTakeawayStamps('Two moments [1:00] and [2:30]').stamps).toEqual([60, 150]);
  });
});
