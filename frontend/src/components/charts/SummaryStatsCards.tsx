import { memo } from 'react'
import './charts.css'

export type SummaryStat = {
  id: string
  label: string
  value: string
}

type SummaryStatsCardsProps = {
  title: string
  stats: SummaryStat[]
}

export const SummaryStatsCards = memo(function SummaryStatsCards({ title, stats }: SummaryStatsCardsProps) {
  return (
    <section className="ch-card" aria-label={title}>
      <header className="ch-card__head">
        <h3>{title}</h3>
      </header>
      <div className="ch-summary-grid">
        {stats.map((stat) => (
          <article key={stat.id} className="ch-summary-item">
            <p className="ch-summary-item__label">{stat.label}</p>
            <p className="ch-summary-item__value">{stat.value}</p>
          </article>
        ))}
      </div>
    </section>
  )
})

export default SummaryStatsCards
