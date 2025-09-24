# reader2pdf
> Content-oriented "website loaded with reader mode --> PDF" tool.

Instead of screenshotting or printing whole webpages, 
`reader2pdf` extracts the article body using Mozilla's [`readability.js`](https://github.com/mozilla/readability) (vendored).
Rebuilds a clean HTML page (with a `<base>` tag so images resolve) and prints it to PDF via Playwright/Chromium. 

So, you can get clutter-free PDFs that keep the main text and images (lazy-loaded and <noscript> images are normalized).

## Preview
- The program is running.
<img width="2831" height="1246" alt="Screenshot from 2025-09-24 13-41-13" src="https://github.com/user-attachments/assets/c53ef5a8-04f4-46d9-9994-0984660b21f2" />

- Parsed PDF documents from the given URLs.
<img width="2989" height="2133" alt="image" src="https://github.com/user-attachments/assets/be31d0e4-50be-4ebb-af82-f8777feb0d76" />


## Features

- Uses [`readability.js`](https://github.com/mozilla/readability) to isolate the article content.
- Fixes relative URLs, lazy-loaded/srcset `<img>`/`<picture>`, and `<noscript>` images.
- Minimal, printable HTML and CSS.
- Concurrent fetching & rendering for faster bulk processing.
- Live TUI: tail of processed URLs (latest at bottom) + a single progress bar, so you get noticed about the progress!

## Mechanisms

1. Fetch page HTML (`DOMContentLoaded` + best-effort network idle).
2. Parse with vendored [`readability.js`](https://github.com/mozilla/readability) to get the title and the cleaned article HTML.
3. Build a minimal HTML document (adds `<base href="...">` so images/styles resolve).
4. Print to PDF via Playwright/Chromium.

## Setup
```sh
# 1) Create and enter a virtual env
uv venv
source .venv/bin/activate

# 2) Install the package (editable during development)
uv sync

# 3) One-time: install the Playwright browser
reader2pdf install-browser
```

## How to use
Prepare a text file with one URL per line (lines starting with # are ignored):
```text
# urls.txt
https://example.com/great-post
https://blog.example.org/research/writeup
...
```
And, run `reader2pdf` like below. (For detailed explanation, hit command `reader2pdf run --help`.)
```sh
reader2pdf run --url-file urls.txt --out-dir output
````
