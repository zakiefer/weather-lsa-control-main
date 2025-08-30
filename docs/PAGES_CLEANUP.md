# Pages cleanup

## Keep

- ui/pages/Map.py — Main map experience
- ui/pages/Live.py — Live dashboard
- ui/pages/History.py — Historical events
- ui/pages/Settings.py — Settings and helpers
- ui/pages/Profile.py — User profile
- ui/pages/Health.py — Health/status
- ui/pages/Reports.py — Reports
- ui/pages/Ads.py — Ads tooling

## Remove/Hide (duplicates/placeholders)

- ui/pages/MapLayers.py — deprecated (stubbed to stop)
- ui/pages/_MapLayers.py — deprecated hidden
- ui/pages/_map_layers.py — deprecated hidden
- ui/pages/_ZZZ__DELETE_ME__MapLayers.md — placeholder hidden

Note: Files prefixed with “_” are ignored by Streamlit sidebar. The visible MapLayers.py is retained as a visible stub that immediately stops with guidance, but can be safely deleted in a follow-up.
