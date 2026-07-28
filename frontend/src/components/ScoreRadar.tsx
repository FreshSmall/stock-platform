import ReactECharts from 'echarts-for-react';
import type { AnalysisScores } from '../api/types';

// Radar chart of the 5 analysis dimensions used by H3.
const DIMENSIONS: { key: keyof AnalysisScores; label: string }[] = [
  { key: 'fundamental', label: '基本面' },
  { key: 'technical', label: '技术面' },
  { key: 'capital', label: '资金面' },
  { key: 'news', label: '消息面' },
  { key: 'risk', label: '风险' },
];

export default function ScoreRadar({ scores }: { scores: AnalysisScores | undefined }) {
  const values = DIMENSIONS.map((d) => scores?.[d.key] ?? 0);
  const option = {
    tooltip: { trigger: 'item' },
    radar: {
      indicator: DIMENSIONS.map((d) => ({ name: d.label, max: 100 })),
      radius: '65%',
      splitArea: { areaStyle: { color: ['#fafafa', '#fff'] } },
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            value: values,
            name: '评分',
            areaStyle: { color: 'rgba(22, 119, 255, 0.2)' },
            lineStyle: { color: '#1677ff' },
            itemStyle: { color: '#1677ff' },
          },
        ],
      },
    ],
  };
  return <ReactECharts option={option} style={{ height: 260 }} />;
}
