import requests
from bs4 import BeautifulSoup
import time
import os
import re
from datetime import datetime
import html # For escaping content for HTML output
from urllib.parse import urljoin, urlparse
from collections import OrderedDict # To maintain order of topics

# --- Configuration ---
# Define websites and their specific parsing rules
WEBSITE_CONFIGS = {
    "The Guardian": {
        "base_urls": [ # Guardian sections are relatively stable and well-defined
            "https://www.theguardian.com/world",
            "https://www.theguardian.com/us-news/us-politics",
            "https://www.theguardian.com/uk-news",
            "https://www.theguardian.com/environment/climate-crisis",
            "https://www.theguardian.com/world/middleeast",
            "https://www.theguardian.com/world/ukraine",
            "https://www.theguardian.com/environment",
            "https://www.theguardian.com/science",
            "https://www.theguardian.com/global-development",
            "https://www.theguardian.com/football",
            "https://www.theguardian.com/technology",
            "https://www.theguardian.com/business",
            "https://www.theguardian.com/obituaries",
            "https://www.theguardian.com/international"
        ],
        "homepage": "https://www.theguardian.com/", # Used for dynamic discovery, if needed later
        "menu_selectors": [], # Not actively used for Guardian as base_urls are comprehensive
        "article_link_selectors": [
            {'name': 'a', 'class_': 'dcr-h52q4q'},
            {'name': 'a', 'attrs': {'data-link-name': lambda x: x and 'article' in x.lower()}},
            {'name': 'a', 'class_': 'u-faux-block-link__overlay'}
        ],
        "content_body_selectors": [
            {'name': 'div', 'class_': 'article-body-commercial-selector'},
            {'name': 'div', 'itemprop': 'articleBody'},
            {'name': 'div', 'class_': lambda x: x and ('dcr-article-body' in x or 'article-body' in x)},
            {'name': 'article'}
        ],
        "excluded_keywords_in_url": ["/commentisfree/", "/video/", "/gallery/", "/audio/", "/live/", "#", "/profile/"]
    },
    "Hespress": {
        "base_urls": [], # Will be dynamically populated
        "homepage": "https://www.hespress.com/",
        "menu_selectors": [
            # Selector for the main navigation menu
            {'ul_selector': {'id': 'menu-main_menu', 'class_': 'nav'},
             'link_selector': {'name': 'a', 'class_': 'nav-link'},
             'url_domain_check': 'www.hespress.com' # Ensure links are internal
            }
        ],
        "article_link_selectors": [
            {'name': 'a', 'class_': 'stretched-link'},
            {'name': 'a', 'attrs': {'rel': 'bookmark'}},
            {'name': 'a', 'class_': 'post-card__link'}
        ],
        "content_body_selectors": [
            {'name': 'div', 'class_': 'article-body'}
        ],
        "excluded_keywords_in_url": ["/video/", "/photo/", "/podcast/", "/live/", "#"]
    },
    "Al Jazeera": {
        "base_urls": [], # Will be dynamically populated
        "homepage": "https://www.aljazeera.com/",
        "menu_selectors": [
            # Selector for main header menu items
            {'ul_selector': {'class_': 'menu header-menu'},
             'link_selector': {'name': 'a'},
             'url_domain_check': 'www.aljazeera.com'
            },
            # Selector for items within the "More" submenu (if any, adjust as per current HTML)
            {'ul_selector': {'class_': 'menu menu__submenu'},
             'link_selector': {'name': 'a'},
             'url_domain_check': 'www.aljazeera.com'
            }
        ],
        "article_link_selectors": [
            {'name': 'a', 'class_': 'u-clickable-card__link'},
            {'name': 'a', 'class_': 'fte-article__title-link'}
        ],
        "content_body_selectors": [
            {'name': 'div', 'class_': 'l-col--8'},
            {'name': 'div', 'class_': 'article-body'}
        ],
        "excluded_keywords_in_url": ["/videos/", "/gallery/", "/programmes/", "/live/", "#", "/tag/"]
    }
}

MAIN_NEWS_LOG = "news_log.txt"
ARTICLES_RAW_TEXT_DIR = "scraped_articles_raw_text"
ARTICLE_PAGES_DIR = "article_pages"
ARTICLE_IMAGES_DIR = os.path.join(ARTICLE_PAGES_DIR, "images") # Subdirectory for downloaded images
HTML_DASHBOARD_FILE = "news_dashboard.html"

CHECK_INTERVAL_SECONDS = 300 # IMPORTANT: Set to 5 minutes to be polite to servers
ARTICLE_SCRAPE_DELAY_SECONDS = 5 # Delay between scraping individual articles

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# --- Global variables ---
seen_articles = set()
# Use OrderedDict to store topics to maintain insertion order (for consistent tab order)
discovered_topics = OrderedDict() # Key: topic_id, Value: Topic Name (Title Case)
SECTION_DISCOVERY_COMPLETED = False # Global variable initialization

# --- Utility Functions ---

def load_seen_articles(filename):
    """Loads previously logged article URLs into the 'seen_articles' set."""
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith("URL:"):
                    url = line.replace("URL:", "").strip()
                    seen_articles.add(url)
    print(f"Loaded {len(seen_articles)} previously seen article URLs from {filename}.")

def sanitize_filename(title, max_len=100):
    """Sanitizes a string to be used as a valid filename."""
    sanitized_title = re.sub(r'[^\w\s.-]', '', title).strip() # Allow hyphens and periods
    sanitized_title = re.sub(r'\s+', '_', sanitized_title)
    return sanitized_title[:max_len]

def log_new_article_metadata(site_name, topic, title, url, filename):
    """Appends new article metadata to the specified log file."""
    with open(filename, 'a', encoding='utf-8') as f:
        f.write(f"Site: {site_name}\nTopic: {topic}\nTitle: {title}\nURL: {url}\n\n")
    # print(f"Logged new article metadata from {site_name} (Topic: {topic}): {title}")

def download_image(img_url, base_url, image_dir):
    """Downloads an image and returns its local path relative to article_pages."""
    if not img_url:
        return None

    # Resolve relative URLs
    absolute_img_url = urljoin(base_url, img_url)
    parsed_img_url = urlparse(absolute_img_url)

    # Basic check for valid image URL structure
    if not (parsed_img_url.scheme in ['http', 'https'] and parsed_img_url.netloc):
        # print(f"Skipping invalid image URL: {img_url}")
        return None
    # Check for common image extensions
    if not any(ext in parsed_img_url.path.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg']):
        # print(f"Skipping non-image URL: {img_url}")
        return None

    os.makedirs(image_dir, exist_ok=True)

    # Create a unique filename for the image using its original name and a hash
    img_name_original = os.path.basename(parsed_img_url.path)
    if '.' in img_name_original:
        name_part, ext_part = img_name_original.rsplit('.', 1)
        # Add a hash to prevent name collisions if multiple images have same name from different paths
        img_name = f"{sanitize_filename(name_part)}_{abs(hash(absolute_img_url))}.{ext_part}"
    else: # No extension, just use a hash
        img_name = f"img_{abs(hash(absolute_img_url))}.png" # Default to png

    local_img_path = os.path.join(image_dir, img_name)
    
    # Check if image already exists locally to avoid re-downloading
    if os.path.exists(local_img_path):
        # print(f"Image already exists: {local_img_path}")
        return os.path.join(os.path.basename(image_dir), img_name) # Return relative path

    try:
        img_response = requests.get(absolute_img_url, headers=HEADERS, timeout=10)
        img_response.raise_for_status()

        with open(local_img_path, 'wb') as f:
            f.write(img_response.content)
        # print(f"Downloaded image: {img_name}")
        # Return path relative to ARTICLE_PAGES_DIR (e.g., "images/my_image.png")
        return os.path.join(os.path.basename(image_dir), img_name)
    except requests.exceptions.RequestException as e:
        print(f"Error downloading image {absolute_img_url}: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred while saving image {img_name}: {e}")
        return None

def extract_topic_from_url(site_name, url):
    """
    Extracts a primary topic/category from the article URL based on site patterns.
    Adds extracted topics to the global `discovered_topics` OrderedDict.
    """
    parsed_url = urlparse(url)
    path = parsed_url.path.strip('/')
    parts = path.split('/')
    
    topic = "General" # Default topic

    if site_name == "The Guardian":
        for i, part in enumerate(parts):
            if part and not re.match(r'^\d{4}$|^\d{2}$', part): # Avoid years/months
                # Prioritize known section names
                if part in ["world", "us-news", "uk-news", "environment", "science",
                            "global-development", "football", "technology", "business", "obituaries", "international"]:
                    topic = part.replace('-', ' ').title()
                    break
                elif i > 0 and parts[i-1] == "news" and not re.match(r'^\d+$', part): # e.g. /news/politics
                    topic = parts[i].replace('-', ' ').title()
                    break
                elif i == 0: # First part of path if it's a category
                    topic = parts[i].replace('-', ' ').title()
                    break
    elif site_name == "Hespress":
        if len(parts) > 0 and not re.match(r'^\d+\.html$', parts[0]) and parts[0]:
            topic = parts[0].replace('-', ' ').title()
        else:
            topic = "General" # Hespress homepage articles often just have ID.html
    elif site_name == "Al Jazeera":
        for i, part in enumerate(parts):
            if part and not re.match(r'^\d{4}$|^\d{2}$', part):
                if part == "news" and i + 1 < len(parts) and not re.match(r'^\d+$', parts[i+1]):
                    topic = parts[i+1].replace('-', ' ').title()
                    break
                elif part == "liveblog":
                    topic = "Liveblog" # Special case for Al Jazeera liveblogs
                    break
                else:
                    topic = parts[i].replace('-', ' ').title()
                    break
    
    # Add to global discovered topics (maintain insertion order and use consistent ID format)
    topic_id = topic.lower().replace(' ', '-')
    if topic_id not in discovered_topics:
        discovered_topics[topic_id] = topic # Key is ID, value is Title Case name
    
    return topic

def discover_sections(site_name, homepage_url, menu_selectors_list):
    """
    Dynamically discovers section URLs from the website's homepage navigation menu.
    Updates the WEBSITE_CONFIGS for the given site with the discovered URLs.
    Also populates the global `discovered_topics` set based on these sections.
    """
    print(f"Discovering sections for {site_name} from {homepage_url}...")
    discovered_urls = set()
    try:
        response = requests.get(homepage_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        for selector_config in menu_selectors_list:
            ul_selector_attrs = selector_config.get('ul_selector', {})
            link_selector_attrs = selector_config.get('link_selector', {})
            url_domain_check = selector_config.get('url_domain_check')

            menu_uls = soup.find_all('ul', ul_selector_attrs)

            for menu_ul in menu_uls:
                links = menu_ul.find_all('a', link_selector_attrs)
                
                for link in links:
                    href = link.get('href')
                    if href:
                        absolute_url = urljoin(homepage_url, href)
                        parsed_url = urlparse(absolute_url)

                        if not (parsed_url.scheme in ['http', 'https'] and parsed_url.netloc): continue
                        if parsed_url.fragment: continue # Skip anchor links like #top
                        if parsed_url.path.endswith(('.html', '.htm', '.php')): continue # Likely articles, not sections
                        if not parsed_url.path or parsed_url.path == '/': continue # Skip base homepage

                        if url_domain_check and url_domain_check not in parsed_url.netloc: continue

                        link_text = link.get_text(strip=True).lower()
                        # Exclude common non-section navigation links
                        if not link_text or link_text in ["trending", "live", "sign up", "more", "search", "login", "home", "back to dashboard", "skip links"]:
                            continue
                        if link.get('data-testid') == 'sub-menu-item' and link.get('aria-expanded') == 'false': # Al Jazeera specific button
                            continue
                        if link.get('class') and 'auth-btn' in link.get('class'): # Auth buttons
                            continue

                        # Exclude specific paths that are not main sections but might appear in menus
                        if any(keyword in absolute_url for keyword in ["/tag/", "/videos/", "/gallery/", "/programmes/", "/podcast/", "/about/", "/contact/", "/liveblog/"]):
                            continue
                        
                        discovered_urls.add(absolute_url)
                        extract_topic_from_url(site_name, absolute_url) # Populate discovered_topics using the section URL itself

    except requests.exceptions.RequestException as e:
        print(f"Error discovering sections for {site_name} from {homepage_url}: {e}")
    except Exception as e:
        print(f"An unexpected error occurred during section discovery for {site_name}: {e}")
    
    current_base_urls = set(WEBSITE_CONFIGS[site_name]["base_urls"])
    new_urls_added = 0
    for url in discovered_urls:
        if url not in current_base_urls:
            WEBSITE_CONFIGS[site_name]["base_urls"].append(url)
            new_urls_added += 1
    
    if new_urls_added > 0:
        print(f"Discovered and added {new_urls_added} new sections for {site_name}.")
    # else: # Commented out for less verbose output if no new sections found
        # print(f"No new sections discovered for {site_name}.")

def process_article_content(site_name, title, url, raw_text_dir, content_body_selectors):
    """
    Fetches, processes, and saves the full article content as raw text and
    formats it for embedding in HTML (including local image paths).
    Also generates a snippet.
    Returns (full_content_raw, full_content_html_formatted, snippet).
    """
    full_content_raw = ""
    full_content_html_formatted = ""
    snippet = "Content not available or could not be extracted."

    try:
        os.makedirs(raw_text_dir, exist_ok=True)
        raw_text_filename = os.path.join(raw_text_dir, f"{sanitize_filename(title)}_{site_name}.txt")

        # If the raw text file already exists, read from it to avoid re-scraping
        if os.path.exists(raw_text_filename):
            with open(raw_text_filename, 'r', encoding='utf-8') as f:
                content_lines = f.readlines()
                content_start_index = -1
                # Find the first blank line to separate metadata from content
                for i, line in enumerate(content_lines):
                    if line.strip() == "":
                        content_start_index = i + 1
                        break
                if content_start_index != -1:
                    full_content_raw = "".join(content_lines[content_start_index:])
                    # For HTML, assume content in raw file is already escaped text
                    # and wrap in paragraphs. If it contained HTML, it would be treated as literal text.
                    # This is a simplification; for existing files with complex HTML, re-parsing might be needed.
                    # Re-parsing is safer here to handle images, etc.
                    # This path will not reconstruct HTML with local image paths etc.
                    # So, if file exists, we still need to potentially re-process or store HTML alongside raw.
                    # For simplicity, for existing raw text files, we'll just extract snippet.
                    
                    snippet_lines = [p.strip() for p in full_content_raw.split('\n\n') if p.strip()][:3]
                    snippet = " ".join(snippet_lines).strip()
                    if len(snippet) > 200:
                        snippet = snippet[:200] + "..."
                    elif not snippet and raw_content_lines:
                        snippet = raw_content_lines[0].strip()
                    
                    # For a robust solution, if raw text exists, we'd need to either:
                    # 1. Store the full_content_html_formatted alongside raw_text
                    # 2. Re-parse the original HTML page to reconstruct formatted HTML (less efficient)
                    # For now, we'll just return raw and an empty formatted HTML, forcing full re-scrape if needed.
                    # Or, better, if raw exists, just proceed to scrape to get full_content_html_formatted
                    # A better approach for the dashboard would be to always re-generate the article's HTML page,
                    # but only re-scrape if raw_text doesn't exist.
                    pass # Let the code below handle scraping for full content and HTML generation

        print(f"Fetching full content for: {title} from {site_name} ({url})")
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        article_body = None
        for selector in content_body_selectors:
            # Handle different ways selectors might be defined
            if 'class_' in selector:
                article_body = soup.find(selector['name'], class_=selector['class_'])
            elif 'attrs' in selector:
                article_body = soup.find(selector['name'], **selector['attrs'])
            elif 'itemprop' in selector:
                article_body = soup.find(selector['name'], itemprop=selector['itemprop'])
            else: # Fallback to just tag name
                article_body = soup.find(selector['name'])

            if article_body:
                break

        raw_content_lines = []
        html_content_parts = []
        
        if article_body:
            current_list_tag = None # To manage ul/ol wrapping
            for element in article_body.find_all(['p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'img', 'blockquote', 'figure', 'ul', 'ol'], recursive=False):
                # Handle lists (ul, ol) by iterating their children directly
                if element.name in ['ul', 'ol']:
                    if current_list_tag and current_list_tag != element.name: # Close previous list if different type
                        html_content_parts.append(f"</{current_list_tag}>")
                    if not current_list_tag: # Open list if not already in one
                        html_content_parts.append(f"<{element.name}>")
                    current_list_tag = element.name # Set current list type
                    for li_item in element.find_all('li', recursive=False):
                        text = li_item.get_text(separator=' ', strip=True)
                        if text:
                            raw_content_lines.append(f"- {text}")
                            html_content_parts.append(f"<li>{html.escape(text)}</li>")
                    if current_list_tag == element.name: # Close the list after processing its items
                        html_content_parts.append(f"</{current_list_tag}>")
                        current_list_tag = None # Reset list tracker
                elif current_list_tag: # If we were in a list and this is not a list item, close the list
                    html_content_parts.append(f"</{current_list_tag}>")
                    current_list_tag = None

                # Process other non-list elements
                if element.name == 'p':
                    text = element.get_text(separator=' ', strip=True)
                    if text:
                        raw_content_lines.append(text)
                        html_content_parts.append(f"<p>{html.escape(text)}</p>")
                elif element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                    text = element.get_text(separator=' ', strip=True)
                    if text:
                        raw_content_lines.append(text)
                        html_content_parts.append(f"<{element.name}>{html.escape(text)}</{element.name}>")
                elif element.name == 'img' and element.get('src'):
                    img_src = element.get('src')
                    img_alt = element.get('alt', '')
                    local_img_path_relative = download_image(img_src, url, ARTICLE_IMAGES_DIR)
                    if local_img_path_relative:
                        raw_content_lines.append(f"Image: {img_src}") # Log original URL for raw text
                        # Use path relative to article_pages directory in the HTML
                        html_content_parts.append(f'<img src="{html.escape(local_img_path_relative)}" alt="{html.escape(img_alt)}" class="rounded-lg">')
                    else:
                        # Fallback to original URL if download fails, with an onerror placeholder
                        html_content_parts.append(f'<img src="{html.escape(img_src)}" alt="{html.escape(img_alt)}" class="rounded-lg" onerror="this.onerror=null;this.src=\'https://placehold.co/150x100/e2e8f0/7f8c8d?text=Image+Load+Error\';">')
                elif element.name == 'blockquote':
                    text = element.get_text(separator=' ', strip=True)
                    if text:
                        raw_content_lines.append(f"> {text}")
                        html_content_parts.append(f'<blockquote class="border-l-4 border-gray-300 pl-4 py-2 my-4 italic text-gray-700">{html.escape(text)}</blockquote>')
                elif element.name == 'figure':
                    img_in_figure = element.find('img')
                    if img_in_figure and img_in_figure.get('src'):
                        img_src = img_in_figure.get('src')
                        img_alt = img_in_figure.get('alt', '')
                        local_img_path_relative = download_image(img_src, url, ARTICLE_IMAGES_DIR)
                        if local_img_path_relative:
                            raw_content_lines.append(f"Image: {img_src}")
                            html_content_parts.append(f'<img src="{html.escape(local_img_path_relative)}" alt="{html.escape(img_alt)}" class="rounded-lg">')
                        else:
                            html_content_parts.append(f'<img src="{html.escape(img_src)}" alt="{html.escape(img_alt)}" class="rounded-lg" onerror="this.onerror=null;this.src=\'https://placehold.co/150x100/e2e8f0/7f8c8d?text=Image+Load+Error\';">')
                    figcaption = element.find('figcaption')
                    if figcaption:
                        raw_content_lines.append(f"Caption: {figcaption.get_text(strip=True)}")
                        html_content_parts.append(f'<p class="text-center text-sm text-gray-500 mt-2">{html.escape(figcaption.get_text(strip=True))}</p>')

            # Ensure any open list tags are closed at the very end
            if current_list_tag:
                html_content_parts.append(f"</{current_list_tag}>")

        else:
            print(f"Could not find specific article body for '{title}' from {site_name}. Attempting generic paragraph fallback.")
            for p in soup.find_all('p'):
                text = p.get_text(separator=' ', strip=True)
                if text:
                    raw_content_lines.append(text)
                    html_content_parts.append(f"<p>{html.escape(text)}</p>")

        full_content_raw = "\n\n".join(raw_content_lines)
        full_content_html_formatted = "".join(html_content_parts)

        if full_content_raw:
            with open(raw_text_filename, 'w', encoding='utf-8') as f:
                f.write(f"Site: {site_name}\n")
                f.write(f"Title: {title}\n")
                f.write(f"URL: {url}\n\n")
                f.write(full_content_raw)

            temp_snippet_lines = []
            for line in raw_content_lines:
                if line.strip():
                    temp_snippet_lines.append(line.strip())
                    if len(temp_snippet_lines) >= 3:
                        break
            snippet = " ".join(temp_snippet_lines).strip()
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            elif not snippet and raw_content_lines:
                snippet = raw_content_lines[0].strip()
        else:
            print(f"No significant content extracted for '{title}' from {site_name}. Raw text file not saved.")

    except requests.exceptions.RequestException as e:
        print(f"Error fetching article '{title}' from {site_name} ({url}): {e}")
    except Exception as e:
        print(f"An unexpected error occurred while processing article '{title}' from {site_name}: {e}")

    return full_content_raw, full_content_html_formatted, snippet

def generate_article_html_page(site_name, topic, title, original_url, full_content_html, local_article_filename):
    """
    Generates a standalone HTML file for an article, including its content, source, and topic.
    Returns the path to the generated file or None on error.
    """
    os.makedirs(ARTICLE_PAGES_DIR, exist_ok=True)
    file_path = os.path.join(ARTICLE_PAGES_DIR, local_article_filename)

    if os.path.exists(file_path):
        return file_path

    article_page_html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)} - {html.escape(site_name)}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body {{
            font-family: 'Inter', sans-serif;
            background-color: #f0f2f5;
        }}
        .article-content p {{
            margin-bottom: 1rem;
            line-height: 1.6;
        }}
        .article-content ul, .article-content ol {{
            margin-left: 1.5rem;
            list-style-type: disc;
            margin-bottom: 1rem;
        }}
        .article-content li {{
            margin-bottom: 0.5rem;
        }}
        .article-content h1, .article-content h2, .article-content h3, .article-content h4, .article-content h5, .article-content h6 {{
            font-weight: bold;
            margin-top: 1.5rem;
            margin-bottom: 0.75rem;
        }}
        .article-content h1 {{ font-size: 2.25rem; }}
        .article-content h2 {{ font-size: 1.875rem; }}
        .article-content h3 {{ font-size: 1.5rem; }}
        .article-content h4 {{ font-size: 1.25rem; }}
        .article-content blockquote {{
            border-left: 4px solid #cbd5e0; /* gray-300 */
            padding-left: 1rem;
            margin-top: 1rem;
            margin-bottom: 1rem;
            font-style: italic;
            color: #4a5568; /* gray-700 */
        }}
        .article-content img, .article-content video {{
            max-width: 100%;
            height: auto;
            display: block;
            margin: 1rem auto;
            border-radius: 0.5rem;
        }}
    </style>
</head>
<body class="p-4 sm:p-6 md:p-10">
    <div class="max-w-3xl mx-auto bg-white shadow-lg rounded-xl p-6 md:p-8">
        <div class="mb-6 flex flex-col sm:flex-row justify-between items-start sm:items-center">
            <a href="../{HTML_DASHBOARD_FILE}" class="inline-flex items-center text-blue-500 hover:text-blue-700 text-sm font-medium mb-2 sm:mb-0">
                <svg class="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"></path></svg>
                Back to Dashboard
            </a>
            <p class="text-sm text-gray-500">Source: {html.escape(site_name)} | Topic: {html.escape(topic)}</p>
        </div>
        
        <h1 class="text-3xl md:text-4xl font-bold text-gray-900 mb-4">{html.escape(title)}</h1>
        <p class="text-gray-600 text-sm mb-6">Original Article: <a href="{html.escape(original_url)}" target="_blank" rel="noopener noreferrer" class="text-blue-500 hover:underline">{html.escape(original_url)}</a></p>
        
        <div class="article-content text-gray-800 leading-relaxed">
            {full_content_html}
        </div>

        <div class="mt-8 pt-4 border-t border-gray-200 text-center">
            <p class="text-gray-500 text-sm">Content retrieved on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
    </div>
</body>
</html>
    """
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(article_page_html.strip())
        # print(f"Generated local HTML page: {file_path}")
        return file_path
    except Exception as e:
        print(f"Error generating HTML page for '{title}': {e}")
        return None

def update_html_dashboard(article_data, html_file):
    """
    Updates the main news dashboard HTML file by adding a new article entry
    to its specific topic tab AND the 'All News' tab.
    Dynamically creates new topic tabs and containers if they don't exist.
    """
    site_name = article_data['site']
    topic = article_data['topic']
    title = article_data['title']
    original_url = article_data['url']
    snippet = article_data['snippet']
    local_article_html_path = article_data['local_html_path']

    try:
        # Read existing dashboard HTML content
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        soup = BeautifulSoup(html_content, 'html.parser')
        
        topics_tab_buttons_container = soup.find('div', id='topics-tab-buttons')
        topics_news_content_container = soup.find('div', id='topics-news-content')

        if not topics_tab_buttons_container or not topics_news_content_container:
            print("Error: Dashboard HTML structure for dynamic tabs not found. Cannot update.")
            return

        # Ensure 'All News' placeholders are handled only once at initial generation
        # Any 'Loading topics...' or 'Please run script...' will be removed by create_initial_dashboard_html_template

        # Add new tab and content div for the topic if it doesn't exist
        topic_id = topic.lower().replace(' ', '-')
        
        # Check if tab button for this topic already exists
        existing_topic_tab_button = topics_tab_buttons_container.find('button', attrs={'data-tab-target': f"#{topic_id}-news-container"})
        
        if not existing_topic_tab_button:
            # Create new tab button
            new_tab_button_html = f"""
            <button class="tab-button py-3 px-6 text-sm font-medium transition-all duration-300 border-b-2 border-transparent text-gray-600 hover:text-gray-900 hover:border-blue-500 focus:outline-none flex-grow sm:flex-none text-center" data-tab-target="#{topic_id}-news-container">{html.escape(topic)}</button>
            """
            new_tab_button_soup = BeautifulSoup(new_tab_button_html, 'html.parser')
            # Insert the new topic button right after the 'All News' button to keep it ordered
            all_news_button = topics_tab_buttons_container.find('button', attrs={'data-tab-target': '#all-news-container'})
            if all_news_button:
                all_news_button.insert_after(new_tab_button_soup.button)
            else: # Fallback, should not happen if create_initial_dashboard_html_template works
                topics_tab_buttons_container.append(new_tab_button_soup.button)
            
            # Create new content container for this topic
            new_content_container_html = f"""
            <div id="{topic_id}-news-container" class="tab-content space-y-6 hidden">
                <div class="bg-blue-50 border border-blue-200 text-blue-800 px-4 py-3 rounded-md text-center shadow-md">
                    <p>No news articles found for '{html.escape(topic)}' yet. Please wait for the script to find some.</p>
                </div>
            </div>
            """
            new_content_container_soup = BeautifulSoup(new_content_container_html, 'html.parser')
            # Insert the new content container right after the 'All News' content container
            all_news_content_div = topics_news_content_container.find('div', id='all-news-container')
            if all_news_content_div:
                all_news_content_div.insert_after(new_content_container_soup.div)
            else: # Fallback
                topics_news_content_container.append(new_content_container_soup.div)
            print(f"Dynamically added new tab and container for topic: '{topic}'")

        # --- Create Article Card HTML ---
        article_card_html_template = f"""
        <div class="article-card bg-gray-50 p-4 md:p-6 rounded-lg shadow-sm border border-gray-200">
            <p class="text-xs text-gray-500 mb-1">{datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | {html.escape(site_name)}</p>
            <h2 class="text-xl font-semibold text-gray-800 mb-2">
                <a href="{html.escape(local_article_html_path)}" target="_blank" rel="noopener noreferrer" class="hover:text-blue-600 transition-colors duration-200">{html.escape(title)}</a>
            </h2>
            <p class="text-sm font-medium text-blue-700 mb-2">Topic: {html.escape(topic)}</p>
            <p class="text-gray-700 text-sm mb-3">{html.escape(snippet)}</p>
            <a href="{html.escape(local_article_html_path)}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center text-blue-500 hover:text-blue-700 text-sm font-medium">
                Read full article
                <svg class="w-3 h-3 ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 8l4-4m0 0l-4-4m4 4H3"></path></svg>
            </a>
        </div>
        """

        # --- Insert Article into Topic-Specific Tab ---
        target_topic_container = soup.find('div', id=f"{topic_id}-news-container")
        if target_topic_container:
            # Remove the "No news articles found" placeholder if it exists
            placeholder_content = target_topic_container.find('div', class_='bg-blue-50')
            if placeholder_content and "No news articles found for" in placeholder_content.find('p').text:
                placeholder_content.decompose()

            article_card_soup = BeautifulSoup(article_card_html_template, 'html.parser')
            target_topic_container.insert(0, article_card_soup.div) # Prepend newest article
        else:
            print(f"Warning: Topic container '{topic_id}-news-container' not found for article '{title}'.")

        # --- Insert Article into "All News" Tab (always exists) ---
        all_news_container = soup.find('div', id='all-news-container')
        if all_news_container:
            # Remove the "No news articles found yet" placeholder if it exists
            placeholder_content = all_news_container.find('div', class_='bg-blue-50')
            if placeholder_content and "No news articles found yet" in placeholder_content.find('p').text:
                placeholder_content.decompose()

            article_card_all_soup = BeautifulSoup(article_card_html_template, 'html.parser')
            all_news_container.insert(0, article_card_all_soup.div)
        else:
            print("Warning: 'All News' container not found. Articles not added to the general feed.")

        # Write the updated HTML back to the file
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(str(soup))
        print(f"Updated HTML dashboard with new article: {title}")

    except Exception as e:
        print(f"Error updating HTML dashboard: {e}")

def create_initial_dashboard_html_template(html_file):
    """
    Creates the base news_dashboard.html with the fixed 'All News' tab and
    placeholders for dynamic topic tabs/content. This is called only once.
    """
    initial_html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Live News Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body {
            font-family: 'Inter', sans-serif;
            background-color: #e2e8f0;
            line-height: 1.6;
            color: #334155;
        }
        ::-webkit-scrollbar {
            width: 8px;
        }
        ::-webkit-scrollbar-track {
            background: #cbd5e1;
            border-radius: 10px;
        }
        ::-webkit-scrollbar-thumb {
            background: #94a3b8;
            border-radius: 10px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: #64748b;
        }
        .tab-button.active {
            border-color: #3b82f6;
            color: #1d4ed8;
            font-weight: 600;
        }
        .article-card {
            transition: transform 0.2s ease-in-out, box-shadow 0.2s ease-in-out;
        }
        .article-card:hover {
            transform: translateY(-3px);
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
        }
    </style>
    <script>
        setTimeout(function(){
            location.reload();
        }, 30000);
    </script>
</head>
<body class="p-4 sm:p-6 md:p-10">
    <div class="max-w-5xl mx-auto bg-white shadow-xl rounded-xl p-6 md:p-8 border border-gray-200">
        <h1 class="text-3xl md:text-4xl font-extrabold text-gray-900 mb-4 text-center tracking-tight">
            🌐 Real-time Global News by Topic
        </h1>
        <p class="text-gray-600 text-center mb-8 max-w-2xl mx-auto">
            Your personalized feed for the latest headlines, categorized by specific topics from The Guardian, Hespress, and Al Jazeera.
        </p>

        <div id="topics-tab-buttons" class="flex justify-center border-b-2 border-gray-200 mb-8 px-4 flex-wrap gap-2">
            <button class="tab-button py-3 px-6 text-sm font-medium transition-all duration-300 border-b-2 border-transparent text-gray-600 hover:text-gray-900 hover:border-blue-500 focus:outline-none flex-grow sm:flex-none text-center" data-tab-target="#all-news-container">All News</button>
            </div>

        <div id="topics-news-content">
            <div id="all-news-container" class="tab-content space-y-6">
                <div class="bg-blue-50 border border-blue-200 text-blue-800 px-4 py-3 rounded-md text-center shadow-md">
                    <p>No news articles found yet. Please run the Python script to start populating this dashboard.</p>
                </div>
            </div>
            </div>
    </div>

    <script>
        document.addEventListener('DOMContentLoaded', () => {
            const setupTabs = () => {
                const tabs = document.querySelectorAll('.tab-button');
                const tabContents = document.querySelectorAll('.tab-content');

                if (tabs.length === 0) {
                    setTimeout(setupTabs, 1000); // Retry if tabs aren't loaded yet by Python
                    return;
                }

                const activateTab = (tabToActivate) => {
                    tabs.forEach(tab => {
                        tab.classList.remove('active', 'border-blue-600', 'text-blue-600');
                        tab.classList.add('border-transparent', 'text-gray-600');
                    });
                    tabContents.forEach(tabContent => {
                        tabContent.classList.add('hidden');
                    });

                    tabToActivate.classList.add('active', 'border-blue-600', 'text-blue-600');
                    tabToActivate.classList.remove('border-transparent', 'text-gray-600');
                    document.querySelector(tabToActivate.dataset.tabTarget).classList.remove('hidden');
                };

                tabs.forEach(tab => {
                    // Remove existing click listeners to avoid duplicates if setupTabs is called multiple times
                    if (tab.clickListener) { // Check if listener exists before removing
                        tab.removeEventListener('click', tab.clickListener); 
                    }
                    tab.clickListener = () => activateTab(tab); // Store new listener
                    tab.addEventListener('click', tab.clickListener); 
                });

                // Activate the first tab (e.g., 'All News') by default
                const defaultTabButton = tabs[0];
                if (defaultTabButton) {
                    activateTab(defaultTabButton);
                }
            };

            setupTabs(); // Call immediately
            setTimeout(setupTabs, 500); // Call again after 0.5 sec to catch dynamically loaded content
            setTimeout(setupTabs, 2000); // More aggressive retry for initial load of dynamic content
        });
    </script>
</body>
</html>
    """
    try:
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(initial_html_content.strip())
        print(f"Successfully created initial dashboard HTML template: {html_file}")
    except Exception as e:
        print(f"Error creating initial dashboard HTML template: {e}")

# --- Main monitoring function ---
def monitor_news_websites():
    """
    Main function to iterate through configured websites, find new articles,
    and process them. This function drives the entire scraping and dashboard update process.
    """
    global SECTION_DISCOVERY_COMPLETED # Use the global flag

    if not SECTION_DISCOVERY_COMPLETED:
        # Step 1: Initial setup of dashboard HTML if it doesn't exist
        # This creates the basic HTML structure with the 'All News' tab.
        if not os.path.exists(HTML_DASHBOARD_FILE):
             create_initial_dashboard_html_template(HTML_DASHBOARD_FILE)

        # Step 2: Discover sections and populate topics from homepages of all configured sites
        # This also populates the `discovered_topics` global OrderedDict.
        for site_name, config in WEBSITE_CONFIGS.items():
            if config.get("menu_selectors"):
                discover_sections(site_name, config["homepage"], config["menu_selectors"])
            # Ensure homepage is in base_urls if no other sections discovered for a site
            if not config["base_urls"] and config["homepage"] not in config["base_urls"]:
                 config["base_urls"].append(config["homepage"])
        
        # Ensure 'General' topic is always available if no specific topics were found from URLs
        if not discovered_topics:
            discovered_topics['general'] = "General"

        # Step 3: Update the initial dashboard HTML structure with all discovered topics
        # This will add the new topic tabs and their corresponding content containers.
        try:
            with open(HTML_DASHBOARD_FILE, 'r', encoding='utf-8') as f:
                current_dashboard_soup = BeautifulSoup(f.read(), 'html.parser')
            
            topics_tab_buttons_container = current_dashboard_soup.find('div', id='topics-tab-buttons')
            topics_news_content_container = current_dashboard_soup.find('div', id='topics-news-content')

            # Remove placeholder if it exists (only for initial load)
            placeholder_content = topics_news_content_container.find('div', class_='bg-blue-50')
            if placeholder_content and placeholder_content.find('p').text.strip() == "No news articles found yet. Please run the Python script to start populating this dashboard.":
                placeholder_content.decompose()

            # Add tabs and containers for all discovered topics (excluding 'All News' which is already fixed)
            for topic_id, topic_name in discovered_topics.items():
                if topic_id == "all-news": continue # Skip the fixed 'All News' tab

                # Add topic button if it doesn't exist
                if not topics_tab_buttons_container.find('button', attrs={'data-tab-target': f"#{topic_id}-news-container"}):
                    new_tab_button_html = f"""
                    <button class="tab-button py-3 px-6 text-sm font-medium transition-all duration-300 border-b-2 border-transparent text-gray-600 hover:text-gray-900 hover:border-blue-500 focus:outline-none flex-grow sm:flex-none text-center" data-tab-target="#{topic_id}-news-container">{html.escape(topic_name)}</button>
                    """
                    new_tab_button_soup = BeautifulSoup(new_tab_button_html, 'html.parser')
                    all_news_button = topics_tab_buttons_container.find('button', attrs={'data-tab-target': '#all-news-container'})
                    if all_news_button: # Insert after 'All News' button
                        all_news_button.insert_after(new_tab_button_soup.button)
                    else: # Fallback if 'All News' button not found
                        topics_tab_buttons_container.append(new_tab_button_soup.button)

                # Add topic content container if it doesn't exist
                if not topics_news_content_container.find('div', id=f"{topic_id}-news-container"):
                    new_content_container_html = f"""
                    <div id="{topic_id}-news-container" class="tab-content space-y-6 hidden">
                        <div class="bg-blue-50 border border-blue-200 text-blue-800 px-4 py-3 rounded-md text-center shadow-md">
                            <p>No news articles found for '{html.escape(topic_name)}' yet. Please wait for the script to find some.</p>
                        </div>
                    </div>
                    """
                    new_content_container_soup = BeautifulSoup(new_content_container_html, 'html.parser')
                    all_news_content_div = topics_news_content_container.find('div', id='all-news-container')
                    if all_news_content_div: # Insert after 'All News' content div
                        all_news_content_div.insert_after(new_content_container_soup.div)
                    else: # Fallback
                        topics_news_content_container.append(new_content_container_soup.div)

            with open(HTML_DASHBOARD_FILE, 'w', encoding='utf-8') as f:
                f.write(str(current_dashboard_soup))
            print("Initial dashboard HTML structure with topic tabs updated.")
        except Exception as e:
            print(f"Error during initial dashboard HTML structure update: {e}")

        SECTION_DISCOVERY_COMPLETED = True # Set flag after initial setup is done
        print("\n--- Initial Setup Completed. Starting Article Monitoring ---")

    # This loop runs continuously to find and process new articles
    for site_name, config in WEBSITE_CONFIGS.items():
        urls_to_check = config["base_urls"] if config["base_urls"] else [config["homepage"]]

        for base_url in urls_to_check:
            print(f"Checking for new news on {site_name} at {base_url}...")
            try:
                response = requests.get(base_url, headers=HEADERS, timeout=10)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, 'html.parser')

                newly_found_articles = []
                for selector in config["article_link_selectors"]:
                    links = []
                    if 'class_' in selector:
                        links = soup.find_all(selector['name'], class_=selector['class_'])
                    elif 'attrs' in selector:
                        links = soup.find_all(selector['name'], **selector['attrs'])
                    else:
                        links = soup.find_all(selector['name'])

                    for link in links:
                        href = link.get('href')
                        title = link.get_text(strip=True)

                        if href:
                            if not href.startswith(('http://', 'https://')):
                                href = urljoin(base_url, href)

                            clean_href = href.split('?')[0].split('#')[0]

                            is_excluded = False
                            for keyword in config["excluded_keywords_in_url"]:
                                if keyword in clean_href:
                                    is_excluded = True
                                    break
                            if is_excluded:
                                continue

                            # Site-specific URL pattern checks for stricter filtering
                            if site_name == "Hespress" and not re.match(r"https:\/\/www\.hespress\.com\/\d+\.html", clean_href):
                                continue
                            if site_name == "Al Jazeera" and not (
                                re.match(r"https:\/\/www\.aljazeera\.com\/[a-zA-Z0-9-]+\/[a-zA-Z0-9-]+\/?$", clean_href) or
                                re.match(r"https:\/\/www\.aljazeera\.com\/news\/liveblog\/", clean_href)
                            ):
                                continue

                            if clean_href not in seen_articles:
                                if title and title.strip():
                                    topic = extract_topic_from_url(site_name, clean_href) # Extract topic
                                    newly_found_articles.append({"title": title, "url": clean_href, "site": site_name, "topic": topic})
                                    seen_articles.add(clean_href) # Add to seen set immediately

                if newly_found_articles:
                    print(f"Found {len(newly_found_articles)} potential new articles from {site_name} at {base_url}!")
                    for article_data in newly_found_articles:
                        log_new_article_metadata(article_data['site'], article_data['topic'], article_data['title'], article_data['url'], MAIN_NEWS_LOG)
                        
                        full_content_raw, full_content_html, snippet = process_article_content(
                            article_data['site'],
                            article_data['title'],
                            article_data['url'],
                            ARTICLES_RAW_TEXT_DIR,
                            config["content_body_selectors"]
                        )

                        if full_content_raw:
                            local_article_filename = f"{sanitize_filename(article_data['title'])}_{sanitize_filename(article_data['site'], max_len=20)}.html"
                            
                            local_html_page_full_path = generate_article_html_page(
                                article_data['site'],
                                article_data['topic'], # Pass topic here for article page content
                                article_data['title'],
                                article_data['url'],
                                full_content_html,
                                local_article_filename
                            )
                            if local_html_page_full_path:
                                relative_local_path = os.path.join(ARTICLE_PAGES_DIR, os.path.basename(local_html_page_full_path))
                                article_data['snippet'] = snippet # Update snippet in dict for dashboard
                                article_data['local_html_path'] = relative_local_path # Add local path
                                
                                # Update the dashboard with the full article data
                                update_html_dashboard(article_data, HTML_DASHBOARD_FILE)
                        time.sleep(ARTICLE_SCRAPE_DELAY_SECONDS)
                else:
                    print(f"No new articles found from {site_name} at {base_url}.")

            except requests.exceptions.RequestException as e:
                print(f"Network error fetching {site_name} at {base_url}: {e}")
            except Exception as e:
                print(f"An unexpected error occurred while checking {site_name} at {base_url}: {e}")
            finally:
                time.sleep(1)

# --- Script execution ---
if __name__ == "__main__":
    # Load previously seen articles to avoid re-processing
    load_seen_articles(MAIN_NEWS_LOG)

    print(f"Starting news monitor for multiple websites. Checking all configured sections every {CHECK_INTERVAL_SECONDS} second(s).")
    print(f"Raw article texts will be saved in '{ARTICLES_RAW_TEXT_DIR}' directory.")
    print(f"Individual article HTML pages (with images) will be saved in '{ARTICLE_PAGES_DIR}' directory.")
    print(f"The main HTML dashboard will be updated in '{HTML_DASHBOARD_FILE}'.")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            monitor_news_websites()
            print(f"\nFinished checking all websites. Waiting {CHECK_INTERVAL_SECONDS} seconds before next cycle.")
            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nNews monitoring stopped by user.")
    finally:
        print("Exiting.")
