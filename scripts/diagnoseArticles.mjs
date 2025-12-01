import { config } from 'dotenv';
import pg from 'pg';

config();

const { Client } = pg;

async function diagnoseArticles() {
  const client = new Client({
    connectionString: process.env.DATABASE_URL,
    ssl: process.env.DATABASE_URL?.includes('neon.tech') 
      ? { rejectUnauthorized: false } 
      : false
  });

  try {
    await client.connect();
    console.log('🔌 Connected to database\n');

    // Total count
    const total = await client.query('SELECT COUNT(*)::BIGINT AS count FROM articles;');
    console.log(`📊 Total articles in database: ${total.rows[0].count}`);

    // Count by processing status
    const withKeywords = await client.query(
      'SELECT COUNT(*)::BIGINT AS count FROM articles WHERE keywords IS NOT NULL;'
    );
    const withConfidence = await client.query(
      'SELECT COUNT(*)::BIGINT AS count FROM articles WHERE confidence_score IS NOT NULL;'
    );
    const fullyProcessed = await client.query(
      'SELECT COUNT(*)::BIGINT AS count FROM articles WHERE keywords IS NOT NULL AND confidence_score IS NOT NULL;'
    );
    const withoutProcessing = await client.query(
      'SELECT COUNT(*)::BIGINT AS count FROM articles WHERE keywords IS NULL OR confidence_score IS NULL;'
    );

    console.log(`\n📈 Processing Status:`);
    console.log(`   With keywords: ${withKeywords.rows[0].count}`);
    console.log(`   With confidence_score: ${withConfidence.rows[0].count}`);
    console.log(`   Fully processed (both): ${fullyProcessed.rows[0].count}`);
    console.log(`   Without processing: ${withoutProcessing.rows[0].count}`);

    // Check for duplicates by link
    const duplicates = await client.query(`
      SELECT link, COUNT(*) as count 
      FROM articles 
      GROUP BY link 
      HAVING COUNT(*) > 1;
    `);
    console.log(`\n🔍 Duplicate links: ${duplicates.rows.length}`);

    // Check for articles with NULL links (shouldn't happen but worth checking)
    const nullLinks = await client.query(
      'SELECT COUNT(*)::BIGINT AS count FROM articles WHERE link IS NULL;'
    );
    console.log(`   Articles with NULL links: ${nullLinks.rows[0].count}`);

    // Check date ranges
    const dateRange = await client.query(`
      SELECT 
        MIN(fetched_at) as earliest,
        MAX(fetched_at) as latest,
        COUNT(DISTINCT DATE(fetched_at)) as unique_dates
      FROM articles;
    `);
    console.log(`\n📅 Date Range:`);
    console.log(`   Earliest: ${dateRange.rows[0].earliest}`);
    console.log(`   Latest: ${dateRange.rows[0].latest}`);
    console.log(`   Unique dates: ${dateRange.rows[0].unique_dates}`);

    // Check if there are any other tables
    const otherTables = await client.query(`
      SELECT table_name 
      FROM information_schema.tables 
      WHERE table_schema = 'public' 
        AND table_type = 'BASE TABLE'
        AND table_name != 'articles';
    `);
    console.log(`\n🗂️  Other tables in database: ${otherTables.rows.length}`);
    if (otherTables.rows.length > 0) {
      otherTables.rows.forEach(row => {
        console.log(`   - ${row.table_name}`);
      });
    }

    // Check for articles that might have been inserted but failed processing
    const recentArticles = await client.query(`
      SELECT 
        id, 
        title, 
        link, 
        fetched_at,
        CASE 
          WHEN keywords IS NULL THEN 'No keywords'
          WHEN confidence_score IS NULL THEN 'No confidence'
          ELSE 'Processed'
        END as status
      FROM articles
      ORDER BY fetched_at DESC
      LIMIT 20;
    `);
    console.log(`\n📰 Most recent 20 articles:`);
    recentArticles.rows.forEach((row, idx) => {
      console.log(`   ${idx + 1}. [${row.status}] ${row.title.substring(0, 50)}...`);
    });

    await client.end();
    console.log('\n✅ Diagnosis complete');
  } catch (error) {
    console.error('❌ Error:', error.message);
    process.exitCode = 1;
  }
}

diagnoseArticles();

