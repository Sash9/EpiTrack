import { config } from 'dotenv';
import pg from 'pg';

config();

const { Client } = pg;

async function testSearch() {
  const client = new Client({
    connectionString: process.env.DATABASE_URL,
    ssl: process.env.DATABASE_URL?.includes('neon.tech') 
      ? { rejectUnauthorized: false } 
      : false
  });

  try {
    await client.connect();
    console.log('🔌 Connected to database\n');

    // Test 1: Check sample disease_breakdown values
    const sample = await client.query(`
      SELECT id, title, disease_breakdown 
      FROM articles 
      WHERE disease_breakdown IS NOT NULL 
        AND jsonb_typeof(disease_breakdown) = 'object'
      LIMIT 5;
    `);
    
    console.log('📊 Sample disease_breakdown values:');
    sample.rows.forEach(r => {
      const diseases = Object.keys(r.disease_breakdown || {});
      console.log(`  ID ${r.id}: ${diseases.join(', ')}`);
    });

    // Test 2: Search for "rsv" (lowercase)
    const searchPattern1 = '%rsv%';
    const test1 = await client.query(`
      SELECT COUNT(*)::BIGINT AS count
      FROM articles
      WHERE EXISTS (
        SELECT 1 
        FROM jsonb_object_keys(disease_breakdown) AS disease
        WHERE disease ILIKE $1
      );
    `, [searchPattern1]);
    console.log(`\n🔍 Search for "rsv" (lowercase): ${test1.rows[0].count} articles`);

    // Test 3: Search for "Rsv" (mixed case)
    const searchPattern2 = '%Rsv%';
    const test2 = await client.query(`
      SELECT COUNT(*)::BIGINT AS count
      FROM articles
      WHERE EXISTS (
        SELECT 1 
        FROM jsonb_object_keys(disease_breakdown) AS disease
        WHERE disease ILIKE $1
      );
    `, [searchPattern2]);
    console.log(`🔍 Search for "Rsv" (mixed case): ${test2.rows[0].count} articles`);

    // Test 4: Full query like the API uses
    const searchPattern3 = '%Rsv%';
    const test3 = await client.query(`
      SELECT id, title, disease_breakdown
      FROM articles
      WHERE 
        (title IS NOT NULL AND title ILIKE $1)
        OR (description IS NOT NULL AND description ILIKE $1)
        OR (country IS NOT NULL AND country ILIKE $1)
        OR (
          disease_breakdown IS NOT NULL 
          AND jsonb_typeof(disease_breakdown) = 'object'
          AND EXISTS (
            SELECT 1 
            FROM jsonb_object_keys(disease_breakdown) AS disease
            WHERE disease ILIKE $1
          )
        )
      LIMIT 5;
    `, [searchPattern3]);
    console.log(`\n📰 Full query test found ${test3.rows.length} articles:`);
    test3.rows.forEach(r => {
      const diseases = Object.keys(r.disease_breakdown || {});
      console.log(`  - ID ${r.id}: ${r.title.substring(0, 60)}...`);
      console.log(`    Diseases: ${diseases.join(', ')}`);
    });

    await client.end();
  } catch (error) {
    console.error('❌ Error:', error.message);
    process.exitCode = 1;
  }
}

testSearch();

