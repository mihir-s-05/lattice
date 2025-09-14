# LATTICE Web UI

React single-page application for monitoring and interacting with LATTICE runs.

## Development

```bash
npm install
npm run dev
```

The UI expects the backend API base URL in `VITE_API_BASE` (defaults to `/api`).

## Tests

```bash
npm test
```

## Demo Script

1. Start the development server: `npm run dev`.
2. Open http://localhost:5173 and navigate to a run.
3. Chat messages stream in the main view while agent swimlanes update.
4. Use the header buttons to open Decision, Web Search, or Huddle panels.
