import requests
import spacy
import psycopg2
from datetime import datetime
import os
from dotenv import load_dotenv
import json
import re
from urllib.parse import urlparse
import time
from bs4 import BeautifulSoup

# Try to import newspaper3k, but make it optional
try:
    from newspaper import Article
    NEWSPAPER_AVAILABLE = True
except ImportError:
    NEWSPAPER_AVAILABLE = False
    print("⚠️  newspaper3k not available, will use BeautifulSoup fallback only")

# Load environment variables
load_dotenv()

API_KEY = os.getenv("NEWSAPI_KEY", "f5fb544ed1984e49b9ae342dccc4006c")

# Database connection string from environment
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Initialize spaCy model (load once, reuse)
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    print("⚠️  spaCy model 'en_core_web_sm' not found. Installing...")
    print("   Run: python -m spacy download en_core_web_sm")
    raise

# List of diseases 
groups = [
    # Respiratory & Viral
    ["covid", "influenza", "flu", "coronavirus", "pandemic", "measles", "mumps", "rsv", "respiratory syncytial virus"],
    ["common cold", "rhinovirus", "adenovirus", "parainfluenza", "hantavirus", "sars", "mers"],
    
    # Vector-borne & Tropical
    ["ebola", "zika", "dengue", "malaria", "yellow fever", "chikungunya", "west nile", "lyme disease"],
    ["rocky mountain spotted fever", "typhus", "scrub typhus", "leishmaniasis", "trypanosomiasis", "sleeping sickness"],
    ["filariasis", "onchocerciasis", "river blindness", "schistosomiasis", "bilharzia"],
    
    # Bacterial Infections
    ["tuberculosis", "cholera", "typhoid", "diphtheria", "pertussis", "whooping cough", "tetanus"],
    ["meningitis", "meningococcal", "pneumonia", "strep throat", "staphylococcus", "staph infection"],
    ["salmonella", "e. coli", "campylobacter", "shigella", "botulism", "anthrax", "plague"],
    ["leprosy", "syphilis", "gonorrhea", "chlamydia", "legionnaires", "legionella"],
    
    # Viral Infections
    ["hepatitis a", "hepatitis b", "hepatitis c", "hepatitis e", "norovirus", "rotavirus", "polio", "poliomyelitis"],
    ["monkeypox", "smallpox", "chickenpox", "varicella", "shingles", "herpes", "hpv", "human papillomavirus"],
    ["rabies", "hiv", "aids", "cytomegalovirus", "cmv", "epstein barr", "mononucleosis"],
    
    # Zoonotic & Emerging
    ["avian flu", "bird flu", "swine flu", "h1n1", "h5n1", "h3n2", "q fever", "brucellosis"],
    ["lassa fever", "marburg", "rift valley fever", "crimean congo hemorrhagic fever", "nipah"],
    ["hendra", "marburg virus", "lassa virus", "nipah virus"],
    
    # Parasitic & Other
    ["giardia", "cryptosporidium", "toxoplasmosis", "trichinosis", "tapeworm", "roundworm", "hookworm"],
    ["scabies", "lice", "bed bugs", "tick borne", "tick fever"],
    
    # Chronic & Other Conditions
    ["cancer", "diabetes", "hypertension", "heart disease", "stroke", "asthma", "copd"],
    ["alzheimer", "dementia", "parkinson", "multiple sclerosis", "ms", "lupus", "rheumatoid arthritis"]
]

# Flatten all diseases into a single list for counting
all_diseases = []
for group in groups:
    all_diseases.extend(group)

# Keywords for NLP analysis
health_keywords = [
    "outbreak", "cases", "hospital", "disease", "ICU", "virus", "infection",
    "symptoms", "diagnosed", "CDC", "WHO", "sick", "illness", "health alert", 
    "quarantine", "epidemic", "transmission", "hospitalization", "pandemic"
]

context_terms = "outbreak OR infection OR epidemic OR cases OR symptoms OR hospitalization OR transmission OR health alert OR disease OR CDC OR WHO OR quarantine"
exclude_terms = "NOT vaccine NOT politics NOT sports NOT costume NOT photography NOT Halloween NOT movie"

def get_db_connection():
    """Establish connection to Neon PostgreSQL database"""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set")
    return psycopg2.connect(DATABASE_URL)

def create_table_if_not_exists(conn):
    """Create articles table if it doesn't exist"""
    cursor = conn.cursor()
    
    # Create table if it doesn't exist
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            link TEXT UNIQUE NOT NULL,
            source TEXT,
            published_at TIMESTAMP,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            keywords JSONB,
            confidence_score FLOAT,
            disease_mention_count INTEGER DEFAULT 0,
            disease_breakdown JSONB,
            country TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_link ON articles(link);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_published_at ON articles(published_at);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_confidence_score ON articles(confidence_score);")
    
    # Add new columns to existing table if they don't exist (for migration)
    # Check and add disease_mention_count column
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='articles' AND column_name='disease_mention_count';
    """)
    if not cursor.fetchone():
        try:
            cursor.execute("ALTER TABLE articles ADD COLUMN disease_mention_count INTEGER DEFAULT 0;")
            print("✓ Added disease_mention_count column to existing table")
        except Exception as e:
            print(f"Warning: Could not add disease_mention_count column: {e}")
    
    # Check and add disease_breakdown column
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='articles' AND column_name='disease_breakdown';
    """)
    if not cursor.fetchone():
        try:
            cursor.execute("ALTER TABLE articles ADD COLUMN disease_breakdown JSONB;")
            print("✓ Added disease_breakdown column to existing table")
        except Exception as e:
            print(f"Warning: Could not add disease_breakdown column: {e}")
    
    # Check and add country column
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='articles' AND column_name='country';
    """)
    if not cursor.fetchone():
        try:
            cursor.execute("ALTER TABLE articles ADD COLUMN country TEXT;")
            print("✓ Added country column to existing table")
        except Exception as e:
            print(f"Warning: Could not add country column: {e}")
    
    # Create country index after column is added
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_country ON articles(country);")
    
    # Create GIN index on disease_breakdown for efficient JSON queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_disease_breakdown ON articles USING GIN (disease_breakdown);")
    
    conn.commit()
    cursor.close()

def fetch_article_content(url, timeout=10):
    """
    Fetch full article content from URL using newspaper3k or BeautifulSoup fallback
    Returns article text or None if fetching fails
    """
    if not url:
        return None
    
    # Try newspaper3k first if available
    if NEWSPAPER_AVAILABLE:
        try:
            article = Article(url)
            article.download()
            article.parse()
            if article.text:
                return article.text
        except Exception:
            # Fall through to BeautifulSoup method
            pass
    
    # Fallback to BeautifulSoup method
    try:
        response = requests.get(url, timeout=timeout, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Remove script, style, and other non-content elements
            for element in soup(["script", "style", "nav", "header", "footer", "aside", "advertisement", "ads"]):
                element.decompose()
            
            # Try to find main article content
            # Look for common article containers
            article_content = None
            for selector in ['article', '.article', '#article', '.content', '.post-content', '.entry-content', 'main', '.main-content']:
                article_content = soup.select_one(selector)
                if article_content:
                    break
            
            # If no specific article container found, use body
            if not article_content:
                article_content = soup.find('body') or soup
            
            # Get text
            text = article_content.get_text(separator=' ', strip=True)
            
            # Clean up excessive whitespace
            text = ' '.join(text.split())
            
            # Return text (limit to reasonable size)
            return text[:10000]  # Limit to first 10000 chars
    except Exception as e:
        pass
    
    return None

def count_disease_mentions(text):
    """
    Count how many times diseases are mentioned in the article text
    Returns a tuple: (total_count, breakdown_dict)
    breakdown_dict contains {disease_name: count} for each disease found
    """
    if not text:
        return 0, {}
    
    text_lower = text.lower()
    total_count = 0
    breakdown = {}
    
    # Count occurrences of each disease in the text
    for disease in all_diseases:
        disease_lower = disease.lower()
        # For multi-word diseases (like "avian flu"), use a more flexible pattern
        if ' ' in disease_lower:
            # For phrases, use word boundaries at start and end
            pattern = r'\b' + re.escape(disease_lower) + r'\b'
        else:
            # For single words, use word boundaries
            pattern = r'\b' + re.escape(disease_lower) + r'\b'
        
        matches = re.findall(pattern, text_lower)
        match_count = len(matches)
        
        if match_count > 0:
            # Store the count for this disease (use original case from all_diseases)
            breakdown[disease] = match_count
            total_count += match_count
    
    return total_count, breakdown

def extract_country_from_article(text, article_data=None, url=None):
    """
    Extract country/geolocation from article text using NLP
    Also checks article_data, URL domain, and source for country information
    Returns country name or None
    """
    # First, check if NewsAPI provides country information in the article data
    if article_data:
        country = article_data.get('country') or article_data.get('countryCode')
        if country:
            return country
    
    # Check URL domain for country hints (e.g., .co.uk, .com.au, .ca, etc.)
    if url:
        try:
            domain = urlparse(url).netloc.lower()
            # Common country domain patterns
            country_domains = {
                # English-speaking countries
                '.co.uk': 'United Kingdom',
                '.com.au': 'Australia',
                '.ca': 'Canada',
                '.co.za': 'South Africa',
                '.co.nz': 'New Zealand',
                '.ie': 'Ireland',
                '.us': 'United States',
                '.com.sg': 'Singapore',
                
                # Asia
                '.in': 'India',
                '.jp': 'Japan',
                '.cn': 'China',
                '.kr': 'South Korea',
                '.tw': 'Taiwan',
                '.hk': 'Hong Kong',
                '.ph': 'Philippines',
                '.th': 'Thailand',
                '.my': 'Malaysia',
                '.id': 'Indonesia',
                '.vn': 'Vietnam',
                '.pk': 'Pakistan',
                '.bd': 'Bangladesh',
                '.lk': 'Sri Lanka',
                '.np': 'Nepal',
                
                # Europe
                '.de': 'Germany',
                '.fr': 'France',
                '.it': 'Italy',
                '.es': 'Spain',
                '.nl': 'Netherlands',
                '.be': 'Belgium',
                '.ch': 'Switzerland',
                '.at': 'Austria',
                '.se': 'Sweden',
                '.no': 'Norway',
                '.dk': 'Denmark',
                '.fi': 'Finland',
                '.pl': 'Poland',
                '.pt': 'Portugal',
                '.gr': 'Greece',
                '.cz': 'Czech Republic',
                '.hu': 'Hungary',
                '.ro': 'Romania',
                '.ru': 'Russia',
                '.ua': 'Ukraine',
                '.tr': 'Turkey',
                
                # Americas
                '.br': 'Brazil',
                '.mx': 'Mexico',
                '.ar': 'Argentina',
                '.cl': 'Chile',
                '.co': 'Colombia',
                '.pe': 'Peru',
                '.ve': 'Venezuela',
                '.ec': 'Ecuador',
                '.uy': 'Uruguay',
                '.py': 'Paraguay',
                '.cr': 'Costa Rica',
                '.pa': 'Panama',
                
                # Middle East & Africa
                '.ae': 'United Arab Emirates',
                '.sa': 'Saudi Arabia',
                '.il': 'Israel',
                '.eg': 'Egypt',
                '.za': 'South Africa',
                '.ke': 'Kenya',
                '.ng': 'Nigeria',
                '.gh': 'Ghana',
                '.tz': 'Tanzania',
                '.et': 'Ethiopia',
            }
            for domain_suffix, country_name in country_domains.items():
                if domain_suffix in domain:
                    return country_name
        except:
            pass
    
    # Check source name for country hints
    if article_data:
        source_name = article_data.get('source', {}).get('name', '').lower()
        source_country_hints = {
            # United Kingdom
            'bbc': 'United Kingdom',
            'guardian': 'United Kingdom',
            'reuters': 'United Kingdom',  # Reuters is international but UK-based
            'telegraph': 'United Kingdom',
            'independent': 'United Kingdom',
            'daily mail': 'United Kingdom',
            'sky news': 'United Kingdom',
            'itv': 'United Kingdom',
            'channel 4': 'United Kingdom',
            
            # United States
            'cnn': 'United States',
            'abc news': 'United States',
            'nbc': 'United States',
            'cbs': 'United States',
            'fox news': 'United States',
            'new york times': 'United States',
            'washington post': 'United States',
            'wall street journal': 'United States',
            'usa today': 'United States',
            'ap news': 'United States',
            'associated press': 'United States',
            'npr': 'United States',
            'pbs': 'United States',
            
            # Canada
            'cbc': 'Canada',
            'ctv': 'Canada',
            'global news': 'Canada',
            'national post': 'Canada',
            'globe and mail': 'Canada',
            
            # Australia
            'abc australia': 'Australia',
            'abc news australia': 'Australia',
            'sbs': 'Australia',
            'the australian': 'Australia',
            'sydney morning herald': 'Australia',
            'the age': 'Australia',
            
            # Other countries
            'al jazeera': 'Qatar',
            'xinhua': 'China',
            'china daily': 'China',
            'south china morning post': 'Hong Kong',
            'times of india': 'India',
            'hindustan times': 'India',
            'the hindu': 'India',
            'asahi shimbun': 'Japan',
            'yomiuri': 'Japan',
            'le monde': 'France',
            'le figaro': 'France',
            'frankfurter allgemeine': 'Germany',
            'der spiegel': 'Germany',
            'el pais': 'Spain',
            'corriere della sera': 'Italy',
            'folha': 'Brazil',
            'globo': 'Brazil',
        }
        for hint, country in source_country_hints.items():
            if hint in source_name:
                return country
    
    if not text:
        return None
    
    # Use spaCy to extract geographic entities from text
    doc = nlp(text)
    countries = []
    country_priority = {}  # Track frequency and position
    
    # Common country names to look for
    common_countries = [
        # North America
        'United States', 'USA', 'US', 'America',
        'Canada', 'Mexico',
        
        # Central America & Caribbean
        'Cuba', 'Jamaica', 'Haiti', 'Dominican Republic', 'Puerto Rico',
        'Guatemala', 'Honduras', 'El Salvador', 'Nicaragua', 'Costa Rica', 'Panama',
        
        # South America
        'Brazil', 'Argentina', 'Chile', 'Colombia', 'Peru', 'Venezuela',
        'Ecuador', 'Bolivia', 'Paraguay', 'Uruguay',
        
        # Europe - Western
        'United Kingdom', 'UK', 'Britain', 'England', 'Scotland', 'Wales', 'Ireland',
        'France', 'Germany', 'Italy', 'Spain', 'Portugal', 'Netherlands', 'Belgium',
        'Switzerland', 'Austria', 'Luxembourg',
        
        # Europe - Northern
        'Sweden', 'Norway', 'Denmark', 'Finland', 'Iceland',
        
        # Europe - Eastern
        'Poland', 'Czech Republic', 'Hungary', 'Romania', 'Bulgaria', 'Croatia',
        'Serbia', 'Slovakia', 'Slovenia', 'Greece', 'Turkey',
        
        # Europe - Former USSR
        'Russia', 'Ukraine', 'Belarus', 'Kazakhstan', 'Georgia', 'Armenia', 'Azerbaijan',
        
        # Middle East
        'Saudi Arabia', 'United Arab Emirates', 'UAE', 'Israel', 'Palestine',
        'Jordan', 'Lebanon', 'Syria', 'Iraq', 'Iran', 'Kuwait', 'Qatar', 'Bahrain',
        'Oman', 'Yemen', 'Egypt',
        
        # Asia - East
        'China', 'Japan', 'South Korea', 'North Korea', 'Taiwan', 'Hong Kong', 'Macau',
        'Mongolia',
        
        # Asia - South
        'India', 'Pakistan', 'Bangladesh', 'Sri Lanka', 'Nepal', 'Bhutan', 'Afghanistan',
        
        # Asia - Southeast
        'Indonesia', 'Malaysia', 'Singapore', 'Thailand', 'Vietnam', 'Philippines',
        'Myanmar', 'Cambodia', 'Laos', 'Brunei',
        
        # Asia - Central
        'Kazakhstan', 'Uzbekistan', 'Turkmenistan', 'Kyrgyzstan', 'Tajikistan',
        
        # Oceania
        'Australia', 'New Zealand', 'Papua New Guinea', 'Fiji', 'Samoa', 'Tonga',
        
        # Africa - North
        'Egypt', 'Libya', 'Tunisia', 'Algeria', 'Morocco', 'Sudan',
        
        # Africa - West
        'Nigeria', 'Ghana', 'Senegal', 'Ivory Coast', 'Mali', 'Burkina Faso',
        'Niger', 'Guinea', 'Sierra Leone', 'Liberia',
        
        # Africa - East
        'Kenya', 'Ethiopia', 'Tanzania', 'Uganda', 'Rwanda', 'Somalia',
        'Djibouti', 'Eritrea', 'South Sudan',
        
        # Africa - Central
        'Democratic Republic of Congo', 'DRC', 'Congo', 'Cameroon', 'Chad',
        'Central African Republic', 'Gabon', 'Equatorial Guinea',
        
        # Africa - South
        'South Africa', 'Zimbabwe', 'Botswana', 'Namibia', 'Angola', 'Mozambique',
        'Zambia', 'Malawi', 'Madagascar', 'Mauritius',
    ]
    
    # Extract GPE (Geopolitical Entity) entities which include countries
    for i, ent in enumerate(doc.ents):
        if ent.label_ == "GPE":
            ent_text = ent.text.strip()
            ent_lower = ent_text.lower()
            
            # Map common abbreviations and variations
            country_map = {
                # United States variations
                'us': 'United States',
                'usa': 'United States',
                'u.s.': 'United States',
                'u.s.a.': 'United States',
                'united states': 'United States',
                'america': 'United States',
                'united states of america': 'United States',
                
                # United Kingdom variations
                'uk': 'United Kingdom',
                'u.k.': 'United Kingdom',
                'united kingdom': 'United Kingdom',
                'britain': 'United Kingdom',
                'england': 'United Kingdom',
                'great britain': 'United Kingdom',
                'gb': 'United Kingdom',
                
                # Other common abbreviations
                'uae': 'United Arab Emirates',
                'drc': 'Democratic Republic of Congo',
                'congo': 'Democratic Republic of Congo',
                'south korea': 'South Korea',
                'north korea': 'North Korea',
                'russia': 'Russia',
                'russian federation': 'Russia',
            }
            
            # Check if it's a known country or variation
            if ent_lower in country_map:
                country_name = country_map[ent_lower]
            elif ent_text in common_countries or any(cc.lower() in ent_lower for cc in common_countries):
                country_name = ent_text
            else:
                # Check if it matches any common country (case-insensitive)
                for cc in common_countries:
                    if cc.lower() == ent_lower or cc.lower() in ent_lower or ent_lower in cc.lower():
                        country_name = cc
                        break
                else:
                    continue  # Skip if not a recognized country
            
            # Track priority (earlier mentions are more likely to be the main country)
            if country_name not in country_priority:
                country_priority[country_name] = {'count': 0, 'first_pos': i}
            country_priority[country_name]['count'] += 1
    
    # Return the most frequently mentioned country, or the first one if tied
    if country_priority:
        # Sort by count (descending), then by first position (ascending)
        sorted_countries = sorted(
            country_priority.items(),
            key=lambda x: (-x[1]['count'], x[1]['first_pos'])
        )
        return sorted_countries[0][0]
    
    return None

def analyze_article_with_nlp(text):
    """
    Analyze article text using spaCy NLP
    Returns keywords found and confidence score
    """
    if not text:
        return [], 0.0
    
    doc = nlp(text.lower())
    
    # Extract keywords and entities
    found_keywords = []
    keyword_counts = {}
    
    # Check for health-related keywords
    for keyword in health_keywords:
        if keyword.lower() in text.lower():
            found_keywords.append(keyword)
            keyword_counts[keyword] = keyword_counts.get(keyword, 0) + 1
    
    # Extract named entities (diseases, organizations, etc.)
    entities = []
    for ent in doc.ents:
        if ent.label_ in ["PERSON", "ORG", "GPE"] or "disease" in ent.text.lower() or "virus" in ent.text.lower():
            entities.append(ent.text)
    
    # Calculate confidence score based on keyword frequency and relevance
    # More keywords found = higher confidence
    keyword_score = min(len(found_keywords) / len(health_keywords) * 100, 100)
    entity_score = min(len(entities) * 5, 30)  # Max 30 points for entities
    text_length_score = min(len(text) / 1000 * 10, 10)  # Max 10 points for text length
    
    confidence = keyword_score + entity_score + text_length_score
    confidence = min(confidence, 100.0)  # Cap at 100
    
    # Combine keywords and unique entities
    all_keywords = list(set(found_keywords + entities))
    
    return all_keywords, round(confidence, 2)

def save_articles_to_db(articles, conn):
    """Save articles to PostgreSQL database with deduplication"""
    if not articles:
        print("No articles to save")
        return
    
    cursor = conn.cursor()
    saved_count = 0
    skipped_count = 0
    
    for article in articles:
        try:
            # Check if article already exists (by link)
            cursor.execute("SELECT id FROM articles WHERE link = %s", (article['link'],))
            if cursor.fetchone():
                skipped_count += 1
                continue
            
            # Insert new article
            cursor.execute("""
                INSERT INTO articles (title, description, link, source, published_at, keywords, confidence_score, disease_mention_count, disease_breakdown, country)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                article['title'],
                article['description'],
                article['link'],
                article['source'],
                article['published_at'],
                json.dumps(article['keywords']),
                article['confidence_score'],
                article.get('disease_mention_count', 0),
                json.dumps(article.get('disease_breakdown', {})),
                article.get('country')
            ))
            saved_count += 1
        except Exception as e:
            print(f"Error saving article {article.get('link', 'unknown')}: {e}")
            conn.rollback()
            continue
    
    conn.commit()
    cursor.close()
    print(f"✓ Saved {saved_count} new articles. Skipped {skipped_count} duplicates.")

def fetch_and_save_news():
    """Fetch news articles, analyze with NLP, and save to database"""
    if not DATABASE_URL:
        print("❌ ERROR: DATABASE_URL environment variable is not set")
        print("   Please set it in your .env file or environment variables")
        return
    
    # Connect to database
    try:
        conn = get_db_connection()
        create_table_if_not_exists(conn)
        print(f"[{datetime.now()}] Connected to database successfully")
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        return
    
    all_results = []
    for disease_group in groups:
        diseases_or = " OR ".join(disease_group)
        query = f"({diseases_or}) AND ({context_terms}) {exclude_terms}"
        url = (
            f'https://newsapi.org/v2/everything'
            f'?q={query}'
            f'&language=en'
            f'&sortBy=publishedAt'
            f'&pageSize=100'
            f'&apiKey={API_KEY}'
        )
        print(f"[{datetime.now()}] Fetching with query: {query[:80]}...")
        try:
            response = requests.get(url)
            data = response.json()
            if 'articles' in data:
                all_results.extend(data['articles'])
            else:
                print("Error or no articles returned:", data)
        except Exception as e:
            print(f"Error fetching news: {e}")
            continue

    # Post-filter for health relevance
    relevant_terms = [
        "outbreak", "cases", "hospital", "disease", "ICU", "virus", "infection",
        "symptoms", "diagnosed", "CDC", "WHO", "sick", "illness", "health alert", "quarantine", "epidemic"
    ]
    filtered = [
        a for a in all_results
        if any(
            term in ((a.get('title') or '') + (a.get('description') or '')).lower()
            for term in relevant_terms
        )
    ]

    print(f"Total filtered relevant articles: {len(filtered)}")
    
    # Process articles with NLP
    processed_articles = []
    total_articles = len(filtered)
    
    for idx, a in enumerate(filtered, 1):
        print(f"Processing article {idx}/{total_articles}: {a.get('title', '')[:60]}...")
        
        # Start with title and description
        article_text = f"{a.get('title', '')} {a.get('description', '')}"
        
        # Try to fetch full article content
        article_url = a.get('url', '')
        full_content = None
        if article_url:
            try:
                print(f"  Fetching full article content from {article_url[:60]}...")
                full_content = fetch_article_content(article_url)
                if full_content:
                    # Combine with title/description for better analysis
                    article_text = f"{article_text} {full_content}"
                    print(f"  ✓ Fetched {len(full_content)} characters of content")
                else:
                    print(f"  ⚠ Could not fetch full content, using title/description only")
            except Exception as e:
                print(f"  ⚠ Error fetching article content: {e}")
            # Small delay to avoid rate limiting
            time.sleep(0.5)
        
        # Analyze with spaCy
        keywords, confidence = analyze_article_with_nlp(article_text)
        
        # Count disease mentions (now with full article content if available)
        disease_count, disease_breakdown = count_disease_mentions(article_text)
        print(f"  Disease mentions found: {disease_count} total")
        if disease_breakdown:
            print(f"  Disease breakdown: {', '.join([f'{k}: {v}' for k, v in disease_breakdown.items()])}")
        
        # Extract country/geolocation (now with full article content if available)
        country = extract_country_from_article(article_text, a, article_url)
        print(f"  Country extracted: {country if country else 'None'}")
        
        # Parse published date
        published_at = None
        try:
            published_at = datetime.fromisoformat(a.get('publishedAt', '').replace('Z', '+00:00'))
        except:
            pass
        
        processed_articles.append({
            'title': a.get('title', ''),
            'description': a.get('description', ''),
            'link': article_url,
            'source': a.get('source', {}).get('name', ''),
            'published_at': published_at,
            'keywords': keywords,
            'confidence_score': confidence,
            'disease_mention_count': disease_count,
            'disease_breakdown': disease_breakdown,
            'country': country
        })
    
    print(f"\n✓ Finished processing {len(processed_articles)} articles")
    
    # Save to database
    save_articles_to_db(processed_articles, conn)
    conn.close()
    print(f"✓ Processed {len(processed_articles)} articles with NLP analysis")

if __name__ == "__main__":
    fetch_and_save_news()
