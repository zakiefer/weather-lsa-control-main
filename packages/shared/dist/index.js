// src/schemas/map.ts
import { z } from "zod";
var LayerOpacitySchema = z.record(z.string(), z.number().min(0).max(1));
var MapStateSchema = z.object({
  center: z.tuple([z.number(), z.number()]).default([37.8, -96.9]),
  zoom: z.number().min(0).max(20).default(4),
  timestamp: z.number().int().optional(),
  opacities: LayerOpacitySchema.default({})
});
export {
  LayerOpacitySchema,
  MapStateSchema
};
