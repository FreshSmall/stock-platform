import { Empty } from 'antd';

export default function EmptyState({
  description = '暂无数据',
}: {
  description?: string;
}) {
  return <Empty description={description} style={{ padding: 40 }} />;
}
