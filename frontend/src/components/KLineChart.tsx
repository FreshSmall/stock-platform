import ReactECharts from 'echarts-for-react';
import type { EChartsInstance } from 'echarts-for-react/lib/types';
import { useRef } from 'react';
import type { IndicatorMap, KLineItem } from '../api/types';
import { UP_COLOR, DOWN_COLOR } from '../utils/format';

// K-line technical chart (H2, extended in V1.5 Stage H).
//
// Three vertically-stacked, x-axis-shared grids:
//   1. Main: candlestick + MA5/MA10/MA20 overlays (+ EMA/BOLL overlays when
//      those indicator tabs are active).
//   2. Volume bars, red/green by the day's direction.
//   3. Technical sub-chart driven by `activeIndicator`: MACD (dif/dea/macd
//      histogram), KDJ (k/d/j), RSI (rsi6/12/24). EMA and BOLL are overlays on
//      the main grid, so when they are active the sub-chart is hidden.
//
// `indicators.ma` is optional — when absent we still render candle+volume.
// `indicators.macd` / `indicators.kdj` / `indicators.rsi` feed grid 3.

const PCT_ZOOM_START = 70; // show the most recent ~30% of bars initially.

export type IndicatorKey = 'macd' | 'kdj' | 'ema' | 'rsi' | 'boll';

type Props = {
  kline: KLineItem[];
  indicators: IndicatorMap;
  activeIndicator: IndicatorKey;
};

export default function KLineChart({ kline, indicators, activeIndicator }: Props) {
  const ref = useRef<ReactECharts | null>(null);

  const dates = kline.map((k) => k.trade_date);

  // Candlestick: [date, open, close, low, high].
  const ohlc = kline.map((k) => [
    k.open ?? 0,
    k.close ?? 0,
    k.low ?? 0,
    k.high ?? 0,
  ]);

  const volume = kline.map((k) => ({
    value: k.volume ?? 0,
    // Red on up days, green on down — matches the candle color.
    itemStyle: { color: colorForDay(k), borderColor: colorForDay(k) },
  }));

  // Index indicator rows by date so we can align them with the candle x-axis
  // even if their lengths differ (they shouldn't, but be safe).
  const ma = indexByDate(indicators.ma ?? []);
  const ma5 = dates.map((d) => ma.get(d)?.ma5 ?? '-');
  const ma10 = dates.map((d) => ma.get(d)?.ma10 ?? '-');
  const ma20 = dates.map((d) => ma.get(d)?.ma20 ?? '-');

  const maSeries = [
    lineSeries('MA5', ma5, '#fadb14'),
    lineSeries('MA10', ma10, '#13c2c2'),
    lineSeries('MA20', ma20, '#722ed1'),
  ];

  // Overlay series (EMA / BOLL) sit on the main grid alongside MA. These only
  // exist when their indicator tab is active.
  const overlays = buildOverlaySeries(activeIndicator, dates, indicators);

  // Sub-chart series depend on the active indicator. EMA/BOLL are overlay-only,
  // so they contribute no sub-chart series and we collapse grid 3 for them.
  const sub = buildSubSeries(activeIndicator, dates, indicators);
  const subYRange = sub.yRange;
  const hasSub = sub.series.length > 0;

  const option: Record<string, unknown> = {
    animation: false,
    backgroundColor: '#fff',
    legend: {
      top: 0,
      data: ['MA5', 'MA10', 'MA20', ...overlays.legendNames, ...sub.legendNames],
      textStyle: { fontSize: 11 },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      borderWidth: 1,
      borderColor: '#d9d9d9',
      // Render OHLC + indicators in a compact table.
      formatter: (params: any[]) => {
        if (!params.length) return '';
        const date = params[0].axisValue;
        const byName = new Map<string, any>();
        for (const p of params) byName.set(p.seriesName, p);
        const rows: string[] = [`<b>${date}</b>`];
        const o = byName.get('日K')?.value;
        if (o) {
          rows.push(
            `开 ${num(o[1])} 收 ${num(o[2])} 低 ${num(o[3])} 高 ${num(o[4])}`,
          );
        }
        for (const name of ['MA5', 'MA10', 'MA20', '成交量']) {
          const v = byName.get(name)?.value;
          if (v != null && v !== '-') rows.push(`${name} ${num(v)}`);
        }
        for (const name of sub.legendNames) {
          const v = byName.get(name)?.value;
          if (v != null && v !== '-') rows.push(`${name} ${num(v)}`);
        }
        return rows.join('<br/>');
      },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: hasSub
      ? [
          { left: 56, right: 16, top: 32, height: '52%' }, // main
          { left: 56, right: 16, top: '64%', height: '12%' }, // volume
          { left: 56, right: 16, top: '79%', height: '16%' }, // sub
        ]
      : [
          { left: 56, right: 16, top: 32, height: '64%' }, // main (taller)
          { left: 56, right: 16, top: '70%', height: '16%' }, // volume
          { left: 56, right: 16, top: '90%', height: '0%' }, // sub hidden
        ],
    xAxis: [
      candleXAxis(dates),
      { ...candleXAxis(dates), gridIndex: 1, axisLabel: { show: false } },
      { ...candleXAxis(dates), gridIndex: 2, axisLabel: { show: false } },
    ],
    yAxis: [
      { scale: true, splitLine: { lineStyle: { color: '#f0f0f0' } } },
      { gridIndex: 1, splitNumber: 2, axisLabel: { inside: true, fontSize: 10 } },
      { gridIndex: 2, scale: true, ...(subYRange ? { min: subYRange[0], max: subYRange[1] } : {}) },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2], start: PCT_ZOOM_START, end: 100 },
      {
        type: 'slider',
        xAxisIndex: [0, 1, 2],
        bottom: 4,
        height: 16,
        start: PCT_ZOOM_START,
        end: 100,
        showDetail: false,
      },
    ],
    series: [
      {
        name: '日K',
        type: 'candlestick',
        data: ohlc,
        itemStyle: {
          color: UP_COLOR,
          color0: DOWN_COLOR,
          borderColor: UP_COLOR,
          borderColor0: DOWN_COLOR,
        },
      },
      ...maSeries,
      ...overlays.series,
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volume,
      },
      ...sub.series,
    ],
  };

  return (
    <ReactECharts
      ref={(r) => {
        ref.current = r;
      }}
      option={option}
      notMerge
      lazyUpdate
      style={{ height: 520 }}
      // Double-click resets the zoom window to the full range.
      onEvents={{
        dblclick: () => {
          const inst: EChartsInstance | undefined = ref.current?.getEchartsInstance();
          inst?.dispatchAction({ type: 'dataZoom', start: 0, end: 100 });
        },
      }}
    />
  );
}

// ---- helpers ---------------------------------------------------------------

function colorForDay(k: KLineItem): string {
  if ((k.close ?? 0) >= (k.open ?? 0)) return UP_COLOR;
  return DOWN_COLOR;
}

function num(v: unknown): string {
  if (v == null || v === '') return '--';
  const n = Number(v);
  if (!isFinite(n)) return '--';
  return n.toFixed(2);
}

function indexByDate<T extends { trade_date: string }>(rows: T[]): Map<string, T> {
  const m = new Map<string, T>();
  for (const r of rows) m.set(r.trade_date, r);
  return m;
}

function lineSeries(name: string, data: any[], color: string) {
  return {
    name,
    type: 'line',
    data,
    smooth: true,
    symbol: 'none',
    lineStyle: { width: 1, color },
    // Hide a series cleanly when all its values are missing.
    connectNulls: false,
  };
}

function candleXAxis(dates: string[]) {
  return {
    type: 'category',
    data: dates,
    boundaryGap: false,
    axisLine: { onZero: false },
    splitLine: { show: false },
    axisLabel: { fontSize: 10 },
    min: 'dataMin',
    max: 'dataMax',
  };
}

type SubResult = {
  series: any[];
  legendNames: string[];
  yRange?: [number, number];
};

// Overlay series (EMA / BOLL) are drawn on the main grid (xAxisIndex 0). They
// return an empty list when their indicator data is absent or the active tab is
// not one of theirs.
function buildOverlaySeries(
  active: IndicatorKey,
  dates: string[],
  indicators: IndicatorMap,
): SubResult {
  if (active === 'ema') {
    const rows = indexByDate(indicators.ema ?? []);
    const ema12 = dates.map((d) => rows.get(d)?.ema12 ?? '-');
    const ema26 = dates.map((d) => rows.get(d)?.ema26 ?? '-');
    return {
      legendNames: ['EMA12', 'EMA26'],
      series: [
        { name: 'EMA12', type: 'line', data: ema12, xAxisIndex: 0, yAxisIndex: 0, symbol: 'none', lineStyle: { width: 1, color: '#fa541c' } },
        { name: 'EMA26', type: 'line', data: ema26, xAxisIndex: 0, yAxisIndex: 0, symbol: 'none', lineStyle: { width: 1, color: '#2f54eb' } },
      ],
    };
  }
  if (active === 'boll') {
    const rows = indexByDate(indicators.boll ?? []);
    const up = dates.map((d) => rows.get(d)?.up ?? '-');
    const mid = dates.map((d) => rows.get(d)?.mid ?? '-');
    const low = dates.map((d) => rows.get(d)?.low ?? '-');
    return {
      legendNames: ['BOLL上轨', 'BOLL中轨', 'BOLL下轨'],
      series: [
        { name: 'BOLL上轨', type: 'line', data: up, xAxisIndex: 0, yAxisIndex: 0, symbol: 'none', lineStyle: { width: 1, color: '#8c8c8c', type: 'dashed' } },
        { name: 'BOLL中轨', type: 'line', data: mid, xAxisIndex: 0, yAxisIndex: 0, symbol: 'none', lineStyle: { width: 1, color: '#1677ff' } },
        { name: 'BOLL下轨', type: 'line', data: low, xAxisIndex: 0, yAxisIndex: 0, symbol: 'none', lineStyle: { width: 1, color: '#8c8c8c', type: 'dashed' } },
      ],
    };
  }
  return { legendNames: [], series: [] };
}

function buildSubSeries(
  active: IndicatorKey,
  dates: string[],
  indicators: IndicatorMap,
): SubResult {
  if (active === 'macd') {
    const rows = indexByDate(indicators.macd ?? []);
    const dif = dates.map((d) => rows.get(d)?.dif ?? '-');
    const dea = dates.map((d) => rows.get(d)?.dea ?? '-');
    const macd = dates.map((d) => rows.get(d)?.macd ?? 0);
    const hist = macd.map((v) => ({
      value: v,
      itemStyle: { color: v >= 0 ? UP_COLOR : DOWN_COLOR },
    }));
    return {
      legendNames: ['DIF', 'DEA', 'MACD'],
      series: [
        { name: 'DIF', type: 'line', data: dif, xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, color: '#fff' } },
        { name: 'DEA', type: 'line', data: dea, xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, color: '#ffec3d' } },
        { name: 'MACD', type: 'bar', data: hist, xAxisIndex: 2, yAxisIndex: 2 },
      ],
    };
  }
  if (active === 'rsi') {
    const rows = indexByDate(indicators.rsi ?? []);
    const rsi6 = dates.map((d) => rows.get(d)?.rsi6 ?? '-');
    const rsi12 = dates.map((d) => rows.get(d)?.rsi12 ?? '-');
    const rsi24 = dates.map((d) => rows.get(d)?.rsi24 ?? '-');
    return {
      legendNames: ['RSI6', 'RSI12', 'RSI24'],
      yRange: [0, 100],
      series: [
        { name: 'RSI6', type: 'line', data: rsi6, xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, color: '#f5222d' } },
        { name: 'RSI12', type: 'line', data: rsi12, xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, color: '#faad14' } },
        { name: 'RSI24', type: 'line', data: rsi24, xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, color: '#1677ff' } },
      ],
    };
  }
  if (active === 'kdj') {
    // KDJ: 0-100+ range (J can overshoot, so leave auto-scale).
    const rows = indexByDate(indicators.kdj ?? []);
    const k = dates.map((d) => rows.get(d)?.k ?? '-');
    const d = dates.map((d) => rows.get(d)?.d ?? '-');
    const j = dates.map((d) => rows.get(d)?.j ?? '-');
    return {
      legendNames: ['K', 'D', 'J'],
      yRange: [0, 100],
      series: [
        { name: 'K', type: 'line', data: k, xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, color: '#1677ff' } },
        { name: 'D', type: 'line', data: d, xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, color: '#fa8c16' } },
        { name: 'J', type: 'line', data: j, xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, color: '#722ed1' } },
      ],
    };
  }
  // ema / boll are overlay-only — no sub-chart series.
  return { legendNames: [], series: [] };
}

