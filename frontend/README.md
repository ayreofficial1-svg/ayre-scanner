# Swing Scanner — React Frontend

Vite + React + TypeScript frontend for the Nifty 500 Swing Scanner.

## Setup

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 — API calls proxy to Flask on http://localhost:5000

## Build for production

```bash
npm run build
```

Outputs to `../nifty_scanner/static/` — Flask serves it automatically.

## Folder structure

```
frontend/
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
└── src/
    ├── main.tsx
    ├── App.tsx
    ├── index.css
    ├── types.ts
    ├── utils.ts
    └── components/
        ├── Clock.tsx
        ├── ScanRing.tsx
        ├── SignalCard.tsx
        └── WatchlistTable.tsx
```

## Dev workflow

1. Terminal 1: `cd nifty_scanner && python main.py`
2. Terminal 2: `cd frontend && npm run dev`
3. Open http://localhost:5173

The Vite dev server proxies `/api/*` to Flask on port 5000.
