import { memo } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import './charts.css'

export type BarDatum = {
  name: string
  value: number
}

type HydrologyBarChartProps = {
  title: string
  data: BarDatum[]
}

export const HydrologyBarChart = memo(function HydrologyBarChart({ title, data }: HydrologyBarChartProps) {
  return (
    <section className="ch-card" aria-label={title}>
      <header className="ch-card__head">
        <h3>{title}</h3>
      </header>
      <div className="ch-plot">
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.12)" />
            <XAxis dataKey="name" stroke="rgba(232,244,246,0.7)" tick={{ fontSize: 11 }} />
            <YAxis stroke="rgba(232,244,246,0.7)" tick={{ fontSize: 11 }} />
            <Tooltip
              contentStyle={{
                background: '#0f2a31',
                border: '1px solid rgba(255,255,255,0.2)',
                borderRadius: 8,
                color: '#e8f4f6',
              }}
            />
            <Bar dataKey="value" fill="#22d3ee" radius={[6, 6, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
})

export default HydrologyBarChart
