import { getMapState } from './getMapState';

export default async function MapPage() {
  const state = await getMapState();
  return (
    <main>
      <h1>Map</h1>
      <pre data-testid="map-state">{JSON.stringify(state, null, 2)}</pre>
    </main>
  );
}
