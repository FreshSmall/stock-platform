import { Select } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { searchStocks } from '../api/stock';

export default function StockSearch() {
  const nav = useNavigate();
  const [opts, setOpts] = useState<{ value: string; label: string }[]>([]);
  const [t, setT] = useState<string>();

  useEffect(() => {
    if (!t) {
      setOpts([]);
      return;
    }
    const id = setTimeout(async () => {
      try {
        const items = await searchStocks(t, 10);
        setOpts(
          items.map((i: any) => ({
            value: i.stock_code,
            label: `${i.stock_name} (${i.stock_code})`,
          })),
        );
      } catch {
        setOpts([]);
      }
    }, 250);
    return () => clearTimeout(id);
  }, [t]);

  return (
    <Select
      showSearch
      placeholder="搜索股票代码/名称"
      style={{ width: '100%' }}
      filterOption={false}
      onSearch={setT}
      options={opts}
      onSelect={(v: string) => nav(`/stock/${v}`)}
    />
  );
}
