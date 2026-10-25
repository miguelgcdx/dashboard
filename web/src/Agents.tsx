import { Badge, Card, Theme } from '@radix-ui/themes';
import { cacheShare, formatTokens, volume } from './data';
import type { Snapshot } from './data';
import { sum, UsageView } from './UsageView';
import { AgentPanels } from './AgentPanels';

export const agentRoute = (role: string): string => `/agents/${encodeURIComponent(role)}`;

function AgentIndex({ data }: { data: Snapshot }) {
  return <>
    <div className="page-heading"><div><p className="eyebrow">CONFIGURED ROLES</p>
      <h1>Agents</h1><p>All configured non-SDD roles, including roles without measured usage.</p>
    </div><Badge>{data.roles.length} configured roles</Badge></div>
    <p className="muted">Safely linked child usage only. Parent, SDD, and unlinked child usage are excluded. Historical attribution may be incomplete.</p>
    <section className="agent-grid" aria-label="Configured agents">
      {data.roles.map((role) => {
        const rows = data.rows.filter((row) => row.role === role);
        const totals = sum(rows);
        return <Card asChild key={role}><article className="agent-card">
          <Badge>{rows.length ? 'Measured usage' : 'No measured usage'}</Badge>
          <h2><a href={`#${agentRoute(role)}`}>{role}</a></h2>
          <strong>{formatTokens(volume(totals))} tokens</strong>
          <p>Cache-read share: {cacheShare(totals)}</p>
          {rows.length === 0 && <p>No measured usage for this role in the retained snapshot.</p>}
          <a href={`#${agentRoute(role)}`}>View {role} details →</a>
        </article></Card>;
      })}
    </section>
    {data.roles.length === 0 && <p className="empty">No configured non-SDD roles in this snapshot.</p>}
  </>;
}

function AgentDetail({ data, role }: { data: Snapshot; role: string }) {
  return <>
    <a className="agent-back" href="#/agents">← Back to Agents</a>
    <div className="page-heading"><div><p className="eyebrow">AGENT DETAIL</p>
      <h1 aria-describedby="agent-detail-context">{role}</h1>
    </div><Badge>Linked children only</Badge></div>
    <p id="agent-detail-context" className="visually-hidden">Actual usage-event providers and models for this exact role, not task launch metadata or task token totals.
      {' '}Snapshot as of <time dateTime={data.stats.asOf}>{data.stats.asOf.slice(0, 16).replace('T', ' ')}</time> · Timezone: {data.stats.timezone}.
      {' '}Historical child attribution may be incomplete; parent, SDD, and unlinked usage are excluded.</p>
    <UsageView rows={data.rows.filter((row) => row.role === role)} name={role} scope="Selected role" showModelCompany
      renderPanels={(selection) => <AgentPanels {...selection}
        asOf={data.stats.asOf} role={role} />} />
  </>;
}

export function Agents({ data, role }: { data: Snapshot; role?: string }) {
  return <Theme appearance="dark" accentColor="violet" grayColor="slate" hasBackground={false} className="agents-view">
    {role !== undefined ? <AgentDetail key={role} data={data} role={role} /> : <AgentIndex data={data} />}
  </Theme>;
}
