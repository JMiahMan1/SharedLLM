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


# GitHub API code search (free tier: 30 req/min unauthenticated, 30/hr)
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
    except Exception as e:
        print(f"  API error: {e}", file=sys.stderr)
        return None


def search_github(query: str, language: str | None = None, owner: str | None = None,
                  repo: str | None = None, max_results: int = 50) -> SearchResults:
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
    data = api_get(url)
    if not data or "items" not in data:
        results.error = "GitHub API returned no items"
        return results

    results.total_estimated = data.get("total_count", 0)

    for item in data["items"][:max_results]:
        path = item.get("path", "")
        # Fetch the file content to get the matched line and context
        match = CodeMatch(
            repository=item.get("repository", {}).get("full_name", ""),
            path=path,
            name=item.get("name", ""),
            url=item.get("html_url", ""),
            language=item.get("language", ""),
            line_number=item.get("text_match", [{}])[0].get("matches", [{}])[0].get("text", "").count("\n") + 1 if item.get("text_match") else item.get("score", 0),
            snippet=item.get("html_url", ""),
            source="github",
        )

        # Get the actual code line using the API's text matches or fetch file
        score_data = item.get("text_match", [{}])[0]
        if score_data and "matches" in score_data:
            match.value = score_data["matches"][0].get("text", "") if score_data["matches"] else ""
        else:
            # Try to fetch the specific line from raw content
            owner_name = item.get("repository", {}).get("full_name", "")
            raw_url = f"{GITHUB_RAW_BASE}/{owner_name}/{item['sha']}/{path}"
            raw_data = api_get(raw_url, timeout=10)
            if isinstance(raw_data, str):
                lines = raw_data.split("\n")
                if 0 < item.get("score", 1) <= len(lines):
                    match.value = lines[item.get("score", 1) - 1] if item.get("score", 1) <= len(lines) else lines[0]
            match.value = match.value.strip()[:500] if match.value else ""

        results.matches.append(match)

    return results


def search_gitlab(query: str, language: str | None = None, owner: str | None = None,
                  max_results: int = 50) -> SearchResults:
    """Search GitLab code using the Search API."""
    results = SearchResults(query=query, source="gitlab")

    params = urllib.parse.urlencode({"scope": "blobs", "search": query, "per_page": min(max_results, 100)})
    url = f"{GITLAB_API}?{params}"

    print(f"  GitLab: {url[:120]}...")

    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "SharedLLM-CodeSearch/1.0")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        results.error = f"GitLab API error: {e}"
        return results

    if not isinstance(data, list):
        results.error = "GitLab API returned unexpected format"
        return results

    for item in data[:max_results]:
        project = item.get("project", {})
        path = item.get("path", "")
        line_content = item.get("line_content", "")

        match = CodeMatch(
            repository=project.get("path_with_namespace", ""),
            path=path,
            name=project.get("name", ""),
            url=project.get("web_url", "") + "/-/blob/" + item.get("ref", "main") + "/" + path,
            language=item.get("language", ""),
            line_number=item.get("line_number", 0),
            value=line_content.strip()[:500] if line_content else "",
            snippet=item.get("execution_time", ""),
            source="gitlab",
        )
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
            )
            results.append(r)
        elif source == "gitlab":
            r = search_gitlab(
                query=args.query,
                language=args.language,
                owner=args.owner,
                max_results=args.max_results,
            )
            results.append(r)
        else:
            print(f"Warning: Unknown source '{source}', skipping", file=sys.stderr)

    print(format_results(results))

    if args.output:
        save_results(results, args.output)


if __name__ == "__main__":
    main()
