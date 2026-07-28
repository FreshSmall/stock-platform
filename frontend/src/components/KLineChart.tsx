import ReactECharts from 'echarts-for-react';
import type { EChartsInstance } from 'echarts-for-react/lib/types';
import { useRef } from 'react';
import type { IndicatorMap, KLineItem } from '../api/types';
import { UP_COLOR, DOWN_COLOR } from '../utils/format';

// K-line technical chart (H2).
//
// Three vertically-stacked, x-axis-shared grids:
//   1. Main: candlestick + MA5/MA10/MA20 overlays.
//   2. Volume bars, red/green by the day's direction.
//   3. Technical sub-chart driven by `activeIndicator`: MACD (dif/dea/macd
//      histogram) or KDJ (k/d/j lines).
//
// `indicators.ma` is optional — when absent we still render candle+volume.
// `indicators.macd` / `indicators.kdj` feed grid 3.

const PCT_ZOOM_START = 70; // show the most recent ~30% of bars initially.

type Props = {
  kline: KLineItem[];
  indicators: IndicatorMap;
  activeIndicator: 'macd' | 'kdj';
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

  // Sub-chart series depend on the active indicator.
  const sub = buildSubSeries(activeIndicator, dates, indicators);
  const subYRange = sub.yRange;

  const option: Record<string, unknown> = {
    animation: false,
    backgroundColor: '#fff',
    legend: {
      top: 0,
      data: ['MA5', 'MA10', 'MA20', ...sub.legendNames],
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
    grid: [
      { left: 56, right: 16, top: 32, height: '52%' }, // main
      { left: 56, right: 16, top: '64%', height: '12%' }, // volume
      { left: 56, right: 16, top: '79%', height: '16%' }, // sub
    ],
    xAxis: [
      candleXAxis(dates),
      { ...candleXAxis(dates), gridIndex: 1, axisLabel: { show: false } },
      { ...candleXAxis(dates), gridIndex: 2 },
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

function buildSubSeries(
  active: 'macd' | 'kdj',
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

