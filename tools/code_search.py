#!/usr/bin/env python3
"""
CodeSearch - Search GitHub and GitLab repositories for code.

Usage:
    python code_search.py --query "get_password_hash" --sources github gitlab
    python code_search.py --query "bcrypt" --language python --max-results 20
    python code_search.py --query "auth" --owner tensorflow --repo pytorch
"""

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class CodeMatch:
    """A single code match from a repository search."""
    repository: str = ""  # e.g. "tensorflow/tensorflow"
    path: str = ""
    name: str = ""
    url: str = ""
    language: str = ""
    line_number: int = 0
    value: str = ""
    snippet: str = ""
    source: str = ""  # "github" or "gitlab"

    def to_dict(self):
        return asdict(self)


@dataclass
class SearchResults:
    """Results from a single source search."""
    query: str
    source: str
    matches: list[CodeMatch] = field(default_factory=list)
    error: str = ""
    total_estimated: int = 0

    def to_dict(self):
        return {
            "query": self.query,
            "source": self.source,
            "matches": [m.to_dict() for m in self.matches],
            "error": self.error,
            "total_estimated": self.total_estimated,
        }


# GitHub API code search (requires auth: search/code endpoint needs token)
GITHUB_API = "https://api.github.com/search/code"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com"

# GitLab public API
GITLAB_API = "https://gitlab.com/api/v4/search"
GITLAB_PROJECTS_API = "https://gitlab.com/api/v4/projects"


def api_get(url: str, headers: dict | None = None, timeout: int = 15) -> dict | None:
    """Make a GET request and return JSON, or None on failure."""
    try:
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "SharedLLM-CodeSearch/1.0")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except urllib.error.HTTPError as e:
        print(f"  HTTP error {e.code}: {e.reason}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  API error: {e}", file=sys.stderr)
        return None


def text_get(url: str, headers: dict | None = None, timeout: int = 15) -> str | None:
    """Make a GET request and return raw text, or None on failure."""
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "SharedLLM-CodeSearch/1.0")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        print(f"  HTTP error {e.code}: {e.reason}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  Text fetch error: {e}", file=sys.stderr)
        return None


def _extract_commit_from_url(html_url: str) -> str | None:
    """Extract commit SHA from a GitHub blob URL.

    e.g. https://github.com/owner/repo/blob/COMMIT_SHA/path/to/file.py
    """
    m = re.search(r"/blob/([a-f0-9]{40})/", html_url)
    return m.group(1) if m else None


def search_github(query: str, language: str | None = None, owner: str | None = None,
                  repo: str | None = None, max_results: int = 50,
                  token: str | None = None) -> SearchResults:
    """Search GitHub code using the Search API."""
    results = SearchResults(query=query, source="github")

    # Build query string for GitHub search
    parts = [f"{query}"]
    if language:
        parts.append(f"language:{language}")
    if owner:
        parts.append(f"user:{owner}")
    elif repo:
        parts.append(f"repo:{repo}")

    encoded_query = urllib.parse.quote("+".join(parts))
    url = f"{GITHUB_API}?q={encoded_query}&per_page={min(max_results, 100)}&type=code"

    print(f"  GitHub: {url[:120]}...")
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    data = api_get(url, headers=headers)
    if not data or "items" not in data:
        results.error = "GitHub API returned no items"
        return results

    results.total_estimated = data.get("total_count", 0)

    for item in data["items"][:max_results]:
        path = item.get("path", "")
        owner_name = item.get("repository", {}).get("full_name", "")
        html_url = item.get("html_url", "")

        # Try to get line number from text_match.locations
        line_number = 0
        matched_value = ""
        text_matches = item.get("text_match", [])
        if text_matches:
            first_match = text_matches[0]
            locations = first_match.get("locations", [])
            if locations:
                line_number = locations[0].get("line", 0)
            match_texts = first_match.get("matches", [])
            if match_texts:
                matched_value = match_texts[0].get("text", "")

        # If no locations/matches in text_match, fetch raw file to find the line
        if not matched_value and not line_number:
            # Use commit SHA from html_url (search API returns blob sha, not commit sha)
            commit_sha = _extract_commit_from_url(html_url)
            if commit_sha:
                raw_url = f"{GITHUB_RAW_BASE}/{owner_name}/{commit_sha}/{path}"
                raw_headers = {}
                if token:
                    raw_headers["Authorization"] = f"token {token}"
                raw_data = text_get(raw_url, headers=raw_headers, timeout=10)
                if raw_data:
                    lines = raw_data.split("\n")
                    query_lower = query.lower()
                    for i, line in enumerate(lines):
                        if query_lower in line.lower():
                            line_number = i + 1
                            matched_value = line.strip()[:500]
                            break
                    if not line_number and lines:
                        line_number = 1
                        matched_value = lines[0].strip()[:500]

        match = CodeMatch(
            repository=owner_name,
            path=path,
            name=item.get("name", ""),
            url=html_url,
            language=item.get("language", ""),
            line_number=line_number,
            snippet=item.get("html_url", ""),
            source="github",
            value=matched_value.strip()[:500] if matched_value else "",
        )

        results.matches.append(match)

    return results


def search_gitlab(query: str, language: str | None = None, owner: str | None = None,
                  max_results: int = 50, token: str | None = None) -> SearchResults:
    """Search GitLab code using the Search API.

    The blob search API (/api/v4/search?scope=blobs) requires authentication.
    Falls back to the public projects search when no token is provided.
    """
    results = SearchResults(query=query, source="gitlab")

    # Try blob search first (requires auth)
    params = urllib.parse.urlencode({"scope": "blobs", "search": query, "per_page": min(max_results, 100)})
    url = f"{GITLAB_API}?{params}"

    print(f"  GitLab blob search: {url[:120]}...")

    data = None
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "SharedLLM-CodeSearch/1.0")
        if token:
            req.add_header("PRIVATE-TOKEN", token)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  GitLab blob search auth required (HTTP {e.code}), trying projects search...", file=sys.stderr)
        data = None

    # Fallback to public projects search
    if not data or (isinstance(data, dict) and "message" in data):
        project_params = urllib.parse.urlencode({"search": query, "per_page": min(max_results, 100)})
        project_url = f"{GITLAB_PROJECTS_API}?{project_params}"
        print(f"  GitLab projects search: {project_url[:120]}...")

        try:
            req = urllib.request.Request(project_url)
            req.add_header("User-Agent", "SharedLLM-CodeSearch/1.0")
            if token:
                req.add_header("PRIVATE-TOKEN", token)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            results.error = f"GitLab projects search error: {e}"
            return results

    if not isinstance(data, list):
        results.error = "GitLab API returned unexpected format"
        return results

    for item in data[:max_results]:
        # Handle both blob search results and project search results
        if "project" in item:
            # Blob search result
            project = item.get("project", {})
            path = item.get("path", "")
            line_content = item.get("line_content", "")
            line_number = item.get("line_number", 0)
            ref = item.get("ref", "main")
            repo_name = project.get("path_with_namespace", "")
            web_url = project.get("web_url", "") + "/-/blob/" + ref + "/" + path
            lang = item.get("language", "")
            file_name = project.get("name", "")
            is_project_fallback = False
        else:
            # Project search result (fallback when blob search requires auth)
            repo_name = item.get("path_with_namespace", "")
            path = ""
            line_content = ""
            line_number = 0
            web_url = item.get("web_url", "")
            lang = item.get("language", "")
            file_name = item.get("name", "")
            line_content = item.get("description", "")[:200] if item.get("description") else ""
            is_project_fallback = True

        match = CodeMatch(
            repository=repo_name,
            path=path,
            name=file_name,
            url=web_url,
            language=lang,
            line_number=line_number,
            value=line_content.strip()[:500] if line_content else "",
            source="gitlab",
        )
        match.is_project = is_project_fallback if "is_project_fallback" in dir() else False
        results.matches.append(match)

    return results


def format_results(results_list: list[SearchResults]) -> str:
    """Format search results as a readable string."""
    output = []

    for r in results_list:
        output.append(f"\n{'='*60}")
        output.append(f"QUERY: {r.query}")
        output.append(f"SOURCE: {r.source}")
        output.append(f"{'='*60}")

        if r.error:
            output.append(f"ERROR: {r.error}")
            continue

        if not r.matches:
            output.append("No results found.")
            continue

        output.append(f"Found {len(r.matches)} result(s) (est. {r.total_estimated:,} total)")
        output.append(f"{'Repository':<35} {'Language':<12} {'Line':>5} {'Path'}")
        output.append("-" * 100)

        for i, m in enumerate(r.matches[:30], 1):
            repo_short = m.repository.split("/")[-2] + "/" + m.repository.split("/")[-1] if "/" in m.repository else m.repository
            lang = m.language or "-"
            line = str(m.line_number) if m.line_number else "-"
            path_short = m.path[:40] if m.path else "-"
            output.append(f"{repo_short:<35} {lang:<12} {line:>5} {path_short}")

            # Show the actual code line
            if m.value:
                # Truncate very long lines
                display_value = m.value[:120]
                if len(m.value) > 120:
                    display_value += "..."
                output.append(f"  {display_value}")

            output.append("")

    return "\n".join(output)


def save_results(results_list: list[SearchResults], output_path: str):
    """Save results to JSON file."""
    data = {
        "results": [r.to_dict() for r in results_list],
        "summary": {
            "total_sources": len(results_list),
            "total_matches": sum(len(r.matches) for r in results_list),
        },
    }

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nResults saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Search GitHub/GitLab for code")
    parser.add_argument("--query", "-q", required=True, help="Code search query")
    parser.add_argument("--sources", "-s", nargs="+", default=["github", "gitlab"],
                        help="Sources to search: github, gitlab")
    parser.add_argument("--language", "-l", default=None, help="Filter by programming language")
    parser.add_argument("--owner", default=None, help="GitHub user/org or GitLab namespace")
    parser.add_argument("--repo", default=None, help="Specific repo (GitHub only, format: owner/repo)")
    parser.add_argument("--max-results", "-n", type=int, default=20, help="Max results per source (default: 20)")
    parser.add_argument("--output", "-o", help="Output JSON file path")
    parser.add_argument("--github-token", default=None, help="GitHub personal access token")
    parser.add_argument("--gitlab-token", default=None, help="GitLab private token")

    args = parser.parse_args()

    results = []
    for source in args.sources:
        source = source.lower()
        if source == "github":
            r = search_github(
                query=args.query,
                language=args.language,
                owner=args.owner,
                repo=args.repo,
                max_results=args.max_results,
                token=args.github_token,
            )
            results.append(r)
        elif source == "gitlab":
            r = search_gitlab(
                query=args.query,
                language=args.language,
                owner=args.owner,
                max_results=args.max_results,
                token=args.gitlab_token,
            )
            results.append(r)
        else:
            print(f"Warning: Unknown source '{source}', skipping", file=sys.stderr)

    print(format_results(results))

    if args.output:
        save_results(results, args.output)


if __name__ == "__main__":
    main()
