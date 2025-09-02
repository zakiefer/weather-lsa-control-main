"use client";
import { MapStateSchema } from "@shared/schemas/map";

export async function fetchMapState() {
  const base = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
  const res = await fetch(`${base}/v1/map/state`);
  const json = await res.json();
  return MapStateSchema.parse(json);
}
