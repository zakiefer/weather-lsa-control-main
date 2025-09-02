export default async function LivePage({ searchParams }: { searchParams: { states?: string } }) {
  const base = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000';
  const params = new URLSearchParams();
  if (searchParams?.states) params.set('states', searchParams.states);
  const res = await fetch(`${base}/v1/alerts/live?${params.toString()}`, { cache: 'no-store' });
  const json = await res.json();
  return (
    <main>
      <h1>Live Alerts</h1>
      <pre data-testid="live-alerts">{JSON.stringify(json, null, 2)}</pre>
    </main>
  );
}
