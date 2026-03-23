#!/usr/bin/env python3

from __future__ import annotations
"""
Local Business Website Scraper & Analyzer
Identifies prospects for $500 website redesign service

Usage:
    python local_biz_website_scraper.py --urls-file urls.csv
    python local_biz_website_scraper.py --category "dentist" --city "Austin TX"
    python local_biz_website_scraper.py --demo
"""

import csv
import re
import sys
import time
import argparse
from datetime import datetime
from urllib.parse import urlparse, urljoin
import ssl
import socket

try:
    import requests
    from bs4 import BeautifulSoup
    from tqdm import tqdm
except ImportError:
    print("Missing dependencies. Install with:")
    print("pip install requests beautifulsoup4 tqdm")
    sys.exit(1)


class BusinessWebsiteScraper:
    def __init__(self, rate_limit=2.0):
        self.rate_limit = rate_limit
        self.last_request_time = 0
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        ]
        self.ua_index = 0

    def _wait_for_rate_limit(self):
        """Enforce rate limiting between requests"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self.last_request_time = time.time()

    def _get_user_agent(self):
        """Rotate user agents"""
        ua = self.user_agents[self.ua_index]
        self.ua_index = (self.ua_index + 1) % len(self.user_agents)
        return ua

    def check_ssl(self, url):
        """Check if site has valid SSL certificate"""
        try:
            hostname = urlparse(url).hostname
            if not hostname:
                return False

            context = ssl.create_default_context()
            with socket.create_connection((hostname, 443), timeout=5) as sock:
                with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                    return True
        except:
            return False

    def extract_emails(self, text):
        """Extract email addresses from text"""
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        emails = re.findall(email_pattern, text)
        # Filter out common false positives
        emails = [e for e in emails if not e.endswith(('.png', '.jpg', '.gif'))]
        return list(set(emails))[:3]  # Return up to 3 unique emails

    def extract_phones(self, text):
        """Extract phone numbers from text"""
        phone_patterns = [
            r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',  # (555) 555-5555
            r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}',        # 555-555-5555
            r'\+?1?\s?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'  # +1 (555) 555-5555
        ]
        phones = []
        for pattern in phone_patterns:
            phones.extend(re.findall(pattern, text))
        return list(set(phones))[:2]  # Return up to 2 unique phones

    def detect_tech_stack(self, soup, headers, url):
        """Detect website technology"""
        tech = []

        # Check meta generator
        generator = soup.find('meta', attrs={'name': 'generator'})
        if generator and generator.get('content'):
            tech.append(generator['content'].split()[0])

        # Check for WordPress
        if 'wp-content' in str(soup) or 'wordpress' in str(headers).lower():
            tech.append('WordPress')

        # Check for Wix
        if 'wix.com' in str(soup) or 'X-Wix-' in str(headers):
            tech.append('Wix')

        # Check for Squarespace
        if 'squarespace' in str(soup).lower():
            tech.append('Squarespace')

        # Check for Shopify
        if 'shopify' in str(soup).lower() or 'cdn.shopify.com' in str(soup):
            tech.append('Shopify')

        # Check for Webflow
        if 'webflow' in str(soup).lower():
            tech.append('Webflow')

        # Check server header
        server = headers.get('Server', '')
        if server and not tech:
            tech.append(f"Server: {server}")

        return ', '.join(tech) if tech else 'Unknown/Custom'

    def check_mobile_responsive(self, soup):
        """Check if site has mobile viewport meta tag"""
        viewport = soup.find('meta', attrs={'name': 'viewport'})
        if viewport and 'width=device-width' in str(viewport):
            return True
        return False

    def check_seo_basics(self, soup):
        """Check basic SEO elements and return score 0-100"""
        score = 0
        checks = []

        # Title tag (20 points)
        title = soup.find('title')
        if title and len(title.get_text().strip()) > 10:
            score += 20
            checks.append('title')

        # Meta description (20 points)
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content') and len(meta_desc['content']) > 50:
            score += 20
            checks.append('meta_desc')

        # H1 tag (20 points)
        h1 = soup.find('h1')
        if h1 and len(h1.get_text().strip()) > 5:
            score += 20
            checks.append('h1')

        # OG tags (20 points)
        og_tags = soup.find_all('meta', property=re.compile(r'^og:'))
        if len(og_tags) >= 3:
            score += 20
            checks.append('og_tags')

        # Alt text on images (20 points)
        images = soup.find_all('img')
        if images:
            images_with_alt = [img for img in images if img.get('alt')]
            if len(images_with_alt) / len(images) > 0.5:
                score += 20
                checks.append('img_alt')

        return score, checks

    def check_ai_seo_readiness(self, soup):
        """Check AI-SEO readiness (schema, structured data) and return score 0-100"""
        score = 0
        checks = []

        # Schema.org markup (40 points)
        schema_scripts = soup.find_all('script', type='application/ld+json')
        if schema_scripts:
            score += 40
            checks.append('schema_org')

        # FAQ schema (20 points)
        page_text = str(soup).lower()
        if 'faqpage' in page_text or 'question' in page_text:
            score += 20
            checks.append('faq_schema')

        # Breadcrumbs (20 points)
        if soup.find('nav', class_=re.compile(r'breadcrumb', re.I)):
            score += 20
            checks.append('breadcrumbs')
        elif soup.find('ol', class_=re.compile(r'breadcrumb', re.I)):
            score += 20
            checks.append('breadcrumbs')

        # Structured headings (20 points)
        h_tags = soup.find_all(['h1', 'h2', 'h3'])
        if len(h_tags) >= 5:
            score += 20
            checks.append('structured_headings')

        return score, checks

    def estimate_last_updated(self, soup, headers):
        """Estimate when site was last updated"""
        # Check Last-Modified header
        last_modified = headers.get('Last-Modified')
        if last_modified:
            return f"Header: {last_modified}"

        # Check copyright year
        copyright_match = re.search(r'©?\s*(?:Copyright\s+)?(\d{4})', str(soup), re.I)
        if copyright_match:
            year = copyright_match.group(1)
            return f"Copyright: {year}"

        # Check for date patterns in content
        date_patterns = [
            r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}',
            r'\d{1,2}/\d{1,2}/\d{4}',
            r'\d{4}-\d{2}-\d{2}'
        ]

        text = soup.get_text()
        for pattern in date_patterns:
            matches = re.findall(pattern, text)
            if matches:
                return f"Content date: {matches[0]}"

        return "Unknown"

    def check_appears_active(self, soup, text):
        """Check if business appears active"""
        score = 0

        # Has phone number visible
        if self.extract_phones(text):
            score += 1

        # Has email visible
        if self.extract_emails(text):
            score += 1

        # Has social links
        social_patterns = ['facebook.com', 'instagram.com', 'twitter.com', 'linkedin.com', 'youtube.com']
        for pattern in social_patterns:
            if pattern in text.lower():
                score += 1
                break

        # Has recent copyright year
        current_year = datetime.now().year
        if str(current_year) in text or str(current_year - 1) in text:
            score += 1

        return score >= 2  # Need at least 2 signals

    def estimate_budget(self, category, site_score, tech_stack):
        """Estimate budget potential based on business type and site quality"""
        # Base budget by category
        category_budgets = {
            'dentist': 1500,
            'dental': 1500,
            'doctor': 1500,
            'medical': 1500,
            'lawyer': 2000,
            'law firm': 2000,
            'legal': 2000,
            'attorney': 2000,
            'real estate': 1200,
            'realtor': 1200,
            'restaurant': 800,
            'cafe': 800,
            'plumber': 700,
            'hvac': 700,
            'electrician': 700,
            'contractor': 900,
            'salon': 600,
            'spa': 800,
            'gym': 1000,
            'fitness': 1000,
            'default': 500
        }

        category_lower = category.lower()
        base = category_budgets.get(category_lower, category_budgets['default'])

        # If site is really bad (score < 30), they might pay more
        if site_score < 30:
            base = int(base * 1.3)

        # If using DIY platform (Wix, Squarespace), might have smaller budget
        if 'Wix' in tech_stack or 'Squarespace' in tech_stack:
            base = int(base * 0.7)

        return base

    def calculate_site_score(self, results):
        """Calculate overall site score 0-100 (lower = needs more help)"""
        score = 100

        # Penalize for missing features
        if not results['mobile_ready']:
            score -= 25

        if not results['has_ssl']:
            score -= 20

        # SEO score (already 0-100, invert so low = bad)
        score -= (100 - results['seo_score']) * 0.3

        # AI SEO score (already 0-100, invert)
        score -= (100 - results['ai_seo_score']) * 0.25

        # Old sites need help
        if 'Unknown' in results['last_updated']:
            score -= 10
        elif results['last_updated']:
            # Check if year is old
            year_match = re.search(r'(\d{4})', results['last_updated'])
            if year_match:
                year = int(year_match.group(1))
                if year < 2020:
                    score -= 15
                elif year < 2022:
                    score -= 10

        return max(0, int(score))

    def scrape_business(self, url, business_name=None, category=None, city=None):
        """Scrape and analyze a single business website"""
        self._wait_for_rate_limit()

        results = {
            'business_name': business_name or urlparse(url).hostname,
            'url': url,
            'city': city or '',
            'category': category or '',
            'site_score': 0,
            'mobile_ready': False,
            'has_ssl': False,
            'seo_score': 0,
            'ai_seo_score': 0,
            'tech_stack': '',
            'last_updated_estimate': '',
            'appears_active': False,
            'budget_estimate': 500,
            'email_if_found': '',
            'phone_if_found': '',
            'notes': '',
            'outreach_priority': 'LOW'
        }

        try:
            # Ensure URL has scheme
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url

            # Check SSL
            if url.startswith('https://'):
                results['has_ssl'] = self.check_ssl(url)

            # Fetch page
            headers = {'User-Agent': self._get_user_agent()}
            response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')
            text = soup.get_text()

            # Run checks
            results['mobile_ready'] = self.check_mobile_responsive(soup)
            results['tech_stack'] = self.detect_tech_stack(soup, response.headers, url)

            seo_score, seo_checks = self.check_seo_basics(soup)
            results['seo_score'] = seo_score

            ai_seo_score, ai_checks = self.check_ai_seo_readiness(soup)
            results['ai_seo_score'] = ai_seo_score

            results['last_updated_estimate'] = self.estimate_last_updated(soup, response.headers)
            results['appears_active'] = self.check_appears_active(soup, text)

            # Extract contact info
            emails = self.extract_emails(text)
            results['email_if_found'] = ', '.join(emails) if emails else ''

            phones = self.extract_phones(text)
            results['phone_if_found'] = ', '.join(phones) if phones else ''

            # Calculate overall score
            results['site_score'] = self.calculate_site_score(results)

            # Estimate budget
            results['budget_estimate'] = self.estimate_budget(
                category or '',
                results['site_score'],
                results['tech_stack']
            )

            # Determine outreach priority
            if results['site_score'] < 40 and results['appears_active'] and emails:
                results['outreach_priority'] = 'HIGH'
            elif results['site_score'] < 60 and results['appears_active']:
                results['outreach_priority'] = 'MEDIUM'
            else:
                results['outreach_priority'] = 'LOW'

            # Add notes
            notes = []
            if not results['mobile_ready']:
                notes.append('Not mobile responsive')
            if not results['has_ssl']:
                notes.append('No SSL')
            if results['seo_score'] < 50:
                notes.append('Poor SEO')
            if results['ai_seo_score'] < 30:
                notes.append('No AI-SEO')
            results['notes'] = '; '.join(notes)

        except requests.exceptions.RequestException as e:
            results['notes'] = f'Error: {str(e)[:100]}'
        except Exception as e:
            results['notes'] = f'Error: {str(e)[:100]}'

        return results

    def scrape_google_maps(self, category, city, max_results=10):
        """
        Attempt to scrape Google Maps for business URLs
        Note: This is limited without API, mainly for demo purposes
        """
        print(f"Note: Google Maps scraping without API is limited. Consider using Google Places API or manual URL input.")
        print(f"Searching for: {category} in {city}")

        # This is a simplified version - real implementation would need:
        # 1. Google Places API (recommended)
        # 2. Or manual scraping with more sophisticated tools
        # For now, return empty list

        return []


def load_urls_from_csv(filepath):
    """Load URLs from CSV file"""
    businesses = []
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            businesses.append({
                'url': row.get('url', ''),
                'business_name': row.get('business_name', ''),
                'category': row.get('category', ''),
                'city': row.get('city', '')
            })
    return businesses


def save_results_to_csv(results, output_path):
    """Save results to CSV file"""
    if not results:
        print("No results to save.")
        return

    fieldnames = [
        'business_name', 'url', 'city', 'category', 'site_score',
        'mobile_ready', 'has_ssl', 'seo_score', 'ai_seo_score',
        'tech_stack', 'last_updated_estimate', 'appears_active',
        'budget_estimate', 'email_if_found', 'phone_if_found',
        'notes', 'outreach_priority'
    ]

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nResults saved to: {output_path}")


def demo_mode():
    """Run demo with sample business types"""
    print("DEMO MODE: Analyzing sample local business website types\n")

    demo_businesses = [
        {
            'business_name': 'Example Dental Practice',
            'url': 'https://example-dentist.com',
            'category': 'dentist',
            'city': 'Austin TX'
        },
        {
            'business_name': 'Quick Plumbing Services',
            'url': 'https://example-plumber.com',
            'category': 'plumber',
            'city': 'Dallas TX'
        },
        {
            'business_name': 'Downtown Restaurant',
            'url': 'https://example-restaurant.com',
            'category': 'restaurant',
            'city': 'Houston TX'
        },
        {
            'business_name': 'Smith & Associates Law',
            'url': 'https://example-lawfirm.com',
            'category': 'law firm',
            'city': 'San Antonio TX'
        },
        {
            'business_name': 'Premier Realty Group',
            'url': 'https://example-realestate.com',
            'category': 'real estate',
            'city': 'Fort Worth TX'
        }
    ]

    print("Demo Output Format:\n")
    print("-" * 100)

    # Create demo results without actually scraping
    results = []
    for biz in demo_businesses:
        result = {
            'business_name': biz['business_name'],
            'url': biz['url'],
            'city': biz['city'],
            'category': biz['category'],
            'site_score': 35,  # Low score = needs redesign
            'mobile_ready': False,
            'has_ssl': True,
            'seo_score': 40,
            'ai_seo_score': 20,
            'tech_stack': 'WordPress',
            'last_updated_estimate': 'Copyright: 2019',
            'appears_active': True,
            'budget_estimate': 1200,
            'email_if_found': f'info@{biz["business_name"].lower().replace(" ", "")}.com',
            'phone_if_found': '(555) 123-4567',
            'notes': 'Not mobile responsive; Poor SEO; No AI-SEO',
            'outreach_priority': 'HIGH'
        }
        results.append(result)

        # Print sample
        print(f"Business: {result['business_name']}")
        print(f"  URL: {result['url']}")
        print(f"  Score: {result['site_score']}/100 (lower = more likely prospect)")
        print(f"  Priority: {result['outreach_priority']}")
        print(f"  Budget Estimate: ${result['budget_estimate']}")
        print(f"  Issues: {result['notes']}")
        print(f"  Contact: {result['email_if_found']} | {result['phone_if_found']}")
        print("-" * 100)

    print("\nIn real mode, the script would:")
    print("1. Actually visit each URL")
    print("2. Check SSL, mobile responsiveness, SEO")
    print("3. Extract real contact info")
    print("4. Score and prioritize for outreach")
    print("5. Save to CSV at AUTOMATIONS/output/local_biz_prospects.csv")


def main():
    parser = argparse.ArgumentParser(
        description='Scrape and analyze local business websites for redesign prospects'
    )
    parser.add_argument('--urls-file', help='CSV file with URLs (columns: url, business_name, category, city)')
    parser.add_argument('--category', help='Business category (e.g., "dentist", "plumber")')
    parser.add_argument('--city', help='City to search (e.g., "Austin TX")')
    parser.add_argument('--output', default='AUTOMATIONS/output/local_biz_prospects.csv',
                       help='Output CSV file path')
    parser.add_argument('--rate-limit', type=float, default=2.0,
                       help='Seconds between requests (default: 2.0)')
    parser.add_argument('--demo', action='store_true',
                       help='Run demo mode with sample data')

    args = parser.parse_args()

    if args.demo:
        demo_mode()
        return

    # Validate inputs
    if not args.urls_file and not (args.category and args.city):
        parser.error("Either --urls-file or both --category and --city are required")

    scraper = BusinessWebsiteScraper(rate_limit=args.rate_limit)
    businesses = []

    # Load businesses
    if args.urls_file:
        print(f"Loading URLs from: {args.urls_file}")
        businesses = load_urls_from_csv(args.urls_file)
    elif args.category and args.city:
        print(f"Searching for: {args.category} in {args.city}")
        print("\nNote: Direct Google Maps scraping requires API access.")
        print("For best results, manually create a CSV with business URLs.")
        print("You can find URLs via Google Maps, Yelp, Yellow Pages, etc.\n")

        # Attempt to scrape (will return empty without API)
        businesses = scraper.scrape_google_maps(args.category, args.city)

        if not businesses:
            print("No businesses found via automated scraping.")
            print("Please create a CSV file with columns: url, business_name, category, city")
            return

    if not businesses:
        print("No businesses to analyze.")
        return

    print(f"\nAnalyzing {len(businesses)} businesses...")
    print(f"Rate limit: {args.rate_limit} seconds between requests\n")

    # Scrape each business
    results = []
    for biz in tqdm(businesses, desc="Scraping websites"):
        result = scraper.scrape_business(
            url=biz['url'],
            business_name=biz.get('business_name'),
            category=biz.get('category'),
            city=biz.get('city')
        )
        results.append(result)

    # Sort by priority and score
    priority_order = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
    results.sort(key=lambda x: (priority_order[x['outreach_priority']], x['site_score']))

    # Save results
    save_results_to_csv(results, args.output)

    # Print summary
    high_priority = sum(1 for r in results if r['outreach_priority'] == 'HIGH')
    medium_priority = sum(1 for r in results if r['outreach_priority'] == 'MEDIUM')

    print("\n" + "="*80)
    print("ANALYSIS COMPLETE")
    print("="*80)
    print(f"Total analyzed: {len(results)}")
    print(f"High priority prospects: {high_priority}")
    print(f"Medium priority prospects: {medium_priority}")
    print(f"Average estimated budget: ${sum(r['budget_estimate'] for r in results) / len(results):.0f}")
    print(f"\nTop 5 prospects (sorted by priority and score):")
    print("-"*80)

    for i, result in enumerate(results[:5], 1):
        print(f"{i}. {result['business_name']} ({result['category']})")
        print(f"   URL: {result['url']}")
        print(f"   Score: {result['site_score']}/100 | Priority: {result['outreach_priority']} | Budget: ${result['budget_estimate']}")
        print(f"   Contact: {result['email_if_found'] or 'No email'} | {result['phone_if_found'] or 'No phone'}")
        print(f"   Issues: {result['notes']}")
        print()


if __name__ == '__main__':
    main()
