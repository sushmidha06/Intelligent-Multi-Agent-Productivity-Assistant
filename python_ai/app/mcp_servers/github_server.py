"""GitHub MCP server. Uses the user's PAT (fetched from the encrypted
connections store) to call api.github.com."""

from __future__ import annotations

import httpx

from ..node_client import NodeClient
from .base import McpError, McpServer

GH_BASE = "https://api.github.com"


class GithubMcpServer(McpServer):
    server_name = "github"

    def __init__(self, node: NodeClient):
        self.node = node
        self._cached_token: str | None = None
        self._cached_username: str | None = None
        super().__init__()

    # --- auth ---
    def _auth(self) -> tuple[str, str]:
        if self._cached_token and self._cached_username:
            return self._cached_token, self._cached_username
        conn = self.node.get_connection("github")
        if not conn:
            raise McpError(
                "GitHub is not connected for this user. Ask them to go to Integrations and connect GitHub."
            )
        token = (conn.get("secrets") or {}).get("token")
        username = (conn.get("metadata") or {}).get("username") or ""
        if not token:
            raise McpError("GitHub connection is missing a PAT.")
        self._cached_token, self._cached_username = token, username
        return token, username

    def _client(self) -> httpx.Client:
        token, _ = self._auth()
        return httpx.Client(
            base_url=GH_BASE,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=20.0,
        )

    def _register_tools(self) -> None:
        self._tool(
            "list_repos",
            "List repositories the connected GitHub user owns or collaborates on.",
            {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "sort": {"type": "string", "enum": ["updated", "pushed", "full_name"], "default": "updated"},
                },
                "additionalProperties": False,
            },
            self._list_repos,
        )
        self._tool(
            "list_open_prs",
            "List open pull requests authored by or assigned to the connected user across all repos.",
            {
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "enum": ["created", "assigned", "mentioned", "review-requested"], "default": "created"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
                },
                "additionalProperties": False,
            },
            self._list_open_prs,
        )
        self._tool(
            "list_recent_commits",
            "List recent commits in a given repository.",
            {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Format: owner/repo"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
                },
                "required": ["repo"],
                "additionalProperties": False,
            },
            self._list_recent_commits,
        )
        self._tool(
            "get_repo_activity",
            "Get weekly commit activity for a repo (52-week histogram of commits).",
            {
                "type": "object",
                "properties": {"repo": {"type": "string", "description": "Format: owner/repo"}},
                "required": ["repo"],
                "additionalProperties": False,
            },
            self._repo_activity,
        )
        self._tool(
            "create_issue",
            "Create a new issue in a GitHub repository.",
            {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Format: owner/repo"},
                    "title": {"type": "string"},
                    "body": {"type": "string", "description": "Markdown content for the issue description"},
                },
                "required": ["repo", "title"],
                "additionalProperties": False,
            },
            self._create_issue,
        )
        self._tool(
            "create_pull_request",
            "Create a new pull request in a GitHub repository.",
            {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Format: owner/repo"},
                    "title": {"type": "string"},
                    "head": {"type": "string", "description": "The name of the branch where your changes are implemented (e.g. 'feature-x')"},
                    "base": {"type": "string", "description": "The name of the branch you want the changes pulled into (e.g. 'main')"},
                    "body": {"type": "string", "description": "Markdown content for the PR description"},
                },
                "required": ["repo", "title", "head", "base"],
                "additionalProperties": False,
            },
            self._create_pr,
        )

    # --- handlers ---
    def _list_repos(self, limit: int = 10, sort: str = "updated") -> dict:
        with self._client() as c:
            r = c.get("/user/repos", params={"per_page": limit, "sort": sort})
            r.raise_for_status()
            data = r.json()
        repos = [
            {
                "full_name": it["full_name"],
                "private": it.get("private"),
                "description": it.get("description"),
                "stars": it.get("stargazers_count"),
                "open_issues": it.get("open_issues_count"),
                "pushed_at": it.get("pushed_at"),
                "language": it.get("language"),
            }
            for it in data
        ]
        return {"count": len(repos), "repos": repos}

    def _list_open_prs(self, filter: str = "created", limit: int = 10) -> dict:
        _, username = self._auth()
        q = f"is:pr is:open author:{username}" if filter == "created" else f"is:pr is:open {filter}:{username}"
        with self._client() as c:
            r = c.get("/search/issues", params={"q": q, "per_page": limit})
            r.raise_for_status()
            data = r.json()
        prs = [
            {
                "title": it.get("title"),
                "repo": it.get("repository_url", "").rsplit("/", 2)[-2:],
                "url": it.get("html_url"),
                "state": it.get("state"),
                "comments": it.get("comments"),
                "updated_at": it.get("updated_at"),
            }
            for it in data.get("items", [])
        ]
        return {"count": len(prs), "prs": prs}

    def _list_recent_commits(self, repo: str, limit: int = 10) -> dict:
        with self._client() as c:
            r = c.get(f"/repos/{repo}/commits", params={"per_page": limit})
            if r.status_code == 404:
                raise McpError(f"repo not found or no access: {repo}")
            r.raise_for_status()
            data = r.json()
        commits = [
            {
                "sha": it.get("sha", "")[:7],
                "message": (it.get("commit", {}).get("message", "") or "").splitlines()[0],
                "author": it.get("commit", {}).get("author", {}).get("name"),
                "date": it.get("commit", {}).get("author", {}).get("date"),
                "url": it.get("html_url"),
            }
            for it in data
        ]
        return {"repo": repo, "count": len(commits), "commits": commits}

    def _repo_activity(self, repo: str) -> dict:
        with self._client() as c:
            r = c.get(f"/repos/{repo}/stats/participation")
            if r.status_code == 404:
                raise McpError(f"repo not found or no access: {repo}")
            if r.status_code == 202:
                return {"repo": repo, "note": "GitHub is still computing stats; try again in a moment."}
            r.raise_for_status()
            data = r.json() or {}
        owner_weekly = data.get("owner", []) or []
        total = sum(owner_weekly) if owner_weekly else 0
        last_4 = owner_weekly[-4:] if owner_weekly else []
        return {
            "repo": repo,
            "total_owner_commits_52w": total,
            "owner_commits_last_4_weeks": last_4,
            "avg_owner_commits_per_week": round(total / 52, 1) if owner_weekly else 0,
        }

    def _create_issue(self, repo: str, title: str, body: str | None = None) -> dict:
        def do_create():
            with self._client() as c:
                r = c.post(f"/repos/{repo}/issues", json={"title": title, "body": body})
                r.raise_for_status()
                return r.json()

        return self._gate_with_approval(
            tool_name="github__create_issue",
            args={"repo": repo, "title": title, "body": body},
            summary=f"create issue in {repo}: {title}",
            do=do_create,
        )

    def _create_pr(self, repo: str, title: str, head: str, base: str, body: str | None = None) -> dict:
        def do_create():
            with self._client() as c:
                r = c.post(f"/repos/{repo}/pulls", json={"title": title, "head": head, "base": base, "body": body})
                r.raise_for_status()
                return r.json()

        return self._gate_with_approval(
            tool_name="github__create_pull_request",
            args={"repo": repo, "title": title, "head": head, "base": base, "body": body},
            summary=f"create PR in {repo}: {title}",
            do=do_create,
        )
