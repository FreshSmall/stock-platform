// A-share convention: red = up, green = down
export const UP_COLOR = '#f5222d';
export const DOWN_COLOR = '#52c41a';
export const FLAT_COLOR = '#8c8c8c';

export function colorForChange(v: number | null | undefined): string {
  if (v === null || v === undefined || isNaN(Number(v))) return FLAT_COLOR;
  const n = Number(v);
  if (n > 0) return UP_COLOR;
  if (n < 0) return DOWN_COLOR;
  return FLAT_COLOR;
}

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return '--';
  return `${Number(v).toFixed(digits)}%`;
}

export function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return '--';
  // 亿 / 万
  const n = Number(v);
  if (Math.abs(n) >= 1e8) return `${(n / 1e8).toFixed(2)}亿`;
  if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(2)}万`;
  return n.toFixed(2);
}

export function fmtPrice(v: number | null | undefined): string {
  if (v === null || v === undefined) return '--';
  return Number(v).toFixed(2);
}

// Format an amount (in 元) as 亿元 with 2 decimals. Used for table cells where
// the backend returns raw 元 amounts and the UI wants a fixed 亿元 scale.
export function fmtYi(v: number | null | undefined): string {
  if (v === null || v === undefined) return '--';
  return (Number(v) / 1e8).toFixed(2);
}
