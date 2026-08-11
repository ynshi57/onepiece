# VQASee PlantUML diagrams

Use one file per diagram in Cursor / VS Code PlantUML extension:

- `01-system-architecture.puml`
- `02-iphone-frame-flow.puml`
- `03-mode-switch-surroundings-to-walking.puml`
- `04-backend-qwen-flow.puml`
- `05-diagnostic-capture-flow.puml`

## Cursor plugin notes

The `jebbs.plantuml` extension usually needs either:

1. Java installed for local rendering, or
2. PlantUML server rendering enabled.

If local preview fails with Java errors, use server render in Cursor settings:

```json
"plantuml.render": "PlantUMLServer",
"plantuml.server": "https://www.plantuml.com/plantuml"
```

For privacy-sensitive diagrams, install Java locally instead of server render.

## Why split files?

Some editor plugins do not reliably preview multiple `@startuml` blocks in one file. These files intentionally contain exactly one diagram each.
