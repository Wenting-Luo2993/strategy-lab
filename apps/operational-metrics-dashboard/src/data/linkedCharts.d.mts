export type Point = { date: string; value: number };
export type LinkedRange = { from: number; to: number };
export type LinkedCursor = { date: string; key: string } | null;
export type ChartPeer = {
  range: (range: LinkedRange) => void;
  cursor: (cursor: LinkedCursor) => void;
};
export type ChartLink = {
  register: (peer: ChartPeer, initialRange?: LinkedRange) => () => void;
  range: (peer: ChartPeer, range: LinkedRange) => boolean;
  cursor: (peer: ChartPeer, cursor: LinkedCursor) => void;
};
export function dateFromTime(time: unknown): string | null;
export function sumDailySeries(groups: Point[][]): Point[];
export function combinedComparison(members: {
  capital: number | null; r: Point[]; prices: Point[]; closed: Point[];
}[]): { r: Point[]; benchmark: Point[]; closed: Point[] };
export function createChartLink(): ChartLink;
