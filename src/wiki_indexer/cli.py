"""Headless command line interface, e.g. for scheduled indexing runs.

The PAT is read from an environment variable (default ``AZDO_PAT``) or, if that
is not set, from the Windows Credential Manager entry saved by the GUI.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import replace

from wiki_indexer import APP_NAME, __version__
from wiki_indexer.config.paths import AppPaths
from wiki_indexer.indexing.search import SearchMode, format_results
from wiki_indexer.services import AppServices
from wiki_indexer.utils.cancellation import CancellationToken, OperationCancelled
from wiki_indexer.utils.logging_setup import configure_logging
from wiki_indexer.utils.progress import Progress
from wiki_indexer.utils.secrets import SecretString


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wiki-indexer-cli", description=f"{APP_NAME} (command line)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--org", help="Organisation URL, e.g. https://dev.azure.com/myorg")
    parser.add_argument("--project", help="Project name")
    parser.add_argument("--pat-env", default="AZDO_PAT", help="Environment variable holding the PAT (default: AZDO_PAT)")
    parser.add_argument("--output", help="Output directory (overrides appsettings.json)")
    parser.add_argument("--threads", type=int, help="Number of download threads")
    parser.add_argument("--test", action="store_true", help="Only test the connection")
    parser.add_argument("--search", metavar="QUERY", help="Search the wikis after indexing")
    parser.add_argument("--mode", choices=[m.value for m in SearchMode], default=SearchMode.KEYWORD.value)
    parser.add_argument("--verbose", "-v", action="store_true", help="Log to the console")
    return parser


def _utf8_redirected_output() -> None:
    """Interactive consoles handle Unicode natively; pipes and files default to the ANSI code page on Windows,
    which cannot encode ✓/✗, so switch redirected streams to UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None, services: AppServices | None = None) -> int:
    _utf8_redirected_output()
    args = build_parser().parse_args(argv)
    paths = AppPaths.detect()
    configure_logging(paths.log_dir, console=args.verbose)
    services = services or AppServices.create(paths)

    settings = services.settings_store.load()
    org = args.org or settings.organization_url
    project = args.project or settings.project
    if not org or not project:
        print("error: --org and --project are required (or set them in appsettings.json)", file=sys.stderr)
        return 2
    settings = replace(
        settings,
        output_directory=args.output or settings.output_directory,
        thread_count=args.threads or settings.thread_count,
    ).validated()

    env_value = os.environ.get(args.pat_env)
    pat = SecretString(env_value) if env_value else services.stored_pat(org)
    if not pat:
        print(f"error: no PAT in ${args.pat_env} and none stored for {org}", file=sys.stderr)
        return 2

    try:
        result = services.test_connection(org, project, pat, settings)
        print(result.summary())
        if not result.success:
            return 1
        if args.test:
            return 0

        def show(p: Progress) -> None:
            print(f"\r{p.stage}: {p.current}/{p.total} {p.message}".ljust(70), end="", flush=True)

        token = CancellationToken()
        try:
            pipeline_result = services.run_pipeline(
                org, project, pat, settings, services.keyword_store.load(),
                wikis=result.wikis, token=token, progress=show,
            )
        except KeyboardInterrupt:
            token.cancel()
            raise OperationCancelled() from None
        print()
        crawl = pipeline_result.crawl
        print(f"Indexed {crawl.page_count} pages from {len(crawl.trees)} wiki(s) in {pipeline_result.elapsed_seconds}s")
        if crawl.failed_pages:
            print(f"{len(crawl.failed_pages)} page(s) failed to load; see summary.md")
        if pipeline_result.exported_files:
            print(f"Output: {pipeline_result.output_dir}")
        if args.search:
            hits = pipeline_result.search_engine().search(args.search, args.mode)
            print()
            print(format_results(args.search, hits))
        return 0
    except OperationCancelled:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as exc:  # report, log and exit non-zero
        logging.getLogger(__name__).exception("CLI run failed")
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    finally:
        pat.wipe()


if __name__ == "__main__":
    raise SystemExit(main())
