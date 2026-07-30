import client from './client';
import type { SectorRow, SectorStock } from './types';

// V1.5 Stage H — sector (板块) rank + detail endpoints.

export type SectorType = 'industry' | 'concept';
export type SectorSortKey = 'pct_change' | 'amount' | 'main_net_inflow';

export const fetchSectors = (
  type?: SectorType,
  sort?: SectorSortKey,
  limit?: number,
) =>
  client
    .get<SectorRow[]>('/sector', { params: { type, sort, limit } })
    .then((r) => r.data);

export const fetchSector = (code: string) =>
  client.get<SectorRow>(`/sector/${code}`).then((r) => r.data);

export const fetchSectorStocks = (code: string) =>
  client
    .get<SectorStock[]>(`/sector/${code}/stocks`)
    .then((r) => r.data);
