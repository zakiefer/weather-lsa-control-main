import { z } from 'zod';

declare const LayerOpacitySchema: z.ZodRecord<z.ZodString, z.ZodNumber>;
type LayerOpacity = z.infer<typeof LayerOpacitySchema>;
declare const MapStateSchema: z.ZodObject<{
    center: z.ZodDefault<z.ZodTuple<[z.ZodNumber, z.ZodNumber], null>>;
    zoom: z.ZodDefault<z.ZodNumber>;
    timestamp: z.ZodOptional<z.ZodNumber>;
    opacities: z.ZodDefault<z.ZodRecord<z.ZodString, z.ZodNumber>>;
}, "strip", z.ZodTypeAny, {
    center: [number, number];
    zoom: number;
    opacities: Record<string, number>;
    timestamp?: number | undefined;
}, {
    center?: [number, number] | undefined;
    zoom?: number | undefined;
    timestamp?: number | undefined;
    opacities?: Record<string, number> | undefined;
}>;
type MapState = z.infer<typeof MapStateSchema>;

export { type LayerOpacity, LayerOpacitySchema, type MapState, MapStateSchema };
