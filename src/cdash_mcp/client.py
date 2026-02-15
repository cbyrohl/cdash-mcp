"""CDash REST API client. [AI-Claude]"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx


class CDashError(Exception):
    """Base exception for CDash API errors."""


class CDashAuthError(CDashError):
    """Authentication failed (401/403)."""


class CDashNotFoundError(CDashError):
    """Resource not found (404)."""


class CDashConnectionError(CDashError):
    """Connection to CDash server failed."""


@dataclass
class CDashClient:
    """Async client for the CDash REST API.

    Args:
        base_url: CDash instance URL. Defaults to CDASH_URL env var or open.cdash.org.
        token: API token for auth. Defaults to CDASH_TOKEN env var.
    """

    base_url: str = field(
        default_factory=lambda: os.environ.get(
            "CDASH_URL", "https://open.cdash.org"
        ).rstrip("/")
    )
    token: str | None = field(
        default_factory=lambda: os.environ.get("CDASH_TOKEN")
    )
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    async def __aenter__(self) -> CDashClient:
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request to the CDash API and return parsed JSON."""
        assert self._client is not None, "Client not initialized. Use 'async with'."
        try:
            resp = await self._client.get(path, params=params)
        except httpx.ConnectError as e:
            raise CDashConnectionError(
                f"Cannot connect to {self.base_url}: {e}"
            ) from e
        except httpx.TimeoutException as e:
            raise CDashConnectionError(
                f"Request to {self.base_url} timed out: {e}"
            ) from e

        if resp.status_code in (401, 403):
            raise CDashAuthError(
                f"Authentication failed ({resp.status_code}). "
                "Check your CDASH_TOKEN environment variable."
            )
        if resp.status_code == 404:
            raise CDashNotFoundError(f"Resource not found: {path}")
        resp.raise_for_status()
        return resp.json()

    async def get_dashboard(
        self, project: str, date: str | None = None
    ) -> dict[str, Any]:
        """Get the main dashboard for a project.

        Args:
            project: CDash project name.
            date: Optional date string (YYYY-MM-DD).
        """
        params: dict[str, Any] = {"project": project}
        if date:
            params["date"] = date
        return await self._get("/api/v1/index.php", params)

    async def query_tests(
        self,
        project: str,
        date: str | None = None,
        test_name: str | None = None,
        status_filter: str = "not_passed",
    ) -> dict[str, Any]:
        """Query tests across all builds.

        Args:
            project: CDash project name.
            date: Optional date string (YYYY-MM-DD).
            test_name: Optional test name filter.
            status_filter: "not_passed" to get failing/notrun tests.
        """
        params: dict[str, Any] = {"project": project}
        if date:
            params["date"] = date

        # CDash filter system: filtercombine=and, field=status, compare=62 ("is not"), value=Passed
        if status_filter == "not_passed":
            params.update(
                {
                    "filtercount": "1",
                    "showfilters": "1",
                    "field1": "status",
                    "compare1": "62",
                    "value1": "Passed",
                }
            )

        if test_name:
            # Add test name filter
            filter_idx = int(params.get("filtercount", "0")) + 1
            params["filtercount"] = str(filter_idx)
            params["showfilters"] = "1"
            params["filtercombine"] = "and"
            params[f"field{filter_idx}"] = "testname"
            params[f"compare{filter_idx}"] = "63"  # "contains"
            params[f"value{filter_idx}"] = test_name

        return await self._get("/api/v1/queryTests.php", params)

    async def get_build_summary(self, build_id: int) -> dict[str, Any]:
        """Get detailed summary for a specific build.

        Args:
            build_id: The CDash build ID.
        """
        return await self._get("/api/v1/buildSummary.php", {"buildid": build_id})

    async def get_build_errors(
        self, build_id: int, warnings: bool = False
    ) -> dict[str, Any]:
        """Get build errors or warnings.

        Args:
            build_id: The CDash build ID.
            warnings: If True, fetch warnings instead of errors.
        """
        params: dict[str, Any] = {
            "buildid": build_id,
            "type": 1 if warnings else 0,
        }
        return await self._get("/api/v1/viewBuildError.php", params)

    async def get_build_tests(
        self, build_id: int, status_filter: str | None = None
    ) -> dict[str, Any]:
        """Get all tests for a specific build.

        Args:
            build_id: The CDash build ID.
            status_filter: Optional filter: "passed", "failed", "notrun".
        """
        params: dict[str, Any] = {"buildid": build_id}
        if status_filter:
            # CDash uses onlypassed/onlyfailed/onlynotrun in some views
            # but viewTest uses filtercount approach
            params.update(
                {
                    "filtercount": "1",
                    "showfilters": "1",
                    "field1": "status",
                    "compare1": "61",  # "is"
                    "value1": status_filter.capitalize(),
                }
            )
        return await self._get("/api/v1/viewTest.php", params)

    async def get_configure(self, build_id: int) -> dict[str, Any]:
        """Get CMake configure output for a build.

        Args:
            build_id: The CDash build ID.
        """
        return await self._get("/api/v1/viewConfigure.php", {"buildid": build_id})
