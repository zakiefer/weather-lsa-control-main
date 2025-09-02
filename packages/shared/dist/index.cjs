"use strict";
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// src/index.ts
var src_exports = {};
__export(src_exports, {
  LayerOpacitySchema: () => LayerOpacitySchema,
  MapStateSchema: () => MapStateSchema
});
module.exports = __toCommonJS(src_exports);

// src/schemas/map.ts
var import_zod = require("zod");
var LayerOpacitySchema = import_zod.z.record(import_zod.z.string(), import_zod.z.number().min(0).max(1));
var MapStateSchema = import_zod.z.object({
  center: import_zod.z.tuple([import_zod.z.number(), import_zod.z.number()]).default([37.8, -96.9]),
  zoom: import_zod.z.number().min(0).max(20).default(4),
  timestamp: import_zod.z.number().int().optional(),
  opacities: LayerOpacitySchema.default({})
});
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  LayerOpacitySchema,
  MapStateSchema
});
