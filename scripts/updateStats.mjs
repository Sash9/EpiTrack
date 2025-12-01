import { config } from 'dotenv';
import { mkdir, writeFile } from 'fs/promises';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';
import pg from 'pg';

config();

const { Client } = pg;

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const rootDir = resolve(__dirname, '..');
const outputPath = resolve(rootDir, 'src', 'data', 'stats.json');

function requireEnv(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

async function fetchStats(client) {
  const queries = {
    articlesProcessed: 'SELECT COUNT(*)::BIGINT AS count FROM articles;',
    keywordsExtracted: `
      SELECT COUNT(DISTINCT keyword)::BIGINT AS count
      FROM (
        SELECT jsonb_array_elements_text(keywords) AS keyword
        FROM articles
        WHERE keywords IS NOT NULL
      ) AS expanded;
    `,
    diseasesTracked: `
      SELECT COUNT(DISTINCT disease)::BIGINT AS count
      FROM (
        SELECT jsonb_object_keys(disease_breakdown) AS disease
        FROM articles
        WHERE disease_breakdown IS NOT NULL
      ) AS expanded;
    `,
    countriesCovered: `
      SELECT COUNT(DISTINCT country)::BIGINT AS count
      FROM articles
      WHERE country IS NOT NULL
        AND TRIM(country) <> '';
    `
  };

  const stats = {};

  for (const [key, query] of Object.entries(queries)) {
    const result = await client.query(query);
    const value = result.rows?.[0]?.count ?? 0;
    stats[key] = Number(value) || 0;
  }

  stats.lastUpdated = new Date().toISOString();

  return stats;
}

function formatNumber(value) {
  return new Intl.NumberFormat('en-US').format(value);
}

async function writeStatsFile(stats) {
  const payload = {
    ...stats,
    display: {
      articlesProcessed: `${formatNumber(stats.articlesProcessed)}`,
      keywordsExtracted: `${formatNumber(stats.keywordsExtracted)}`,
      diseasesTracked: `${formatNumber(stats.diseasesTracked)}`,
      countriesCovered: `${formatNumber(stats.countriesCovered)}`
    }
  };

  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, JSON.stringify(payload, null, 2));
  console.log(`✅ Wrote stats to ${outputPath}`);
}

async function main() {
  try {
    const client = new Client({
      connectionString: requireEnv('DATABASE_URL'),
      ssl: process.env.DATABASE_SSL === 'false' ? false : { rejectUnauthorized: false }
    });

    console.log('🔌 Connecting to Postgres...');
    await client.connect();

    console.log('📊 Fetching stats from articles table...');
    const stats = await fetchStats(client);
    console.table(stats);

    await writeStatsFile(stats);
    await client.end();

    console.log('✨ Done. Run `npm run update-stats` anytime to refresh the snapshot.');
  } catch (error) {
    console.error('❌ Failed to update stats:', error.message);
    process.exitCode = 1;
  }
}

main();

