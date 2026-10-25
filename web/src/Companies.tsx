import { Badge, Card, Theme } from '@radix-ui/themes';
import { cacheShare, formatTokens, volume } from './data';
import type { Company, Snapshot } from './data';
import { sum, UsageView } from './UsageView';
import { useSource } from './workspace';

export const companyRoute = (company: Company): string => `/companies/${encodeURIComponent(company)}`;

function CompanyIndex({ data }: { data: Snapshot }) {
  const { prefix, codex } = useSource();
  return <>
    <div className="page-heading"><div><p className="eyebrow">MODEL COMPANIES</p>
      <h1>Companies</h1><p>OpenAI and DeepSeek, plus up to three companies ranked by all-date token volume.</p>
    </div><Badge>Five slots</Badge></div>
    <p className="muted">{codex ? 'Accepted Codex rows only; explicit nested child roles where supported.' : 'Parent + linked non-SDD child usage.'} Unclassified usage and companies outside these slots remain in Overview.</p>
    <section className="company-grid" aria-label="Company slots">
      {data.companySlots.map((company, index) => {
        const totals = sum(data.rows.filter((row) => company !== null && row.company === company));
        return <Card asChild key={company ?? `empty-${index}`}>
          <article className="company-card">
            <Badge>{index < 2 ? 'Fixed slot' : company ? 'By token volume' : 'Unfilled slot'}</Badge>
            <h2>{company ? <a href={`#${prefix}${companyRoute(company)}`}>{company}</a> : `Unfilled company slot ${index - 1}`}</h2>
            {company ? <><strong>{formatTokens(volume(totals))} tokens</strong>
              <p>Cache-read share: {cacheShare(totals)}</p>
              {volume(totals) === 0n && <p>No measured token usage for {company}.</p>}
              <a href={`#${prefix}${companyRoute(company)}`}>View {company} details →</a>
            </> : <p>No additional company with measured token usage for this slot.</p>}
          </article>
        </Card>;
      })}
    </section>
  </>;
}

function CompanyDetail({ data, company }: { data: Snapshot; company: Company }) {
  const { prefix, codex } = useSource();
  return <>
    <a className="company-back" href={`#${prefix}/companies`}>← Back to Companies</a>
    <div className="page-heading"><div><p className="eyebrow">COMPANY DETAIL</p>
      <h1>{company}</h1><p>Models attributed by Python classification, not the provider's display name.</p>
    </div><Badge>{codex ? 'Accepted Codex rows' : 'Parent + linked children'}</Badge></div>
    <UsageView rows={data.rows.filter((row) => row.company === company)} name={company} scope="Selected company" />
  </>;
}

export function Companies({ data, company }: { data: Snapshot; company?: Company }) {
  return <Theme appearance="dark" accentColor="violet" grayColor="slate" hasBackground={false} className="companies-view">
    {company ? <CompanyDetail key={company} data={data} company={company} /> : <CompanyIndex data={data} />}
  </Theme>;
}
