# LATTICE Web UI

A production-ready React web interface for monitoring and interacting with LATTICE multi-agent workflows. This UI provides real-time visibility into parallel agent execution, Router LLM interactions, and system state through an intuitive, keyboard-friendly interface.

## Features

### Core Functionality
- **Real-time Agent Monitoring**: Live swimlanes view showing parallel agent execution with turn-by-turn progress
- **Router LLM Chat**: Interactive chat interface for communicating with the Router LLM, with markdown rendering and code block copying
- **System Event Stream**: Comprehensive timeline of all system events with context-aware filtering
- **WebSocket Integration**: Real-time updates via WebSocket connection with automatic reconnection

### Inspector Panels
- **Huddles**: View active and completed agent huddles with participant lists and transcripts
- **Decision Summaries**: Expandable decision cards showing rationale, risks, actions, and contracts
- **Web Search**: Monitor web search queries with results and extracts
- **Stage Gates**: Track stage gate status with conditions and evidence
- **Contract Tests**: Monitor test execution with metrics and results
- **Artifacts**: Browse, search, and preview artifacts with download functionality
- **Plan Graph**: Visualize plan segments and critical path progression

### User Experience
- **Keyboard Navigation**: Full keyboard support with customizable shortcuts
- **Responsive Design**: Works on desktop, tablet, and mobile devices
- **Dark/Light Mode**: System-aware theme switching
- **Collapsible Panels**: Flexible layout with resizable sidebars
- **Accessibility**: WCAG compliant with screen reader support

## Tech Stack

- **Frontend**: React 18, TypeScript, Vite
- **Styling**: Tailwind CSS with custom utilities
- **State Management**: Zustand with Immer for immutable updates
- **Data Fetching**: React Query (TanStack Query)
- **Routing**: React Router v6
- **WebSockets**: Native WebSocket API with reconnection logic
- **Markdown**: markdown-it with DOMPurify sanitization
- **Virtualization**: react-window for performance
- **Testing**: Playwright for e2e tests

## Prerequisites

- Node.js 18+ 
- npm or yarn
- LATTICE backend server running on localhost:8000 (or configured endpoint)

## Installation

1. **Clone and navigate to the webui directory**:
   ```bash
   cd lattice/webui
   ```

2. **Install dependencies**:
   ```bash
   npm install
   ```

3. **Set up environment variables**:
   ```bash
   cp .env.example .env
   ```
   
   Edit `.env` to match your backend configuration:
   ```env
   VITE_API_BASE=http://localhost:8000
   VITE_WS_BASE=ws://localhost:8000
   VITE_DEV_MODE=true
   ```

## Development

### Start the development server:
```bash
npm run dev
```

The application will be available at `http://localhost:5173`

### Build for production:
```bash
npm run build
```

### Preview production build:
```bash
npm run preview
```

### Run tests:
```bash
# Install Playwright browsers (first time only)
npx playwright install

# Run e2e tests
npm run test:e2e

# Run tests in headed mode (with browser UI)
npm run test:e2e:headed

# Run specific test file
npx playwright test tests/basic.spec.ts
```

### Lint and format:
```bash
# Check TypeScript types
npm run type-check

# Lint code
npm run lint

# Format code with Prettier
npm run format
```

## Backend Integration

The web UI expects the following backend endpoints:

### REST API
- `GET /runs` - List all runs
- `GET /runs/:run_id` - Get run details
- `GET /runs/:run_id/artifacts` - List run artifacts  
- `GET /runs/:run_id/artifacts/:path` - Download artifact
- `GET /runs/:run_id/plan_graph` - Get plan graph snapshot
- `POST /runs` - Start new run
- `POST /runs/:run_id/abort` - Abort running run
- `POST /runs/:run_id/chat` - Send message to Router LLM

### WebSocket Events
Connect to `/runs/:run_id/stream` for real-time events:

```typescript
// Event types the UI handles:
interface WebSocketEvent {
  type: 'run_status' | 'router_message' | 'agent_turn' | 'system_event' 
       | 'huddle_open' | 'huddle_complete' | 'decision_summary' 
       | 'web_search' | 'gate_update' | 'test_update' | 'artifacts_update'
  data: any
  timestamp: string
}
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_BASE` | `http://localhost:8000` | Backend REST API base URL |
| `VITE_WS_BASE` | `ws://localhost:8000` | Backend WebSocket base URL |
| `VITE_DEV_MODE` | `true` | Enable development features |
| `VITE_LOG_LEVEL` | `info` | Console logging level |

## Usage

### Navigation
- **Sidebar**: Browse and filter runs by status
- **Search**: Use `/` to focus search, filter runs by name or ID  
- **Tabs**: Switch between Chat and Swimlanes views
- **Inspector**: Toggle panels to monitor specific aspects

### Keyboard Shortcuts
| Key | Action |
|-----|--------|
| `/` | Focus search input |
| `Escape` | Clear focus/close modals |
| `c` | Switch to Chat view |
| `s` | Switch to Swimlanes view |
| `h` | Toggle Huddles panel |
| `d` | Toggle Decisions panel |
| `w` | Toggle Web Search panel |
| `g` | Toggle Gates panel |
| `t` | Toggle Tests panel |
| `a` | Toggle Artifacts panel |
| `p` | Toggle Plan Graph panel |
| `?` | Show keyboard shortcuts help |

### Chat Interface
- Type messages to interact with Router LLM
- Use `Ctrl+Enter` or `Cmd+Enter` to send
- Click code blocks to copy to clipboard
- Scroll through message history with virtualization

### Agent Swimlanes
- View parallel agent execution in columns
- Each agent shows current status and recent activity
- Click agent cards to see detailed turn history
- Artifacts, RAG queries, and tool calls are highlighted

### Inspector Panels
- **Toggle panels** using buttons or keyboard shortcuts
- **Huddles**: Click to open transcript modal
- **Decisions**: Expand cards to see full details
- **Artifacts**: Search, filter, and preview files
- **Plan Graph**: Switch between list and graph views

## Architecture

### State Management
- **Zustand Store**: Central state for runs, UI preferences, and WebSocket connections
- **React Query**: Server state caching and synchronization
- **Immer**: Immutable state updates with draft syntax

### Component Structure
```
src/
├── components/
│   ├── common/          # Reusable UI components
│   ├── views/           # Main content views (Chat, Swimlanes)
│   ├── inspector/       # Inspector panel components
│   ├── AppShell.tsx     # Main layout shell
│   └── RunView.tsx      # Run-specific layout
├── hooks/               # Custom React hooks
│   ├── useWebSocket.ts  # WebSocket connection management
│   └── useHotkeys.ts    # Keyboard shortcut handling
├── store/               # Zustand state management
├── api/                 # REST API client
├── types/               # TypeScript type definitions
└── utils/               # Utility functions
```

### WebSocket Flow
1. Connect to `/runs/:run_id/stream` when viewing a run
2. Parse incoming events and update Zustand store
3. Components subscribe to relevant state slices
4. UI updates automatically via React re-renders
5. Handle disconnections with exponential backoff reconnection

## Performance

### Optimizations
- **React.memo**: Prevent unnecessary re-renders
- **react-window**: Virtualize long lists (chat, agent turns)
- **Code splitting**: Lazy load route components
- **Bundle analysis**: Optimize dependency sizes
- **WebSocket batching**: Batch rapid updates to prevent UI thrashing

### Monitoring
- Connection status indicator in run header
- WebSocket reconnection attempts with user feedback
- Error boundaries for graceful failure handling
- Performance metrics in development mode

## Security

### Content Security
- **DOMPurify**: Sanitize all markdown and HTML content
- **Link safety**: External links open with `noopener noreferrer`
- **Input validation**: Sanitize all user inputs before sending to backend

### Network Security
- **CORS**: Backend should configure appropriate CORS headers
- **Authentication**: Ready for token-based auth (extend API client)
- **Environment separation**: Different configs for dev/staging/prod

## Browser Support

- **Chrome/Edge**: 90+
- **Firefox**: 88+ 
- **Safari**: 14+
- **Mobile**: iOS Safari 14+, Chrome Mobile 90+

## Troubleshooting

### Common Issues

**WebSocket connection fails**:
- Check backend is running on expected port
- Verify `VITE_WS_BASE` environment variable
- Check browser console for connection errors

**UI doesn't update with run progress**:
- Verify WebSocket connection (check run header status)
- Check browser network tab for WebSocket messages
- Ensure backend is emitting expected event types

**Artifacts don't load**:
- Check backend `/runs/:run_id/artifacts` endpoint
- Verify CORS headers allow frontend domain
- Check browser console for API errors

**Performance issues with long runs**:
- Use search/filters to reduce displayed content
- Virtualized lists should handle thousands of items
- Consider increasing WebSocket reconnect intervals

### Development Debugging

Enable debug logging:
```env
VITE_LOG_LEVEL=debug
```

Inspect Zustand state:
```javascript
// Browser console
window.__ZUSTAND_DEVTOOLS__
```

Mock WebSocket events for testing:
```typescript
// Add to browser console
const mockEvent = {
  type: 'agent_turn',
  data: { /* mock data */ },
  timestamp: new Date().toISOString()
}
window.dispatchEvent(new CustomEvent('mock-ws', { detail: mockEvent }))
```

## Contributing

1. **Code Style**: Use Prettier and ESLint configurations
2. **Testing**: Add tests for new features in `tests/` directory
3. **TypeScript**: Maintain strict type checking
4. **Accessibility**: Follow WCAG guidelines
5. **Performance**: Consider virtualization for large datasets

## License

MIT License - see LICENSE file for details.
