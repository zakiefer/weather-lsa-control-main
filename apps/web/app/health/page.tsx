export default async function HealthPage() {
  const base = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000';
  const res = await fetch(`${base}/healthz`, { cache: 'no-store' });
  const json = await res.json();
  return (
    <main>
      <h1>Health</h1>
      <pre>{JSON.stringify(json, null, 2)}</pre>
    </main>
  );
}
