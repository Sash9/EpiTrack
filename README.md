# EpiTrack

EpiTrack is a health news intelligence dashboard built with React + Vite.

## Getting Started

### Prerequisites

Create a `.env` file in the root directory with your Neon PostgreSQL connection string:

```
DATABASE_URL=postgres://...
```

### Running the Application

The application consists of two parts:

1. **API Server** (required for real-time stats):
   ```bash
   npm run dev:api
   ```
   This starts the Express API server on `http://localhost:3001` that queries the database for stats and recent searches.

2. **Frontend Development Server**:
   ```bash
   npm run dev
   ```
   This starts the Vite dev server (typically on `http://localhost:5173`). The Vite config proxies `/api` requests to the API server.

**Note:** Both servers need to be running for the Data Sources page to display real-time data.

## Features

### Real-Time Stats Updates

The Data Sources page automatically fetches and displays:
- Total articles processed
- Distinct keywords extracted
- Distinct diseases tracked
- Distinct countries covered

Stats are polled every 30 seconds, so new articles added to the database will automatically update the displayed counts.

### Recent Searches

The "Recent Searches" section displays the most recently analyzed diseases from articles in the database, showing when each was last analyzed.

## Manual Stats Update (Optional)

If you need to generate a static stats snapshot (for example, for build-time data), you can run:

```bash
npm run update-stats
```

This queries the database and saves results to `src/data/stats.json`. However, the live application uses the API endpoint instead.
