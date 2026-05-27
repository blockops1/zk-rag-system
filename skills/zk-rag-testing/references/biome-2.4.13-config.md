# Biome 2.4.13 — Verified Working Config

**Location:** `<REPO>website/biome.json`
**Version tested:** 2.4.13 (from `website/node_modules/.bin/biome --version`)

## Working Configuration

```json
{
  "$schema": "https://biomejs.dev/schemas/2.4.13/schema.json",
  "vcs": {
    "enabled": true,
    "clientKind": "git",
    "useIgnoreFile": true
  },
  "files": {
    "ignoreUnknown": false
  },
  "formatter": {
    "enabled": true,
    "indentStyle": "tab",
    "indentWidth": 4
  },
  "linter": {
    "enabled": true,
    "rules": {
      "recommended": true
    }
  },
  "javascript": {
    "formatter": {
      "quoteStyle": "double"
    }
  },
  "assist": {
    "enabled": true,
    "actions": {
      "source": {
        "organizeImports": "on"
      }
    }
  }
}
```

## Schema Quirks (Biome 2.4.13)

### These keys do NOT exist in this version
- `semicolons` — not a valid formatter key
- `trailingCommas` at `formatter` level — does not exist
- `noBarrel` — invalid; use `noBarrelFile` (check via `biome explain noBarrelFile`)
- `useLet` — not a valid style rule
- `organizer` — not a valid top-level key (import sorting is under `assist`)
- `files.ignore` — not valid; use `files.ignoreUnknown` or `files.includes`

### Key nesting is different from documentation
- `quoteStyle` for JS lives at `javascript.formatter.quoteStyle`, NOT at `formatter.quoteStyle`
- `indentStyle` and `indentWidth` live at `formatter`, not under `javascript.formatter`

### `useIgnoreFile: true` prerequisite
- Requires a `.gitignore` file in the same directory as `biome.json`
- Without it, Biome exits: `Biome couldn't find an ignore file`

## Fix Command Sequence

```bash
cd website

# 1. Show all errors (lint + format diff)
npm run lint

# 2. Safe auto-fixes only
npm run lint:fix

# 3. Unsafe fixes (noUselessSwitchCase, noUnusedVariables, useOptionalChain)
node_modules/.bin/biome check --write --unsafe js/

# 4. Format
npm run format

# 5. Gate — must pass for CI
npm run ci
```

## Rule Verification

Use `biome explain <rule-name>` to check if a rule exists before adding it to `linter.rules`. Some rules that appear in Biome documentation or blog posts don't exist in 2.4.13.

Verified valid rules in 2.4.13:
- `recommended` (boolean)
- `correctness.noUnusedVariables` (warn/error)
- `correctness.noUnusedImports` (error)
- `complexity.noUselessSwitchCase` (error)
- `complexity.useOptionalChain` (error)
- `style.useConst` (error)
- `style.noNegationElse` (off)
- `security.noDangerouslySetInnerHtml` (error)
