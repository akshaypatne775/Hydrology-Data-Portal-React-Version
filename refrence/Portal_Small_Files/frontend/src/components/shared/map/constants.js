export const SHAPE_COLORS = {
  RCC: '#0e3e49',
  'Shed Tin': '#f4a261',
  'Open Space': '#28a745',
  Kaccha: '#e07a5f',
}

export function toSqftFromSqm(sqm) {
  return parseFloat((Number(sqm || 0) * 10.7639).toFixed(2))
}
