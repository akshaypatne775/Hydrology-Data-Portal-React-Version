import { memo } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import './charts.css'

export type LineDatum = {
  name: string
  value: number
}

type HydrologyLineChartProps = {
  title: string
  data: LineDatum[]
}

export const HydrologyLineChart = memo(function HydrologyLineChart({ title, data }: HydrologyLineChartProps) {
  return (
    <section className="ch-card" aria-label={title}>
      <header className="ch-card__head">
        <h3>{title}</h3>
      </header>
      <div className="ch-plot">
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={data}>
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
            <Line
              type="monotone"
              dataKey="value"
              stroke="#5eead4"
              strokeWidth={2.5}
              dot={{ r: 3 }}
              activeDot={{ r: 5 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
})

export default HydrologyLineChart
