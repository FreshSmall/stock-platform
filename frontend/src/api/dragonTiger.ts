import client from './client';
import type { DragonTigerRow, DragonTigerSeats } from './types';

// V1.5 Stage H — dragon-tiger (龙虎榜) endpoints.

// GET /dragon-tiger?date= — the listed stocks for one day (latest if omitted).
export const fetchDragonTiger = (date?: string) =>
  client
    .get<DragonTigerRow[]>('/dragon-tiger', { params: { date } })
    .then((r) => r.data);

// GET /dragon-tiger/{code}/{date}/seats — top-5 buy/sell seats.
export const fetchDragonTigerSeats = (code: string, date: string) =>
  client
    .get<DragonTigerSeats>(`/dragon-tiger/${code}/${date}/seats`)
    .then((r) => r.data);
