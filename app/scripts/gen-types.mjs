// Generate TypeScript types for the engine document from shared/schema.
import { compileFromFile } from 'json-schema-to-typescript'
import { writeFileSync } from 'node:fs'

const ts = await compileFromFile('../shared/schema/document.schema.json', {
  bannerComment: '/* Generated from shared/schema/document.schema.json by scripts/gen-types.mjs. Do not edit. */',
  additionalProperties: false,
  unreachableDefinitions: true
})
writeFileSync('src/renderer/src/engine/document.d.ts', ts)
console.log('wrote src/renderer/src/engine/document.d.ts')
