#!/usr/bin/env python3

from __future__ import annotations
"""
Local Business Website Redesign Pipeline
Master script that chains together: SCRAPE → GENERATE → HOST → OUTREACH

Usage:
    # From URLs file
    python local_biz_pipeline.py --urls-file sample_local_biz_urls.csv

    # Discover from category + city
    python local_biz_pipeline.py --category "dentist" --city "Austin TX"

    # Dry run (analyze only, no emails)
    python local_biz_pipeline.py --urls-file sample_local_biz_urls.csv --dry-run

    # Custom output location
    python local_biz_pipeline.py --urls-file sample_local_biz_urls.csv --output-dir custom_output

Pipeline Steps:
    1. SCRAPE: Analyze business websites (mobile, SEO, SSL, etc.)
    2. GENERATE: Create landing pages for HIGH priority prospects (score < 40)
    3. HOST: Copy pages to shareable directory
    4. OUTREACH: Generate personalized cold email CSV for Instantly.ai
"""

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import ssl
import socket

try:
    import requests
    from bs4 import BeautifulSoup
    from tqdm import tqdm
except ImportError:
    print("\033[91mMissing dependencies. Install with:\033[0m")
    print("pip install requests beautifulsoup4 tqdm")
    sys.exit(1)

# ANSI color codes for terminal output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


# ============================================================================
# SCRAPER LOGIC (from local_biz_website_scraper.py)
# ============================================================================

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
        emails = [e for e in emails if not e.endswith(('.png', '.jpg', '.gif'))]
        return list(set(emails))[:3]

    def extract_phones(self, text):
        """Extract phone numbers from text"""
        phone_patterns = [
            r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',
            r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}',
            r'\+?1?\s?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
        ]
        phones = []
        for pattern in phone_patterns:
            phones.extend(re.findall(pattern, text))
        return list(set(phones))[:2]

    def detect_tech_stack(self, soup, headers, url):
        """Detect website technology"""
        tech = []
        generator = soup.find('meta', attrs={'name': 'generator'})
        if generator and generator.get('content'):
            tech.append(generator['content'].split()[0])
        if 'wp-content' in str(soup) or 'wordpress' in str(headers).lower():
            tech.append('WordPress')
        if 'wix.com' in str(soup) or 'X-Wix-' in str(headers):
            tech.append('Wix')
        if 'squarespace' in str(soup).lower():
            tech.append('Squarespace')
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
        title = soup.find('title')
        if title and len(title.get_text().strip()) > 10:
            score += 20
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content') and len(meta_desc['content']) > 50:
            score += 20
        h1 = soup.find('h1')
        if h1 and len(h1.get_text().strip()) > 5:
            score += 20
        og_tags = soup.find_all('meta', property=re.compile(r'^og:'))
        if len(og_tags) >= 3:
            score += 20
        images = soup.find_all('img')
        if images:
            images_with_alt = [img for img in images if img.get('alt')]
            if len(images_with_alt) / len(images) > 0.5:
                score += 20
        return score

    def check_ai_seo_readiness(self, soup):
        """Check AI-SEO readiness (schema, structured data) and return score 0-100"""
        score = 0
        schema_scripts = soup.find_all('script', type='application/ld+json')
        if schema_scripts:
            score += 40
        page_text = str(soup).lower()
        if 'faqpage' in page_text or 'question' in page_text:
            score += 20
        h_tags = soup.find_all(['h1', 'h2', 'h3'])
        if len(h_tags) >= 5:
            score += 20
        return score

    def estimate_last_updated(self, soup, headers):
        """Estimate when site was last updated"""
        last_modified = headers.get('Last-Modified')
        if last_modified:
            return f"Header: {last_modified}"
        copyright_match = re.search(r'©?\s*(?:Copyright\s+)?(\d{4})', str(soup), re.I)
        if copyright_match:
            year = copyright_match.group(1)
            return f"Copyright: {year}"
        return "Unknown"

    def check_appears_active(self, soup, text):
        """Check if business appears active"""
        score = 0
        if self.extract_phones(text):
            score += 1
        if self.extract_emails(text):
            score += 1
        social_patterns = ['facebook.com', 'instagram.com', 'twitter.com', 'linkedin.com']
        for pattern in social_patterns:
            if pattern in text.lower():
                score += 1
                break
        current_year = datetime.now().year
        if str(current_year) in text or str(current_year - 1) in text:
            score += 1
        return score >= 2

    def calculate_site_score(self, results):
        """Calculate overall site score 0-100 (lower = needs more help)"""
        score = 100
        if not results['mobile_ready']:
            score -= 25
        if not results['has_ssl']:
            score -= 20
        score -= (100 - results['seo_score']) * 0.3
        score -= (100 - results['ai_seo_score']) * 0.25
        if 'Unknown' in results['last_updated_estimate']:
            score -= 10
        elif results['last_updated_estimate']:
            year_match = re.search(r'(\d{4})', results['last_updated_estimate'])
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
            'email_if_found': '',
            'phone_if_found': '',
            'notes': '',
            'outreach_priority': 'LOW'
        }

        try:
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url

            if url.startswith('https://'):
                results['has_ssl'] = self.check_ssl(url)

            headers = {'User-Agent': self._get_user_agent()}
            response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')
            text = soup.get_text()

            results['mobile_ready'] = self.check_mobile_responsive(soup)
            results['tech_stack'] = self.detect_tech_stack(soup, response.headers, url)
            results['seo_score'] = self.check_seo_basics(soup)
            results['ai_seo_score'] = self.check_ai_seo_readiness(soup)
            results['last_updated_estimate'] = self.estimate_last_updated(soup, response.headers)
            results['appears_active'] = self.check_appears_active(soup, text)

            emails = self.extract_emails(text)
            results['email_if_found'] = ', '.join(emails) if emails else ''

            phones = self.extract_phones(text)
            results['phone_if_found'] = ', '.join(phones) if phones else ''

            results['site_score'] = self.calculate_site_score(results)

            if results['site_score'] < 40 and results['appears_active'] and emails:
                results['outreach_priority'] = 'HIGH'
            elif results['site_score'] < 60 and results['appears_active']:
                results['outreach_priority'] = 'MEDIUM'
            else:
                results['outreach_priority'] = 'LOW'

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


# ============================================================================
# LANDING PAGE GENERATOR LOGIC (simplified from bulk_landing_page_generator.py)
# ============================================================================

CATEGORY_TEMPLATES = {
    "dentist": {"tagline": "Creating Beautiful, Healthy Smiles", "color": "#0EA5E9"},
    "plumber": {"tagline": "Licensed & Insured Plumbing Experts", "color": "#DC2626"},
    "electrician": {"tagline": "Licensed Electrical Contractors", "color": "#F59E0B"},
    "hvac": {"tagline": "Heating & Cooling Specialists", "color": "#10B981"},
    "restaurant": {"tagline": "Where Every Meal is a Memory", "color": "#EF4444"},
    "law_firm": {"tagline": "Experienced Legal Representation", "color": "#1E40AF"},
    "real_estate": {"tagline": "Your Trusted Real Estate Partner", "color": "#7C3AED"},
    "salon": {"tagline": "Where Beauty Meets Artistry", "color": "#EC4899"},
}

def slugify(text):
    """Convert text to URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text

def generate_simple_landing_page(business):
    """Generate simple HTML landing page for a business."""
    name = business.get('business_name', 'Local Business')
    category = business.get('category', 'business')
    city = business.get('city', 'Your City')
    phone = business.get('phone_if_found', '(555) 123-4567')
    email = business.get('email_if_found', 'info@business.com')

    template = CATEGORY_TEMPLATES.get(slugify(category).replace('-', '_'),
                                       {"tagline": f"Professional {category.title()} Services", "color": "#3B82F6"})

    # Super minimal landing page (just enough to demonstrate)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{name} | {template['tagline']} in {city}</title>
    <meta name="description" content="{name} - Professional {category} services in {city}.">
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="antialiased text-gray-900 bg-gray-50">
    <nav class="fixed w-full bg-white shadow-md z-50">
        <div class="max-w-7xl mx-auto px-4 py-4">
            <h1 class="text-2xl font-bold" style="color: {template['color']}">{name}</h1>
        </div>
    </nav>

    <section class="pt-32 pb-20 px-4" style="background: linear-gradient(135deg, {template['color']} 0%, #1E3A8A 100%); color: white;">
        <div class="max-w-4xl mx-auto text-center">
            <h2 class="text-5xl font-bold mb-6">{template['tagline']}</h2>
            <p class="text-xl mb-8">Professional {category} services in {city}</p>
            <a href="tel:{phone.replace(' ', '').replace('(', '').replace(')', '').replace('-', '')}"
               class="inline-block bg-white text-gray-900 px-8 py-4 rounded-lg font-bold text-lg hover:shadow-2xl transition">
                Call Now: {phone}
            </a>
        </div>
    </section>

    <section class="py-20 px-4">
        <div class="max-w-4xl mx-auto">
            <h3 class="text-3xl font-bold mb-8 text-center">Why Choose {name}?</h3>
            <div class="grid md:grid-cols-3 gap-6">
                <div class="bg-white rounded-lg shadow-md p-6">
                    <h4 class="font-bold text-xl mb-2">Local Experts</h4>
                    <p class="text-gray-600">Serving {city} and surrounding areas</p>
                </div>
                <div class="bg-white rounded-lg shadow-md p-6">
                    <h4 class="font-bold text-xl mb-2">Quality Guaranteed</h4>
                    <p class="text-gray-600">Satisfaction guaranteed on all work</p>
                </div>
                <div class="bg-white rounded-lg shadow-md p-6">
                    <h4 class="font-bold text-xl mb-2">Transparent Pricing</h4>
                    <p class="text-gray-600">Upfront estimates with no hidden fees</p>
                </div>
            </div>
        </div>
    </section>

    <section class="py-20 px-4 bg-white">
        <div class="max-w-4xl mx-auto text-center">
            <h3 class="text-3xl font-bold mb-4">Get Your Free Quote</h3>
            <p class="text-xl text-gray-600 mb-8">Contact us today to get started</p>
            <div class="space-y-4">
                <p class="text-lg"><strong>Phone:</strong> {phone}</p>
                <p class="text-lg"><strong>Email:</strong> {email}</p>
            </div>
        </div>
    </section>

    <footer class="bg-gray-900 text-white py-8 px-4 text-center">
        <p>&copy; {datetime.now().year} {name}. All rights reserved.</p>
    </footer>
</body>
</html>
"""
    return html


# ============================================================================
# EMAIL GENERATION LOGIC
# ============================================================================

def generate_cold_emails(prospects, output_dir, preview_base_url):
    """Generate cold email CSV ready for Instantly.ai import."""

    subject_lines = [
        "I built a new website for {business_name}",
        "{business_name} - I noticed something about your site",
        "Quick question about {business_name}'s website"
    ]

    def get_first_name(business_name):
        """Extract likely first name from business name or use generic."""
        # Try to extract owner name from patterns like "Joe's Plumbing"
        match = re.match(r"(\w+)'s", business_name)
        if match:
            return match.group(1)
        return "there"  # Generic fallback

    def format_issues(notes):
        """Format issues into bullet points."""
        if not notes or notes.startswith('Error'):
            return "your site could use a modern refresh"

        issues = notes.split('; ')
        if len(issues) == 1:
            return issues[0].lower()
        elif len(issues) == 2:
            return f"{issues[0].lower()} and {issues[1].lower()}"
        else:
            return f"{issues[0].lower()}, {issues[1].lower()}, and more"

    email_rows = []

    for prospect in prospects:
        business_name = prospect['business_name']
        first_name = get_first_name(business_name)
        email = prospect['email_if_found'].split(',')[0].strip()  # Take first email
        preview_link = f"{preview_base_url}/{slugify(business_name)}.html"
        issues = format_issues(prospect['notes'])

        # Day 1 email
        email_rows.append({
            'email': email,
            'first_name': first_name,
            'company_name': business_name,
            'custom_variable_1': preview_link,
            'custom_variable_2': issues,
            'custom_variable_3': prospect['phone_if_found'] or '',
            'custom_variable_4': prospect['city'],
            'custom_variable_5': str(prospect['site_score']),
            'sequence_step': '1',
            'subject': subject_lines[hash(business_name) % len(subject_lines)].format(business_name=business_name),
            'body': f"""Hi {first_name},

I was looking at {business_name}'s website and noticed a few things:

{issues.capitalize()} — which means you're likely losing customers who browse on their phone or search online.

I went ahead and built a preview of what a modern version could look like:

{preview_link}

If you like what you see, I can have the full site live for $500 — includes mobile optimization, SEO setup, hosting, and 30 days of support.

Want me to walk you through it?

Best,
[Your Name]"""
        })

        # Day 3 follow-up
        email_rows.append({
            'email': email,
            'first_name': first_name,
            'company_name': business_name,
            'custom_variable_1': preview_link,
            'custom_variable_2': issues,
            'custom_variable_3': prospect['phone_if_found'] or '',
            'custom_variable_4': prospect['city'],
            'custom_variable_5': str(prospect['site_score']),
            'sequence_step': '2',
            'subject': f"Re: {subject_lines[hash(business_name) % len(subject_lines)].format(business_name=business_name)}",
            'body': f"""Hi {first_name},

Just wanted to make sure you saw the site I built for {business_name}:

{preview_link}

Happy to jump on a quick call if you have questions.

Best,
[Your Name]"""
        })

        # Day 7 final follow-up
        email_rows.append({
            'email': email,
            'first_name': first_name,
            'company_name': business_name,
            'custom_variable_1': preview_link,
            'custom_variable_2': issues,
            'custom_variable_3': prospect['phone_if_found'] or '',
            'custom_variable_4': prospect['city'],
            'custom_variable_5': str(prospect['site_score']),
            'sequence_step': '3',
            'subject': f"Re: {subject_lines[hash(business_name) % len(subject_lines)].format(business_name=business_name)}",
            'body': f"""Hi {first_name},

Last follow-up — the preview site for {business_name} is still live at:

{preview_link}

I have 2 spots left this month for new builds. Let me know if you're interested.

Best,
[Your Name]"""
        })

    # Save to CSV
    csv_path = output_dir / 'cold_emails_instantly.csv'
    fieldnames = ['email', 'first_name', 'company_name', 'custom_variable_1', 'custom_variable_2',
                  'custom_variable_3', 'custom_variable_4', 'custom_variable_5', 'sequence_step', 'subject', 'body']

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(email_rows)

    return csv_path


# ============================================================================
# MAIN PIPELINE ORCHESTRATION
# ============================================================================

def print_header(text):
    """Print colored header."""
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'='*80}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}{text.center(80)}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}{'='*80}{Colors.ENDC}\n")

def print_step(step_num, text):
    """Print step header."""
    print(f"\n{Colors.BOLD}{Colors.CYAN}[STEP {step_num}] {text}{Colors.ENDC}")
    print(f"{Colors.CYAN}{'-'*80}{Colors.ENDC}")

def print_success(text):
    """Print success message."""
    print(f"{Colors.GREEN}✓ {text}{Colors.ENDC}")

def print_warning(text):
    """Print warning message."""
    print(f"{Colors.YELLOW}⚠ {text}{Colors.ENDC}")

def print_error(text):
    """Print error message."""
    print(f"{Colors.RED}✗ {text}{Colors.ENDC}")


def main():
    parser = argparse.ArgumentParser(
        description='Master pipeline: scrape → generate → host → outreach for local businesses',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('--urls-file', help='CSV file with URLs (columns: url, business_name, category, city)')
    parser.add_argument('--category', help='Business category for discovery (e.g., "dentist", "plumber")')
    parser.add_argument('--city', help='City for discovery (e.g., "Austin TX")')
    parser.add_argument('--output-dir', default='AUTOMATIONS/output/pipeline_run',
                       help='Output directory (default: AUTOMATIONS/output/pipeline_run)')
    parser.add_argument('--preview-url', default='http://localhost:8000',
                       help='Base URL for preview links in emails (default: http://localhost:8000)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Analyze and generate pages but do not create email CSV')
    parser.add_argument('--rate-limit', type=float, default=2.0,
                       help='Seconds between scrape requests (default: 2.0)')

    args = parser.parse_args()

    # Validate inputs
    if not args.urls_file and not (args.category and args.city):
        parser.error("Either --urls-file or both --category and --city are required")

    # Setup output directory structure
    output_dir = Path(args.output_dir)
    prospects_csv = output_dir / 'prospects.csv'
    pages_dir = output_dir / 'landing_pages'
    pages_dir.mkdir(parents=True, exist_ok=True)

    print_header("LOCAL BUSINESS WEBSITE REDESIGN PIPELINE")
    print(f"{Colors.BOLD}Timestamp:{Colors.ENDC} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{Colors.BOLD}Output Directory:{Colors.ENDC} {output_dir.absolute()}")
    print(f"{Colors.BOLD}Preview Base URL:{Colors.ENDC} {args.preview_url}")
    if args.dry_run:
        print_warning("DRY RUN MODE: Will not generate email CSV")

    # ========================================================================
    # STEP 1: SCRAPE & ANALYZE WEBSITES
    # ========================================================================
    print_step(1, "SCRAPE: Analyze business websites")

    scraper = BusinessWebsiteScraper(rate_limit=args.rate_limit)
    businesses = []

    if args.urls_file:
        print(f"Loading URLs from: {args.urls_file}")
        try:
            with open(args.urls_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    businesses.append({
                        'url': row.get('url', ''),
                        'business_name': row.get('business_name', ''),
                        'category': row.get('category', ''),
                        'city': row.get('city', '')
                    })
        except FileNotFoundError:
            print_error(f"File not found: {args.urls_file}")
            sys.exit(1)
    else:
        print(f"Searching for: {args.category} in {args.city}")
        print_warning("Note: Automated discovery requires Google Places API. Manual CSV recommended.")
        print("Create CSV with columns: url, business_name, category, city")
        sys.exit(1)

    if not businesses:
        print_error("No businesses to analyze.")
        sys.exit(1)

    print(f"Analyzing {len(businesses)} businesses (rate limit: {args.rate_limit}s between requests)...\n")

    results = []
    for biz in tqdm(businesses, desc="Scraping", unit="site"):
        result = scraper.scrape_business(
            url=biz['url'],
            business_name=biz.get('business_name'),
            category=biz.get('category'),
            city=biz.get('city')
        )
        results.append(result)

    # Sort by priority
    priority_order = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
    results.sort(key=lambda x: (priority_order[x['outreach_priority']], x['site_score']))

    # Save prospects CSV
    fieldnames = [
        'business_name', 'url', 'city', 'category', 'site_score',
        'mobile_ready', 'has_ssl', 'seo_score', 'ai_seo_score',
        'tech_stack', 'last_updated_estimate', 'appears_active',
        'email_if_found', 'phone_if_found', 'notes', 'outreach_priority'
    ]

    with open(prospects_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print_success(f"Prospects saved to: {prospects_csv}")

    # Print summary
    high_count = sum(1 for r in results if r['outreach_priority'] == 'HIGH')
    medium_count = sum(1 for r in results if r['outreach_priority'] == 'MEDIUM')
    low_count = sum(1 for r in results if r['outreach_priority'] == 'LOW')

    print(f"\n{Colors.BOLD}Analysis Summary:{Colors.ENDC}")
    print(f"  Total analyzed: {len(results)}")
    print(f"  {Colors.GREEN}HIGH priority (score < 40): {high_count}{Colors.ENDC}")
    print(f"  {Colors.YELLOW}MEDIUM priority (score 40-60): {medium_count}{Colors.ENDC}")
    print(f"  {Colors.BLUE}LOW priority (score > 60): {low_count}{Colors.ENDC}")

    # ========================================================================
    # STEP 2: GENERATE LANDING PAGES (HIGH PRIORITY ONLY)
    # ========================================================================
    print_step(2, "GENERATE: Create landing pages for HIGH priority prospects")

    high_priority_prospects = [r for r in results if r['outreach_priority'] == 'HIGH']

    if not high_priority_prospects:
        print_warning("No HIGH priority prospects found (score < 40 + active + email).")
        print("Consider lowering threshold or checking more sites.")

        # Ask if user wants to generate for MEDIUM instead
        print("\nMEDIUM priority prospects available:", medium_count)
        if medium_count > 0:
            print_warning("Run again with manual filtering to generate pages for MEDIUM priority.")
        sys.exit(0)

    print(f"Generating landing pages for {len(high_priority_prospects)} HIGH priority prospects...\n")

    generated_pages = []
    for prospect in tqdm(high_priority_prospects, desc="Generating", unit="page"):
        slug = slugify(prospect['business_name'])
        output_file = pages_dir / f"{slug}.html"

        html = generate_simple_landing_page(prospect)

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html)

        generated_pages.append({
            'business_name': prospect['business_name'],
            'slug': slug,
            'file': str(output_file),
            'url': f"{args.preview_url}/{slug}.html"
        })

    print_success(f"Generated {len(generated_pages)} landing pages in: {pages_dir}")

    # Generate index page
    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Generated Landing Pages - Index</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 p-8">
    <div class="max-w-6xl mx-auto">
        <h1 class="text-4xl font-bold mb-8">Generated Landing Pages ({len(generated_pages)})</h1>
        <div class="grid md:grid-cols-3 gap-4">
"""

    for page in generated_pages:
        index_html += f"""
            <a href="{page['slug']}.html" class="block bg-white rounded-lg shadow p-6 hover:shadow-xl transition">
                <h2 class="text-xl font-bold mb-2">{page['business_name']}</h2>
                <span class="text-blue-600">View Page →</span>
            </a>
"""

    index_html += """
        </div>
    </div>
</body>
</html>
"""

    index_path = pages_dir / 'index.html'
    with open(index_path, 'w') as f:
        f.write(index_html)

    print_success(f"Index page: file://{index_path.absolute()}")

    # ========================================================================
    # STEP 3: HOST (instructions only - manual deployment)
    # ========================================================================
    print_step(3, "HOST: Deploy pages for preview")

    print("Landing pages are ready in:")
    print(f"  {pages_dir.absolute()}")
    print("\nTo host locally:")
    print(f"  cd {pages_dir.absolute()}")
    print(f"  python3 -m http.server 8000")
    print(f"  Open: http://localhost:8000/index.html")
    print("\nTo host on Vercel/Netlify:")
    print(f"  1. Connect {pages_dir} to Vercel/Netlify")
    print(f"  2. Deploy (takes ~2 minutes)")
    print(f"  3. Update --preview-url with deployed URL")

    # ========================================================================
    # STEP 4: OUTREACH - Generate cold emails
    # ========================================================================
    if args.dry_run:
        print_step(4, "OUTREACH: Skipped (dry run mode)")
        print_warning("Re-run without --dry-run to generate email CSV")
    else:
        print_step(4, "OUTREACH: Generate personalized cold emails")

        if not high_priority_prospects:
            print_error("No HIGH priority prospects with email addresses found.")
            sys.exit(1)

        # Filter to only prospects with emails
        prospects_with_email = [p for p in high_priority_prospects if p['email_if_found']]

        if not prospects_with_email:
            print_error("No prospects have email addresses extracted.")
            print("Manual email lookup required.")
            sys.exit(1)

        print(f"Generating cold email sequences for {len(prospects_with_email)} prospects...")
        print(f"(3 emails per prospect = {len(prospects_with_email) * 3} total emails)")

        email_csv = generate_cold_emails(prospects_with_email, output_dir, args.preview_url)

        print_success(f"Email CSV ready: {email_csv}")
        print("\nUpload to Instantly.ai:")
        print("  1. Login to Instantly.ai")
        print("  2. Go to Campaigns → Import Leads")
        print("  3. Upload cold_emails_instantly.csv")
        print("  4. Map custom variables to email templates")
        print("  5. Launch campaign")

    # ========================================================================
    # FINAL SUMMARY
    # ========================================================================
    print_header("PIPELINE COMPLETE")

    print(f"{Colors.BOLD}Summary:{Colors.ENDC}")
    print(f"  Total scraped: {len(results)}")
    print(f"  HIGH priority: {high_count}")
    print(f"  Pages generated: {len(generated_pages)}")
    if not args.dry_run:
        prospects_with_email = [p for p in high_priority_prospects if p['email_if_found']]
        print(f"  Emails ready: {len(prospects_with_email) * 3} (3-step sequence)")

    print(f"\n{Colors.BOLD}Output Files:{Colors.ENDC}")
    print(f"  Prospects CSV: {prospects_csv}")
    print(f"  Landing pages: {pages_dir}")
    print(f"  Index page: file://{index_path.absolute()}")
    if not args.dry_run:
        print(f"  Email CSV: {output_dir / 'cold_emails_instantly.csv'}")

    print(f"\n{Colors.BOLD}Next Steps:{Colors.ENDC}")
    print(f"  1. Review landing pages: open {index_path.absolute()}")
    print(f"  2. Deploy pages (Vercel/Netlify or local server)")
    if not args.dry_run:
        print(f"  3. Upload email CSV to Instantly.ai")
        print(f"  4. Launch campaign and track replies")
    else:
        print(f"  3. Re-run without --dry-run to generate emails")

    print(f"\n{Colors.GREEN}{Colors.BOLD}Pipeline complete! Ready to close $500 deals.{Colors.ENDC}\n")


if __name__ == '__main__':
    main()
