import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Set, Dict, List
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright
from datetime import datetime


class LinkCollector:
    def __init__(self, base_url_file: str = "base_url.txt", output_file: str = "urls.txt"):
        self.base_url_file = base_url_file
        self.output_file = output_file
        self.collected_links: Set[str] = set()
        self.links_by_source: Dict[str, List[str]] = {}
        self.max_concurrent_pages = 5
        self.max_concurrent_sites = 3

    async def collect_links(self):
        """Read URLs from base_url.txt and collect report links across multiple pages."""
        base_urls = self._read_base_urls()
        
        if not base_urls:
            print("No URLs found in base_url.txt.")
            return
        
        print(f"Number of sites to process: {len(base_urls)}")
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            browser_context = await browser.new_context()
            
            try:
                semaphore = asyncio.Semaphore(self.max_concurrent_sites)
                tasks = []
                
                for i, base_url in enumerate(base_urls):
                    task = self._process_site_with_semaphore(
                        semaphore, browser_context, base_url, i + 1, len(base_urls)
                    )
                    tasks.append(task)
                
                await asyncio.gather(*tasks)
                    
            except KeyboardInterrupt:
                print("\nUser terminated the program.")
            finally:
                await browser_context.close()
                await browser.close()
        
        self._save_urls_by_section()
        print(f"\nSaved to: {Path(self.output_file).absolute()}")
        print(f"Saved a total of {len(self.collected_links)} links to {self.output_file}.")
        self._summarize_links()

    async def _process_site_with_semaphore(self, semaphore, browser_context, base_url, site_num, total_sites):
        """Process a site while respecting a semaphore limit."""
        async with semaphore:
            await self._process_single_site(browser_context, base_url, site_num, total_sites)

    async def _process_single_site(self, browser_context, base_url, site_num, total_sites):
        """Process a single site."""
        print(f"\n[{site_num}/{total_sites}] Processing: {base_url}")
        
        initial_collected_links = set(self.collected_links)
        
        site_type = await self._detect_site_type(browser_context, base_url)
        print(f"[{site_num}] Site type: {site_type}")
        
        if site_type == 'load_more':
            links_count = await self._collect_load_more_site(browser_context, base_url, site_num)
        else:
            links_count = await self._collect_paginated_site_parallel(browser_context, base_url, site_num)
        
        new_links_for_this_site = []
        current_domain = urlparse(base_url).netloc.lower()
        
        for link in self.collected_links:
            if link not in initial_collected_links:
                link_domain = urlparse(link).netloc.lower()
                if self._is_link_from_domain(link, current_domain):
                    new_links_for_this_site.append(link)
        
        self.links_by_source[base_url] = new_links_for_this_site
        
        print(f"[{site_num}] Done: collected {len(new_links_for_this_site)} new links (from this site)")
        print(f"[{site_num}] Total links collected so far: {len(self.collected_links)}")

    def _is_link_from_domain(self, link: str, expected_domain: str) -> bool:
        """Check if a link is from the expected domain."""
        try:
            link_domain = urlparse(link).netloc.lower()
            expected_domain = expected_domain.lower()
            
            domain_mapping = {
                'asec.ahnlab.com': ['asec.ahnlab.com'],
                'www.fortinet.com': ['www.fortinet.com', 'fortinet.com'],
                'fortinet.com': ['www.fortinet.com', 'fortinet.com'],
                'research.checkpoint.com': ['research.checkpoint.com'],
                'checkpoint.com': ['research.checkpoint.com', 'checkpoint.com'],
                'thedfirreport.com': ['thedfirreport.com']
            }
            
            for domain_key, allowed_domains in domain_mapping.items():
                if domain_key in expected_domain:
                    return link_domain in allowed_domains
            
            return expected_domain == link_domain or expected_domain in link_domain or link_domain in expected_domain
            
        except Exception:
            return False

    def _read_base_urls(self) -> list:
        """Read URL list from base_url.txt."""
        try:
            with open(self.base_url_file, 'r', encoding='utf-8') as f:
                urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]
                return urls
        except FileNotFoundError:
            print(f"Could not find {self.base_url_file}.")
            return []

    async def _detect_site_type(self, browser_context, base_url: str) -> str:
        """Detect site type (load more vs pagination)."""
        page = await browser_context.new_page()
        try:
            await page.goto(base_url, wait_until='domcontentloaded', timeout=10000)
            await page.wait_for_timeout(1000)
            
            load_more_patterns = [
                'button:has-text("Load more")',
                'button:has-text("Load More")',
                'button:has-text("Load more stories")',
                'button.btn',
                'button[data-loadmore-target]',
                '.load-more-button',
                '#load-more'
            ]
            
            for pattern in load_more_patterns:
                try:
                    elements = await page.locator(pattern).all()
                    for element in elements:
                        if await element.is_visible():
                            text = await element.text_content() or ''
                            if any(keyword in text.lower() for keyword in ['load more', 'more stories', 'load']):
                                return 'load_more'
                except Exception:
                    continue
            
            return 'pagination'
            
        except Exception:
            return 'pagination'
        finally:
            await page.close()

    async def _collect_load_more_site(self, browser_context, base_url: str, site_num: int) -> int:
        """Handle 'Load More' style sites."""
        page = await browser_context.new_page()
        
        try:
            await page.goto(base_url, wait_until='domcontentloaded', timeout=10000)
            await page.wait_for_timeout(3000)
            
            initial_count = len(self.collected_links)
            click_count = 0
            max_clicks = 20
            consecutive_no_new = 0
            limit_click_count = 3

            # Collect links from the initial page
            initial_links = await self._extract_links(page)
            new_initial_links = initial_links - self.collected_links
            
            if new_initial_links:
                self.collected_links.update(new_initial_links)
                print(f"  [{site_num}] Initial page: +{len(new_initial_links)} links")
            else:
                print(f"  [{site_num}] No new links on the initial page")

            while click_count < max_clicks and consecutive_no_new < limit_click_count:
                button_clicked = False
                
                button_patterns = [
                    'button:has-text("Load more")',
                    'button:has-text("Load More")',
                    'button.btn',
                    'button[data-loadmore-target]',
                    '.load-more-button',
                    '#load-more'
                ]
                
                for pattern in button_patterns:
                    try:
                        buttons = await page.locator(pattern).all()
                        for button in buttons:
                            if await button.is_visible():
                                await button.click()
                                await page.wait_for_timeout(4000)
                                click_count += 1
                                button_clicked = True
                                break
                    except Exception:
                        continue
                    
                    if button_clicked:
                        break
                
                if not button_clicked:
                    break
                
                links = await self._extract_links(page)
                new_links = links - self.collected_links
                
                if new_links:
                    self.collected_links.update(new_links)
                    print(f"  [{site_num}] Click #{click_count}: +{len(new_links)} links")
                    consecutive_no_new = 0
                else:
                    consecutive_no_new += 1
                    print(f"  [{site_num}] Click #{click_count}: No new links ({consecutive_no_new}/{limit_click_count})")
            
            return len(self.collected_links) - initial_count
            
        except Exception as e:
            print(f"  [{site_num}] Error occurred: {str(e)}")
            return 0
        finally:
            await page.close()

    async def _collect_paginated_site_parallel(self, browser_context, base_url: str, site_num: int) -> int:
        """Handle paginated sites (with special handling for Check Point)."""
        initial_count = len(self.collected_links)
        
        # Check Point uses a slightly different pagination shape
        current_domain = urlparse(base_url).netloc.lower()
        
        # First page
        first_page_links = await self._extract_links_from_single_page(browser_context, base_url, 1, site_num)
        
        if not first_page_links:
            print(f"  [{site_num}] No links found on the first page.")
            return 0
        
        self.collected_links.update(first_page_links)
        print(f"  [{site_num}] Page 1: +{len(first_page_links)} links")
        
        page_num = 2
        max_pages = 100
        batch_size = self.max_concurrent_pages
        
        while page_num <= max_pages:
            batch_pages = list(range(page_num, min(page_num + batch_size, max_pages + 1)))
            
            semaphore = asyncio.Semaphore(self.max_concurrent_pages)
            tasks = []
            
            for page in batch_pages:
                if 'checkpoint.com' in current_domain:
                    # Check Point: /page/2/ under their listing
                    url = f"{base_url.rstrip('/')}/page/{page}/"
                else:
                    # Generic: /page/2 or /page/2/
                    url = f"{base_url}/page/{page}"
                
                task = self._extract_links_from_single_page_with_semaphore(
                    semaphore, browser_context, url, page, site_num
                )
                tasks.append(task)
            
            results = await asyncio.gather(*tasks)
            
            any_new_links = False
            for page_links in results:
                if page_links:
                    new_links = page_links - self.collected_links
                    if new_links:
                        self.collected_links.update(new_links)
                        page_idx = results.index(page_links)
                        actual_page = batch_pages[page_idx]
                        print(f"  [{site_num}] Page {actual_page}: +{len(new_links)} links")
                        any_new_links = True
            
            if not any_new_links:
                print(f"  [{site_num}] Batch pages {batch_pages[0]}-{batch_pages[-1]}: No new links — stopping")
                break
            
            page_num += batch_size
        
        return len(self.collected_links) - initial_count

    async def _extract_links_from_single_page_with_semaphore(self, semaphore, browser_context, url, page_num, site_num):
        """Extract links from a single page while respecting a semaphore limit."""
        async with semaphore:
            return await self._extract_links_from_single_page(browser_context, url, page_num, site_num)

    async def _extract_links_from_single_page(self, browser_context, url: str, page_num: int, site_num: int) -> set:
        """Extract links from a single page."""
        page = await browser_context.new_page()
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=8000)
            links = await self._extract_links(page)
            return links
        except Exception as e:
            print(f"  [{site_num}] Page {page_num} error: {str(e)}")
            return set()
        finally:
            await page.close()

    async def _extract_links(self, page) -> set:
        """Extract links from a page — selector sets tuned by site."""
        try:
            links = set()
            current_domain = urlparse(page.url).netloc.lower()
            
            if 'thedfirreport.com' in current_domain:
                selectors = [
                    'h2 a',
                    '.entry-title a'
                ]
            elif 'fortinet.com' in current_domain:
                selectors = [
                    'h2 a',
                    '.post-title a'
                ]
            elif 'checkpoint.com' in current_domain:
                # Check Point — collect broadly, then filter
                selectors = [
                    'a',  # collect all anchors, then filter
                ]
            elif 'asec.ahnlab.com' in current_domain:
                selectors = [
                    'h2 a',
                    'h3 a',
                    '.entry-title a',
                    'a[href*="/ko/"]'
                ]
            else:
                selectors = [
                    'h2 a',
                    'h3 a'
                ]
            
            for selector in selectors:
                try:
                    elements = await page.locator(selector).all()
                    for element in elements:
                        try:
                            href = await element.get_attribute('href')
                            if href:
                                absolute_url = urljoin(page.url, href)
                                links.add(absolute_url)
                        except Exception:
                            continue
                except Exception:
                    continue
            
            filtered_links = set()
            for link in links:
                if self._is_valid_universal_link(link, page.url, current_domain):
                    filtered_links.add(link)
            
            return filtered_links
            
        except Exception as e:
            print(f"Error while extracting links: {str(e)}")
            return set()

    def _is_actual_content_url(self, url_lower: str, domain: str) -> bool:
        """Heuristics to determine if the URL is likely an article/content page."""
        import re
        
        if 'thedfirreport.com' in domain:
            if re.search(r'/20\d{2}/\d{2}/\d{2}/.+', url_lower):
                return True
            return False
            
        elif 'fortinet.com' in domain:
            if '/blog/threat-research/' in url_lower:
                parts = url_lower.split('/')
                if len(parts) >= 5 and parts[-1] and len(parts[-1]) > 10:
                    return True
            return False
            
        elif 'checkpoint.com' in domain:
            # Check Point: research.checkpoint.com/2025/<title> shape, allow 2020s broadly
            if any(y in url_lower for y in ['2025', '2024', '2023', '2022', '2021']):
                return True
            return False
            
        elif 'asec.ahnlab.com' in domain:
            if re.search(r'/ko/\d+/?$', url_lower):
                return True
            return False
        
        return False

    def _is_valid_universal_link(self, url: str, current_url: str, current_domain: str = None) -> bool:
        """Generic link validation applicable across sites."""
        if not url or len(url) < 10:
            return False
        
        url_lower = url.lower()
        
        # Exclude social media
        social_media = [
            'facebook.com', 'twitter.com', 'x.com', 'linkedin.com', 
            'instagram.com', 'youtube.com', 'github.com'
        ]
        
        # Exclude file types
        file_extensions = [
            '.jpg', '.jpeg', '.png', '.gif', '.pdf', '.zip'
        ]
        
        # Relaxed exclusion patterns for Check Point
        if not current_domain:
            current_domain = urlparse(current_url).netloc.lower()
        
        if 'checkpoint.com' in current_domain:
            exclude_patterns = [
                'mailto:', 'javascript:', '#',
                'facebook.com', 'twitter.com', 'linkedin.com'
            ]
        else:
            # Generic exclusions
            exclude_patterns = [
                'mailto:', 'javascript:', '#',
                '/search', '/login', '/contact',
                '/page/', '?page=', '?p=',
                '/tag/', '/tags/',
                '/category/', '/categories/',
                '/services/', '/service/',
                '/products/', '/product/',
                '/solutions/', '/solution/',
                '/about/', '/about-us/',
                '/analysts/', '/testimonials/',
                '/detection-rules/', '/threat-intelligence/',
                '/dfir-labs/', '/case-artifacts/',
                '/archive/', '/archives/',
                '/transform'
            ]
        
        if any(social in url_lower for social in social_media):
            return False
        
        if any(ext in url_lower for ext in file_extensions):
            return False
        
        for pattern in exclude_patterns:
            if pattern in url_lower:
                return False
        
        # Exclude bare homepages
        import re
        if re.search(r'^https?://[^/]+/?$', url_lower):
            return False
        
        # Must look like an actual content URL
        if not self._is_actual_content_url(url_lower, current_domain):
            return False
        
        # Only allow same-domain (or subdomain/superdomain) links
        try:
            link_domain = urlparse(url).netloc.lower()
            if current_domain == link_domain or current_domain in link_domain or link_domain in current_domain:
                return True
            return False
        except Exception:
            return False

    def _save_urls_by_section(self):
        """Save collected URLs grouped by base_url to the output file."""
        try:
            existing_content = ""
            if Path(self.output_file).exists():
                with open(self.output_file, 'r', encoding='utf-8') as f:
                    existing_content = f.read()

            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            with open(self.output_file, 'w', encoding='utf-8') as f:
                f.write(f"# CTI Links Collection\n")
                f.write(f"# Last Updated: {current_time}\n")
                f.write(f"# Total Links: {len(self.collected_links)}\n\n")
                
                base_urls = list(self.links_by_source.keys())
                for base_url in reversed(base_urls):
                    links = self.links_by_source[base_url]
                    if links:
                        f.write(f"## {base_url}\n")
                        f.write(f"# Collected: {len(links)} links\n")
                        f.write(f"# Date: {current_time}\n\n")
                        
                        for link in sorted(links, reverse=True):
                            f.write(f"{link}\n")
                        f.write("\n")
                
                if existing_content.strip() and not existing_content.startswith("# CTI Links Collection"):
                    f.write("# =================== Previous Collections ===================\n\n")
                    f.write(existing_content)

        except Exception as e:
            print(f"Error while saving file: {str(e)}")

    def _summarize_links(self):
        """Print a summary of collected links by domain and base_url."""
        domain_count = defaultdict(int)
        
        print("\n=== This run summary (by base_url) ===")
        for base_url, links in self.links_by_source.items():
            if links:
                print(f"{base_url}: {len(links)} links")
                for link in links:
                    domain = urlparse(link).netloc
                    domain_count[domain] += 1

        print("\n=== Overall collected links summary (by domain) ===")
        for domain, count in sorted(domain_count.items()):
            print(f"  {domain}: {count} links")


async def main():
    collector = LinkCollector()
    await collector.collect_links()


if __name__ == "__main__":
    asyncio.run(main())