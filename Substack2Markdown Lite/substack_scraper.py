import argparse
import json
import os
import re
from abc import ABC, abstractmethod
from html import escape
from time import sleep
from urllib.parse import urljoin, urlparse, urlunparse
from xml.etree import ElementTree as ET

import html2text
import markdown
import requests
from bs4 import BeautifulSoup
from config import EMAIL, PASSWORD
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions
from tqdm import tqdm
from webdriver_manager.microsoft import EdgeChromiumDriverManager

USE_PREMIUM: bool = False
BASE_SUBSTACK_URL: str = "https://www.astralcodexten.com/"

### DO NOT EDIT BELOW THIS LINE ###

BASE_MD_DIR: str = "md"
BASE_HTML_DIR: str = "html"
HTML_TEMPLATE: str = "author_template.html"
JSON_DATA_DIR: str = "data"
X_DOMAIN_PATTERN = re.compile(r'https?://(?:www\.)?x\.com(?=[/?#\s]|$)')
STRAY_MARKDOWN_DELIMITER_PATTERN = re.compile(r'(?<=\S)\s+(?:\*\*|__)\s+(?=\S)')
TWEET_EMBED_SELECTOR = ".twitter-embed"
TWEET_HANDLE_PATTERN = re.compile(r'(?:x|xcancel)\.com/([^/]+)/status')
OUTPUT_FORMATS = ("both", "html", "md")
EXTERNAL_LINK_REL = ("noopener", "noreferrer")


class SubstackLoginError(Exception):
    """Raised when the premium scraper cannot log in to Substack."""


def rewrite_x_links(node) -> None:
    """
    Rewrites x.com anchor hrefs to their xcancel.com mirror in place.
    """
    for anchor in node.select("a[href]"):
        anchor["href"] = X_DOMAIN_PATTERN.sub("https://xcancel.com", anchor["href"])


def is_offsite_href(href: str) -> bool:
    """
    True for hrefs that leave the export. Relative paths (links rewritten to a
    sibling .html), bare fragments, and mailto:/tel: links are all in-export.
    """
    parsed = urlparse(href)
    if parsed.scheme in {"http", "https"}:
        return True
    return not parsed.scheme and bool(parsed.netloc)


def set_link_targets(node) -> None:
    """
    Opens off-site links in a new tab and keeps in-export navigation in the current
    one, stripping any inherited target from the latter.

    Decided from the href alone rather than from a stored flag, so it stays correct
    when ``rewrite_generated_html_links`` later turns an absolute same-blog URL into
    a local filename -- that link must stop opening in a new tab.
    """
    for anchor in node.select("a[href]"):
        if is_offsite_href(anchor["href"]):
            anchor["target"] = "_blank"
            rel = anchor.get("rel") or []
            if isinstance(rel, str):
                rel = rel.split()
            anchor["rel"] = [*rel, *(t for t in EXTERNAL_LINK_REL if t not in rel)]
        elif "target" in anchor.attrs:
            del anchor["target"]


def clean_content_html(content_node) -> str:
    """
    Produces sanitized, directly-renderable HTML from a Substack content node.

    Unlike the markdown round-trip, this preserves rich embeds (e.g. the
    ``.twitter-embed`` tweet cards) as real HTML instead of flattening them into
    a broken markdown link.
    """
    node = BeautifulSoup(str(content_node), "html.parser")
    for tag in node.select("script, style, button"):
        tag.decompose()
    # Drop Substack's React hydration blobs (they also leak the original x.com URL).
    for tag in node.find_all(attrs={"data-attrs": True}):
        del tag["data-attrs"]
    for tag in node.find_all(attrs={"data-component-name": True}):
        del tag["data-component-name"]
    # Render footnote reference markers as real superscripts. Substack styles these
    # via its own CSS (which the export doesn't inherit); wrapping in <sup> makes them
    # superscript by default, independent of whether the stylesheet loads.
    for marker in node.select("a.footnote-anchor, a.footnote-number"):
        if marker.parent is not None and marker.parent.name == "sup":
            continue
        marker.wrap(node.new_tag("sup"))
    rewrite_x_links(node)
    set_link_targets(node)
    return str(node)


def _tweet_person(container, url: str = "") -> tuple[str, str]:
    """
    Extracts (display name, handle) from a tweet header/quoted-tweet container.
    """
    spans = container.find_all("span")
    name = spans[0].get_text(strip=True) if spans else ""
    handle = ""
    for span in spans:
        text = span.get_text(strip=True)
        if text.startswith("@"):
            handle = text[1:]
            break
    if not handle and url:
        match = TWEET_HANDLE_PATTERN.search(url)
        if match:
            handle = match.group(1)
    if not name:
        name = handle
    return name, handle


def _collapse_ws(text: str) -> str:
    return re.sub(r"[ \t]*\n[ \t]*", "\n", re.sub(r"[ \t]+", " ", text)).strip()


def _tweet_footer_parts(footer) -> tuple[str, str]:
    """
    Splits a tweet footer into ("timestamp - views" meta, "replies - reposts - likes" stats).
    """
    subdivs = [d for d in footer.find_all("div", recursive=False) if d.find("span")]
    if subdivs:
        meta = _collapse_ws(subdivs[0].get_text(" ", strip=True))
        stats = _collapse_ws(subdivs[1].get_text(" ", strip=True)) if len(subdivs) > 1 else ""
    else:
        meta = _collapse_ws(footer.get_text(" ", strip=True))
        stats = ""
    return meta, stats


def _tweet_blockquote(soup, embed, url: str):
    """
    Builds a clean <blockquote> representation of a tweet embed that survives
    conversion to markdown (single inline link, no image, no stray <hr>).
    """
    children = [c for c in embed.children if getattr(c, "name", None)]
    header, footer = children[0], children[-1]
    body = children[1:-1]

    name, handle = _tweet_person(header, url)
    blockquote = soup.new_tag("blockquote")

    heading = soup.new_tag("p")
    strong = soup.new_tag("strong")
    strong.string = name
    heading.append(strong)
    heading.append(f" (@{handle})")
    blockquote.append(heading)

    for part in body:
        if part.find("picture"):
            continue
        text = _collapse_ws(part.get_text("\n", strip=True))
        if text:
            paragraph = soup.new_tag("p")
            paragraph.string = text
            blockquote.append(paragraph)

    quoted = next((c for c in body if c.find("picture")), None)
    if quoted is not None:
        q_name, q_handle = _tweet_person(quoted)
        q_full = _collapse_ws(quoted.get_text(" ", strip=True))
        for prefix in (q_name, f"@{q_handle}"):
            if prefix and q_full.startswith(prefix):
                q_full = q_full[len(prefix):].lstrip()
        quote_p = soup.new_tag("p")
        emphasis = soup.new_tag("em")
        emphasis.string = "Quoting"
        quote_p.append(emphasis)
        quote_p.append(" ")
        q_strong = soup.new_tag("strong")
        q_strong.string = q_name
        quote_p.append(q_strong)
        quote_p.append(f" (@{q_handle}): {q_full}" if q_full else f" (@{q_handle}):")
        blockquote.append(quote_p)

    meta, stats = _tweet_footer_parts(footer)
    footer_p = soup.new_tag("p")
    if meta:
        footer_p.append(f"{meta} — ")
    link = soup.new_tag("a", href=url)
    link.string = stats or "View on X"
    footer_p.append(link)
    blockquote.append(footer_p)

    return blockquote


def simplify_tweets_for_markdown(content_node) -> str:
    """
    Replaces each tweet embed with a markdown-friendly blockquote, returning HTML
    ready for html2text. Falls back to a simple link if the embed structure is
    unexpected, so a Substack markup change degrades gracefully.
    """
    node = BeautifulSoup(str(content_node), "html.parser")
    for embed in node.select(TWEET_EMBED_SELECTOR):
        anchor = embed.find_parent("a")
        target = anchor if anchor is not None else embed
        raw_href = anchor.get("href", "") if anchor is not None else ""
        url = X_DOMAIN_PATTERN.sub("https://xcancel.com", raw_href)
        try:
            replacement = _tweet_blockquote(node, embed, url)
        # The traversal assumes Substack's header/body/footer shape; a markup change
        # surfaces as a missing child, a missing attribute, or a non-Tag node.
        except (AttributeError, IndexError, KeyError, TypeError):
            replacement = node.new_tag("blockquote")
            paragraph = node.new_tag("p")
            link = node.new_tag("a", href=url or "#")
            link.string = "View tweet"
            paragraph.append(link)
            replacement.append(paragraph)
        target.replace_with(replacement)
    return str(node)


def extract_main_part(url: str) -> str:
    parts = urlparse(url).netloc.split('.')
    return parts[1] if parts[0] == 'www' else parts[0]


def generate_html_file(author_name: str) -> None:
    """
    Generates a HTML file for the given author.
    """
    if not os.path.exists(BASE_HTML_DIR):
        os.makedirs(BASE_HTML_DIR)

    json_path = os.path.join(JSON_DATA_DIR, f'{author_name}.json')
    with open(json_path, 'r', encoding='utf-8') as file:
        essays_data = json.load(file)

    embedded_json_data = json.dumps(essays_data, ensure_ascii=False, indent=4)

    with open(HTML_TEMPLATE, 'r', encoding='utf-8') as file:
        html_template = file.read()

    html_with_data = html_template.replace('<!-- AUTHOR_NAME -->', author_name).replace(
        '<script type="application/json" id="essaysData"></script>',
        f'<script type="application/json" id="essaysData">{embedded_json_data}</script>'
    )
    html_with_author = html_with_data.replace('author_name', author_name)

    html_output_path = os.path.join(BASE_HTML_DIR, f'{author_name}.html')
    with open(html_output_path, 'w', encoding='utf-8') as file:
        file.write(html_with_author)


class BaseSubstackScraper(ABC):
    def __init__(self, base_substack_url: str, md_save_dir: str, html_save_dir: str, output_format: str = "html"):
        if output_format not in OUTPUT_FORMATS:
            raise ValueError(f"output_format must be one of {OUTPUT_FORMATS}")
        self.output_format: str = output_format

        if not base_substack_url.endswith("/"):
            base_substack_url += "/"
        self.base_substack_url: str = base_substack_url
        self.blog_host = self.normalize_hostname(base_substack_url)

        self.writer_name: str = extract_main_part(base_substack_url)
        md_save_dir: str = f"{md_save_dir}/{self.writer_name}"

        self.md_save_dir: str = md_save_dir
        self.html_save_dir: str = f"{html_save_dir}/{self.writer_name}"

        if not os.path.exists(md_save_dir):
            os.makedirs(md_save_dir)
            print(f"Created md directory {md_save_dir}")
        if not os.path.exists(self.html_save_dir):
            os.makedirs(self.html_save_dir)
            print(f"Created html directory {self.html_save_dir}")

        self.keywords: list[str] = ["about", "archive", "podcast"]
        self.post_urls: list[str] = self.get_all_post_urls()
        self.post_url_map = {
            self.normalize_post_url(url): self.get_filename_from_url(url, filetype=".html")
            for url in self.post_urls
        }

    def get_all_post_urls(self) -> list[str]:
        """
        Attempts to fetch URLs from sitemap.xml, falling back to feed.xml if necessary.
        """
        urls = self.fetch_urls_from_sitemap()
        if not urls:
            urls = self.fetch_urls_from_feed()
        return self.filter_urls(urls, self.keywords)

    def fetch_urls_from_sitemap(self) -> list[str]:
        """
        Fetches URLs from sitemap.xml.
        """
        sitemap_url = f"{self.base_substack_url}sitemap.xml"
        response = requests.get(sitemap_url)

        if not response.ok:
            print(f'Error fetching sitemap at {sitemap_url}: {response.status_code}')
            return []

        root = ET.fromstring(response.content)
        urls = [element.text for element in root.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
        return urls

    def fetch_urls_from_feed(self) -> list[str]:
        """
        Fetches URLs from feed.xml.
        """
        print('Falling back to feed.xml. This will only contain up to the 22 most recent posts.')
        feed_url = f"{self.base_substack_url}feed.xml"
        response = requests.get(feed_url)

        if not response.ok:
            print(f'Error fetching feed at {feed_url}: {response.status_code}')
            return []

        root = ET.fromstring(response.content)
        urls = []
        for item in root.findall('.//item'):
            link = item.find('link')
            if link is not None and link.text:
                urls.append(link.text)

        return urls

    @staticmethod
    def filter_urls(urls: list[str], keywords: list[str]) -> list[str]:
        """
        This method filters out URLs that contain certain keywords
        """
        return [url for url in urls if all(keyword not in url for keyword in keywords)]

    @staticmethod
    def html_to_md(html_content: str) -> str:
        """
        This method converts HTML to Markdown
        """
        if not isinstance(html_content, str):
            raise TypeError("html_content must be a string")
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.body_width = 0
        md_content = h.handle(html_content)
        return BaseSubstackScraper.normalize_markdown_content(md_content)

    @staticmethod
    def normalize_markdown_content(md_content: str) -> str:
        """
        Applies post-processing fixes to markdown emitted by html2text.
        """
        if not isinstance(md_content, str):
            raise TypeError("md_content must be a string")

        md_content = STRAY_MARKDOWN_DELIMITER_PATTERN.sub(' ', md_content)
        return X_DOMAIN_PATTERN.sub('https://xcancel.com', md_content)

    @staticmethod
    def normalize_hostname(url: str) -> str:
        hostname = urlparse(url).netloc.lower()
        return hostname.removeprefix("www.")

    @staticmethod
    def normalize_post_url(url: str) -> str:
        parsed = urlparse(url)
        normalized_path = parsed.path.rstrip("/") or "/"
        return urlunparse((
            parsed.scheme.lower(),
            BaseSubstackScraper.normalize_hostname(url),
            normalized_path,
            "",
            "",
            "",
        ))

    def get_local_html_path(self, filename: str) -> str:
        return os.path.join(self.html_save_dir, filename)

    def get_existing_local_html_filename(self, url: str) -> str | None:
        filename = self.post_url_map.get(self.normalize_post_url(url))
        if filename and os.path.exists(self.get_local_html_path(filename)):
            return filename
        return None

    def resolve_same_blog_link(self, href: str, source_url: str) -> str | None:
        if not href or href.startswith("#"):
            return None

        parsed_href = urlparse(href)
        if parsed_href.scheme in {"mailto", "javascript", "tel"}:
            return None

        resolved_url = urljoin(source_url, href)
        resolved_parsed = urlparse(resolved_url)
        if self.normalize_hostname(resolved_url) != self.blog_host:
            return None

        fragment = f"#{resolved_parsed.fragment}" if resolved_parsed.fragment else ""
        local_filename = self.get_existing_local_html_filename(resolved_url)
        if local_filename:
            return f"{local_filename}{fragment}"

        return resolved_url

    def rewrite_source_links(self, content_node: BeautifulSoup, source_url: str) -> None:
        for link in content_node.select("a[href]"):
            rewritten_href = self.resolve_same_blog_link(link["href"], source_url)
            if rewritten_href:
                link["href"] = rewritten_href

    def rewrite_generated_html_links(self, html_content: str) -> str:
        soup = BeautifulSoup(html_content, "html.parser")

        for link in soup.select("a[href]"):
            href = link["href"]
            parsed_href = urlparse(href)
            if parsed_href.scheme not in {"http", "https"}:
                continue

            local_filename = self.get_existing_local_html_filename(href)
            if local_filename:
                fragment = f"#{parsed_href.fragment}" if parsed_href.fragment else ""
                link["href"] = f"{local_filename}{fragment}"

        set_link_targets(soup)
        return str(soup)

    def refresh_existing_html_links(self) -> None:
        if not os.path.exists(self.html_save_dir):
            return

        for filename in os.listdir(self.html_save_dir):
            if not filename.endswith(".html"):
                continue

            filepath = self.get_local_html_path(filename)
            with open(filepath, 'r', encoding='utf-8') as file:
                html_content = file.read()

            rewritten_content = self.rewrite_generated_html_links(html_content)
            if rewritten_content != html_content:
                with open(filepath, 'w', encoding='utf-8') as file:
                    file.write(rewritten_content)

    @staticmethod
    def save_to_file(filepath: str, content: str) -> None:
        """
        This method saves content to a file. Can be used to save HTML or Markdown
        """
        if not isinstance(filepath, str):
            raise TypeError("filepath must be a string")

        if not isinstance(content, str):
            raise TypeError("content must be a string")

        if os.path.exists(filepath):
            print(f"File already exists: {filepath}")
            return

        with open(filepath, 'w', encoding='utf-8') as file:
            file.write(content)

    @staticmethod
    def md_to_html(md_content: str) -> str:
        """
        This method converts Markdown to HTML
        """
        return markdown.markdown(md_content, extensions=['extra'])


    def save_to_html_file(self, filepath: str, content: str, title: str = "") -> None:
        """
        This method saves HTML content to a file with a link to an external CSS file.

        ``title`` becomes the document <title> (browser tab, bookmark name, and what
        most readers use as the share title); it falls back to the filename stem when
        a post has no usable title.
        """
        if not isinstance(filepath, str):
            raise TypeError("filepath must be a string")

        if not isinstance(content, str):
            raise TypeError("content must be a string")

        if not isinstance(title, str):
            raise TypeError("title must be a string")

        html_dir = os.path.dirname(filepath)
        css_path = os.path.relpath("./assets/css/essay-styles.css", html_dir)
        css_path = css_path.replace("\\", "/")

        page_title = title.strip() or os.path.splitext(os.path.basename(filepath))[0]

        html_content = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>{escape(page_title)}</title>
                <link rel="stylesheet" href="{css_path}">
            </head>
            <body>
                <main class="markdown-content">
                {content}
                </main>
            </body>
            </html>
        """

        with open(filepath, 'w', encoding='utf-8') as file:
            file.write(html_content)

    @staticmethod
    def get_filename_from_url(url: str, filetype: str = ".md") -> str:
        """
        Gets the filename from the URL (the ending)
        """
        if not isinstance(url, str):
            raise TypeError("url must be a string")

        if not isinstance(filetype, str):
            raise TypeError("filetype must be a string")

        if not filetype.startswith("."):
            filetype = f".{filetype}"

        return url.split("/")[-1] + filetype

    @staticmethod
    def combine_metadata_and_content(title: str, subtitle: str, date: str, content) -> str:
        """
        Combines the title, subtitle, and content into a single string with Markdown format
        """
        if not isinstance(title, str):
            raise TypeError("title must be a string")

        if not isinstance(content, str):
            raise TypeError("content must be a string")

        metadata = f"# {title}\n\n"
        if subtitle:
            metadata += f"## {subtitle}\n\n"
        metadata += f"**{date}**\n\n"

        return metadata + content

    @staticmethod
    def combine_metadata_and_content_html(title: str, subtitle: str, date: str, html_body: str) -> str:
        """
        Prepends title/subtitle/date as HTML to an already-rendered content body.
        Used by the direct HTML path (no markdown round-trip).
        """
        if not isinstance(title, str):
            raise TypeError("title must be a string")

        if not isinstance(html_body, str):
            raise TypeError("html_body must be a string")

        parts = [f"<h1>{escape(title)}</h1>"]
        if subtitle:
            parts.append(f"<h3>{escape(subtitle)}</h3>")
        parts.append(f"<p><strong>{escape(date)}</strong></p>")
        parts.append(html_body)
        return "\n".join(parts)

    def extract_post_data(self, soup: BeautifulSoup, source_url: str) -> tuple[str, str, str, BeautifulSoup]:
        """
        Extracts post metadata and the sanitized content node.

        Returns the content node itself (after same-blog link rewriting) rather
        than a rendered string, so callers can build markdown and/or clean HTML
        from independent copies without a lossy round-trip.
        """
        ld_json_tag = soup.find("script", type="application/ld+json")
        date_published = "Date not found"

        if ld_json_tag:
            try:
                data = json.loads(ld_json_tag.string)
                if isinstance(data, dict) and "datePublished" in data:
                    date_published = data["datePublished"]
                elif isinstance(data, list):
                    for item in data:
                        if "datePublished" in item:
                            date_published = item["datePublished"]
                            break
            except json.JSONDecodeError:
                pass

        if "T" in date_published:
            date_published = date_published.split("T", 1)[0]

        date = date_published

        title = soup.select_one("h1.post-title, h2").text.strip()

        subtitle_element = soup.select_one("h3.subtitle")
        subtitle = subtitle_element.text.strip() if subtitle_element else ""

        content_node = soup.select_one("div.available-content")
        if content_node is None:
            raise ValueError("Post content not found")
        self.rewrite_source_links(content_node, source_url)
        return title, subtitle, date, content_node

    def build_markdown(self, title: str, subtitle: str, date: str, content_node) -> str:
        """
        Renders the post as markdown, with tweet embeds simplified to blockquotes.
        """
        md = self.html_to_md(simplify_tweets_for_markdown(content_node))
        return self.combine_metadata_and_content(title, subtitle, date, md)

    def build_html(self, title: str, subtitle: str, date: str, content_node) -> str:
        """
        Renders the post as clean HTML directly from the content node (no markdown
        round-trip), preserving rich embeds such as tweet cards.
        """
        html_body = clean_content_html(content_node)
        html_doc = self.combine_metadata_and_content_html(title, subtitle, date, html_body)
        return self.rewrite_generated_html_links(html_doc)

    @abstractmethod
    def get_url_soup(self, url: str) -> str:
        raise NotImplementedError

    def load_indexed_post_urls(self) -> set[str]:
        """
        Returns the normalized URLs already listed in the author's JSON index.

        That index is the only thing the browse page is built from, so a post whose
        files are on disk but whose entry is missing would otherwise be skipped by the
        "already exists" check on every future run and never regain a link.
        """
        json_path = os.path.join(JSON_DATA_DIR, f'{self.writer_name}.json')
        if not os.path.exists(json_path):
            return set()

        try:
            with open(json_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as e:
            print(f"Could not read existing index at {json_path}, rebuilding it: {e}")
            return set()

        if not isinstance(data, list):
            return set()

        return {
            self.normalize_post_url(item["url"])
            for item in data
            if isinstance(item, dict) and item.get("url")
        }

    @staticmethod
    def parse_saved_html_metadata(filepath: str) -> tuple[str, str, str] | None:
        """
        Recovers (title, subtitle, date) from a post page this scraper wrote.

        ``save_to_html_file`` emits the header as the first direct children of
        <main>: an <h1>, an optional <h3>, then <p><strong>date</strong></p>. Walking
        those in order (rather than selecting by tag name anywhere in the document)
        avoids mistaking an <h3> from the post body for the subtitle.
        """
        with open(filepath, 'r', encoding='utf-8') as file:
            soup = BeautifulSoup(file.read(), "html.parser")

        main = soup.select_one("main.markdown-content")
        if main is None:
            return None

        nodes = [child for child in main.children if getattr(child, "name", None)]
        if not nodes or nodes[0].name != "h1":
            return None

        title = nodes[0].get_text(strip=True)
        nodes = nodes[1:]

        subtitle = ""
        if nodes and nodes[0].name == "h3":
            subtitle = nodes[0].get_text(strip=True)
            nodes = nodes[1:]

        date = ""
        if nodes and nodes[0].name == "p":
            strong = nodes[0].find("strong")
            if strong is not None:
                date = strong.get_text(strip=True)

        return title, subtitle, date

    @staticmethod
    def parse_saved_markdown_metadata(filepath: str) -> tuple[str, str, str] | None:
        """
        Recovers (title, subtitle, date) from a post markdown file this scraper wrote.

        Mirrors the header ``combine_metadata_and_content`` produces: "# title", an
        optional "## subtitle", then "**date**".
        """
        blocks: list[str] = []
        with open(filepath, 'r', encoding='utf-8') as file:
            for raw_line in file:
                line = raw_line.strip()
                if line:
                    blocks.append(line)
                if len(blocks) == 3:
                    break

        if not blocks or not blocks[0].startswith("# "):
            return None

        title = blocks[0][2:].strip()
        blocks = blocks[1:]

        subtitle = ""
        if blocks and blocks[0].startswith("## "):
            subtitle = blocks[0][3:].strip()
            blocks = blocks[1:]

        date = ""
        if blocks:
            match = re.fullmatch(r"\*\*(.+)\*\*", blocks[0])
            if match:
                date = match.group(1)

        return title, subtitle, date

    def build_essay_record(
        self, title: str, subtitle: str, date: str, url: str, md_filepath: str, html_filepath: str
    ) -> dict:
        """
        Builds one browse-index entry, pointing at whichever files actually exist.
        """
        essay = {"title": title, "subtitle": subtitle, "date": date, "url": url}
        if self.output_format in ("md", "both") and os.path.exists(md_filepath):
            essay["md_link"] = md_filepath
        if self.output_format in ("html", "both") and os.path.exists(html_filepath):
            essay["html_link"] = html_filepath
        # The browse index links via html_link; fall back to the markdown
        # file when only markdown was produced.
        essay.setdefault("html_link", essay.get("md_link", md_filepath))
        return essay

    def recover_essay_from_disk(self, url: str, md_filepath: str, html_filepath: str) -> dict:
        """
        Rebuilds an index entry for a post already saved locally, without re-fetching it.
        """
        metadata = None
        if os.path.exists(html_filepath):
            metadata = self.parse_saved_html_metadata(html_filepath)
        if metadata is None and os.path.exists(md_filepath):
            metadata = self.parse_saved_markdown_metadata(md_filepath)
        if metadata is None:
            # A link with a slug-derived title still beats a page nothing points to.
            slug = os.path.splitext(os.path.basename(html_filepath))[0]
            metadata = (slug.replace("-", " ").strip() or slug, "", "")

        title, subtitle, date = metadata
        return self.build_essay_record(title, subtitle, date, url, md_filepath, html_filepath)

    def save_essays_data_to_json(self, essays_data: list) -> None:
        """
        Saves essays data to a JSON file for a specific author.
        """
        data_dir = os.path.join(JSON_DATA_DIR)
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)

        json_path = os.path.join(data_dir, f'{self.writer_name}.json')
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as file:
                existing_data = json.load(file)
        else:
            existing_data = []

        existing_dict = {item["url"]: item for item in existing_data if "url" in item}

        for new_item in essays_data:
            existing_dict[new_item["url"]] = new_item

        combined_data = list(existing_dict.values())
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(combined_data, f, ensure_ascii=False, indent=4)


    def scrape_posts(self, num_posts_to_scrape: int = 0) -> None:
        """
        Iterates over all posts and saves them as markdown and html files
        """
        essays_data = []
        count = 0
        want_md = self.output_format in ("md", "both")
        want_html = self.output_format in ("html", "both")
        indexed_urls = self.load_indexed_post_urls()
        total = num_posts_to_scrape if num_posts_to_scrape != 0 else len(self.post_urls)
        try:
            for url in tqdm(self.post_urls, total=total):
                try:
                    md_filename = self.get_filename_from_url(url, filetype=".md")
                    html_filename = self.get_filename_from_url(url, filetype=".html")
                    md_filepath = os.path.join(self.md_save_dir, md_filename)
                    html_filepath = os.path.join(self.html_save_dir, html_filename)

                    need_md = want_md and not os.path.exists(md_filepath)
                    need_html = want_html and not os.path.exists(html_filepath)

                    if need_md or need_html:
                        soup = self.get_url_soup(url)
                        if soup is None:
                            total += 1
                            continue
                        title, subtitle, date, content_node = self.extract_post_data(soup, url)

                        if need_md:
                            self.save_to_file(md_filepath, self.build_markdown(title, subtitle, date, content_node))

                        if need_html:
                            self.save_to_html_file(
                                html_filepath,
                                self.build_html(title, subtitle, date, content_node),
                                title=title,
                            )

                        essays_data.append(
                            self.build_essay_record(title, subtitle, date, url, md_filepath, html_filepath)
                        )
                    elif self.normalize_post_url(url) not in indexed_urls:
                        # The files are on disk but the browse index has no entry for
                        # them (e.g. an earlier run died before writing it). Without
                        # this the "already exists" branch would skip the post forever
                        # and the page would stay unreachable from the index.
                        print(f"Backfilling index entry: {html_filepath if want_html else md_filepath}")
                        essays_data.append(self.recover_essay_from_disk(url, md_filepath, html_filepath))
                    else:
                        print(f"File already exists: {md_filepath if want_md else html_filepath}")
                # Deliberately broad: one unreachable/malformed post must not abort a
                # scrape of several hundred. Network, parse, and I/O errors all land here.
                except Exception as e:  # noqa: BLE001
                    print(f"Error scraping post: {e}")
                count += 1
                if num_posts_to_scrape != 0 and count == num_posts_to_scrape:
                    break
        finally:
            # Runs even on Ctrl-C: pages already written must not be left out of the
            # index, since the "already exists" check would skip them next time.
            try:
                self.refresh_existing_html_links()
            except OSError as e:
                print(f"Error refreshing links in existing HTML files: {e}")
            self.save_essays_data_to_json(essays_data=essays_data)
            generate_html_file(author_name=self.writer_name)


class SubstackScraper(BaseSubstackScraper):
    def __init__(self, base_substack_url: str, md_save_dir: str, html_save_dir: str, output_format: str = "html"):
        super().__init__(base_substack_url, md_save_dir, html_save_dir, output_format)

    def get_url_soup(self, url: str) -> BeautifulSoup | None:
        """
        Gets soup from URL using requests
        """
        try:
            page = requests.get(url, headers=None)
            soup = BeautifulSoup(page.content, "html.parser")
            if soup.find("h2", class_="paywall-title"):
                print(f"Skipping premium article: {url}")
                return None
            return soup
        except Exception as e:
            raise ValueError(f"Error fetching page: {e}") from e


class PremiumSubstackScraper(BaseSubstackScraper):
    def __init__(
            self,
            base_substack_url: str,
            md_save_dir: str,
            html_save_dir: str,
            headless: bool = False,
            edge_path: str = '',
            edge_driver_path: str = '',
            user_agent: str = '',
            output_format: str = "html"
    ) -> None:
        super().__init__(base_substack_url, md_save_dir, html_save_dir, output_format)

        options = EdgeOptions()
        if headless:
            options.add_argument("--headless")
        if edge_path:
            options.binary_location = edge_path
        if user_agent:
            options.add_argument(f'user-agent={user_agent}')

        if edge_driver_path:
            service = Service(executable_path=edge_driver_path)
        else:
            service = Service(EdgeChromiumDriverManager().install())

        self.driver = webdriver.Edge(service=service, options=options)
        self.login()

    def login(self) -> None:
        """
        This method logs into Substack using Selenium
        """
        self.driver.get("https://substack.com/sign-in")
        sleep(3)

        signin_with_password = self.driver.find_element(
            By.XPATH, "//a[@class='login-option substack-login__login-option']"
        )
        signin_with_password.click()
        sleep(3)

        email = self.driver.find_element(By.NAME, "email")
        password = self.driver.find_element(By.NAME, "password")
        email.send_keys(EMAIL)
        password.send_keys(PASSWORD)

        submit = self.driver.find_element(By.XPATH, "//*[@id=\"substack-login\"]/div[2]/div[2]/form/button")
        submit.click()
        sleep(30)

        if self.is_login_failed():
            raise SubstackLoginError(
                "Warning: Login unsuccessful. Please check your email and password, or your account status.\n"
                "Use the non-premium scraper for the non-paid posts. \n"
                "If running headless, run non-headlessly to see if blocked by Captcha."
            )

    def is_login_failed(self) -> bool:
        """
        Check for the presence of the 'error-container' to indicate a failed login attempt.
        """
        error_container = self.driver.find_elements(By.ID, 'error-container')
        return len(error_container) > 0 and error_container[0].is_displayed()

    def get_url_soup(self, url: str) -> BeautifulSoup:
        """
        Gets soup from URL using logged in selenium driver
        """
        try:
            self.driver.get(url)
            return BeautifulSoup(self.driver.page_source, "html.parser")
        except Exception as e:
            raise ValueError(f"Error fetching page: {e}") from e


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape a Substack site.")
    parser.add_argument(
        "-u", "--url", type=str, help="The base URL of the Substack site to scrape."
    )
    parser.add_argument(
        "-d", "--directory", type=str, help="The directory to save scraped posts."
    )
    parser.add_argument(
        "-n",
        "--number",
        type=int,
        default=3,
        help="The number of posts to scrape. If 0 or not provided, all posts will be scraped.",
    )
    parser.add_argument(
        "-p",
        "--premium",
        action="store_true",
        help="Include -p in command to use the Premium Substack Scraper with selenium.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Include -h in command to run browser in headless mode when using the Premium Substack "
        "Scraper.",
    )
    parser.add_argument(
        "--edge-path",
        type=str,
        default="",
        help='Optional: The path to the Edge browser executable (i.e. "path_to_msedge.exe").',
    )
    parser.add_argument(
        "--edge-driver-path",
        type=str,
        default="",
        help='Optional: The path to the Edge WebDriver executable (i.e. "path_to_msedgedriver.exe").',
    )
    parser.add_argument(
        "--user-agent",
        type=str,
        default="",
        help="Optional: Specify a custom user agent for selenium browser automation. Useful for "
        "passing captcha in headless mode",
    )
    parser.add_argument(
        "--html-directory",
        type=str,
        help="The directory to save scraped posts as HTML files.",
    )
    parser.add_argument(
        "-f",
        "--format",
        dest="format",
        choices=list(OUTPUT_FORMATS),
        default="html",
        help="Which files to write: 'html' (default), 'md', or 'both'.",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.directory is None:
        args.directory = BASE_MD_DIR

    if args.html_directory is None:
        args.html_directory = BASE_HTML_DIR

    if args.url:
        if args.premium:
            scraper = PremiumSubstackScraper(
                args.url,
                headless=args.headless,
                md_save_dir=args.directory,
                html_save_dir=args.html_directory,
                output_format=args.format
            )
        else:
            scraper = SubstackScraper(
                args.url,
                md_save_dir=args.directory,
                html_save_dir=args.html_directory,
                output_format=args.format
            )

    else:
        if USE_PREMIUM:
            scraper = PremiumSubstackScraper(
                base_substack_url=BASE_SUBSTACK_URL,
                md_save_dir=args.directory,
                html_save_dir=args.html_directory,
                edge_path=args.edge_path,
                edge_driver_path=args.edge_driver_path,
                output_format=args.format
            )
        else:
            scraper = SubstackScraper(
                base_substack_url=BASE_SUBSTACK_URL,
                md_save_dir=args.directory,
                html_save_dir=args.html_directory,
                output_format=args.format
            )

    scraper.scrape_posts(num_posts_to_scrape=args.number)


if __name__ == "__main__":
    main()
