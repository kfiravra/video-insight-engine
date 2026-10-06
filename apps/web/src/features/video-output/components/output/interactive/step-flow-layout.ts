const COLUMN_OFFSET = 180;
const ROW_SPACING = 160;

/** Vertical zigzag layout — alternates between left and right of center. */
export function zigzagLayout(count: number): Array<{ x: number; y: number }> {
  return Array.from({ length: count }, (_, index) => ({
    x: index % 2 === 0 ? -COLUMN_OFFSET : COLUMN_OFFSET,
    y: index * ROW_SPACING,
  }));
}
